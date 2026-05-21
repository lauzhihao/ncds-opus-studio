import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { access, mkdir, readFile, unlink, writeFile } from 'node:fs/promises';
import {
  DEFAULT_REWRITE_PROFILE_ID,
  getRewriteProfile,
  normalizeRewriteProfileId,
} from './rewrite_profiles.mjs';

const LOCAL_GEMINI_PROVIDER = 'local-gemini';
const LOCAL_GEMINI_MODEL_REF = 'local-gemini/g.sh';
const DEFAULT_LOCAL_GEMINI_CLI_PATH = path.join(os.homedir(), '.gemini', 'g.sh');
const DEFAULT_CODEX_CLI_PATH = '/opt/homebrew/bin/codex';

const REWRITE_MODEL_CANDIDATES = [
  {
    id: 'gemini_local',
    modelRef: LOCAL_GEMINI_MODEL_REF,
    label: 'Local Gemini via g.sh',
  },
  {
    id: 'gpt5',
    modelRef: 'openai-codex/gpt-5.5',
    label: 'GPT-5.5 via OpenAI Codex',
  },
];

export function getDefaultModelConfigPath() {
  return path.join(os.homedir(), '.openclaw', 'openclaw.json');
}

export function getDefaultGeminiCliPath() {
  return DEFAULT_LOCAL_GEMINI_CLI_PATH;
}

export function getDefaultCodexCliPath() {
  return DEFAULT_CODEX_CLI_PATH;
}

export function buildDraftRecord({
  modelId,
  modelLabel,
  status,
  path: draftPath,
  durationMs = null,
  error = null,
  reason = null,
  inputTokens = null,
  outputTokens = null,
}) {
  return {
    modelId,
    modelLabel,
    status,
    path: draftPath,
    durationMs,
    error,
    reason,
    inputTokens,
    outputTokens,
  };
}

async function loadModelConfig(modelConfigPath = getDefaultModelConfigPath()) {
  try {
    const raw = await readFile(modelConfigPath, 'utf8');
    return JSON.parse(raw);
  } catch (error) {
    if (error && error.code === 'ENOENT') {
      return {};
    }

    throw error;
  }
}

function getProviderConfig(config, provider) {
  return config?.models?.providers?.[provider] ?? null;
}

function expandHomePath(value) {
  if (typeof value !== 'string' || !value.trim()) {
    return null;
  }
  const trimmed = value.trim();
  if (trimmed === '~') {
    return os.homedir();
  }
  if (trimmed.startsWith('~/')) {
    return path.join(os.homedir(), trimmed.slice(2));
  }
  return trimmed;
}

function getLocalGeminiCliPath(config, explicitPath = null) {
  return expandHomePath(
    explicitPath
      ?? getProviderConfig(config, LOCAL_GEMINI_PROVIDER)?.cliPath
      ?? config?.env?.vars?.OPENCLAW_GEMINI_CLI
      ?? process.env.OPENCLAW_GEMINI_CLI
      ?? DEFAULT_LOCAL_GEMINI_CLI_PATH,
  );
}

function getCodexCliPath(config, explicitPath = null) {
  return expandHomePath(
    explicitPath
      ?? getProviderConfig(config, 'openai-codex')?.cliPath
      ?? config?.env?.vars?.CODEX_CLI_PATH
      ?? process.env.CODEX_CLI_PATH
      ?? DEFAULT_CODEX_CLI_PATH,
  );
}

function resolveTraceLogPath({ deliverablesDir, transcriptPath }) {
  const baseDir = deliverablesDir || transcriptPath;
  if (!baseDir) return null;
  const resolved = path.resolve(baseDir);
  if (resolved.includes(`${path.sep}video-jobs${path.sep}`)) {
    const marker = `${path.sep}video-jobs${path.sep}`;
    const afterMarker = resolved.slice(resolved.indexOf(marker) + marker.length);
    const [jobId] = afterMarker.split(path.sep);
    if (jobId) {
      const jobsRoot = resolved.slice(0, resolved.indexOf(marker) + marker.length);
      return path.join(jobsRoot, jobId, 'trace.log');
    }
  }
  if (path.basename(resolved) === 'deliverables') {
    return path.join(path.dirname(resolved), 'trace.log');
  }
  return path.join(path.dirname(path.dirname(resolved)), 'trace.log');
}

async function appendRewriteTrace({ deliverablesDir, transcriptPath, stage, detail }) {
  const traceLogPath = resolveTraceLogPath({ deliverablesDir, transcriptPath });
  if (!traceLogPath) return;
  const timestamp = new Date().toISOString();
  const body = typeof detail === 'string' ? detail : JSON.stringify(detail, null, 2);
  await writeFile(traceLogPath, `[${timestamp}] [rewrite] ${stage}\n${body}\n\n`, { encoding: 'utf8', flag: 'a' });
}

function buildLocalGeminiPrompt({
  stage,
  systemPrompt,
  taskPrompt,
  targetProfile = DEFAULT_REWRITE_PROFILE_ID,
}) {
  const outputContract = shouldReturnJson(stage)
    ? '最终输出必须是唯一一个合法 JSON 对象，不要输出代码块、解释、前后缀。'
    : '最终输出必须是任务要求指定的最终正文，不要输出解释、备注、代码块或额外前后缀。';

  return [
    '你正在通过本地 Gemini CLI 处理一个受严格约束的中文内容任务。',
    `当前阶段：${stage}`,
    `目标类型：${targetProfile}`,
    '',
    '【系统角色】',
    systemPrompt,
    '',
    '【任务要求】',
    taskPrompt,
    '',
    '【硬性输出约束】',
    outputContract,
  ].join('\n');
}

export async function runLocalGeminiCli({
  cliPath = getDefaultGeminiCliPath(),
  prompt,
  cwd = os.homedir(),
  timeoutMs = 10 * 60 * 1000,
} = {}, deps = {}) {
  if (typeof prompt !== 'string' || !prompt.trim()) {
    throw new Error('Local Gemini prompt is required');
  }
  const resolvedCliPath = expandHomePath(cliPath);
  if (!resolvedCliPath) {
    throw new Error('Local Gemini CLI path is required');
  }
  const spawnImpl = deps.spawnImpl ?? spawn;

  return await new Promise((resolve, reject) => {
    // detached:true 让 bash 起一个新 process group，便于超时时整组 kill；
    // 否则 bash 死后，gemini-cli 子孙进程会变孤儿继续吃 OAuth/API 锁。
    const child = spawnImpl('bash', [resolvedCliPath, '--safe'], {
      cwd,
      env: process.env,
      stdio: ['pipe', 'pipe', 'pipe'],
      detached: true,
    });

    let stdout = '';
    let stderr = '';
    let settled = false;
    let killTimer = null;

    const finalize = (action) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (killTimer) clearTimeout(killTimer);
      // 主动 destroy stdio 流：g.sh 的孙子 gemini-cli 会继承 stderr fd，
      // 仅靠 'close' 事件会被孤儿持有的 fd 卡到永远不触发。
      try { child.stdin?.destroy(); } catch { /* noop */ }
      try { child.stdout?.destroy(); } catch { /* noop */ }
      try { child.stderr?.destroy(); } catch { /* noop */ }
      action();
    };

    const timer = setTimeout(() => {
      // 杀整个 process group（包括 bash 的孙子 gemini-cli），而不是只杀 bash。
      try { process.kill(-child.pid, 'SIGTERM'); } catch { /* noop */ }
      // 5 秒兜底 SIGKILL，防止孙子吞 TERM。
      killTimer = setTimeout(() => {
        try { process.kill(-child.pid, 'SIGKILL'); } catch { /* noop */ }
      }, 5000);
    }, timeoutMs);

    child.stdout.on('data', (chunk) => {
      stdout += chunk.toString();
    });

    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString();
    });

    child.on('error', (error) => {
      finalize(() => reject(error));
    });

    // 用 'exit'（child 死即触发）而不是 'close'（要等所有 stdio 关闭）。
    // 否则孤儿 gemini-cli 持有的 stderr 写端会让 'close' 永不触发。
    child.on('exit', (code, signal) => {
      finalize(() => {
        if (signal) {
          reject(new Error(`local-gemini killed by ${signal} (likely ${Math.round(timeoutMs / 1000)}s timeout): ${stderr.trim() || stdout.trim() || 'no output'}`));
          return;
        }
        if (code !== 0) {
          reject(new Error(`local-gemini exited with code ${code ?? 'unknown'}: ${stderr.trim() || stdout.trim() || 'no output'}`));
          return;
        }
        const body = stdout.trim();
        if (!body) {
          reject(new Error('local-gemini returned empty output'));
          return;
        }
        resolve(body);
      });
    });

    child.stdin.end(prompt);
  });
}

function buildCodexCliPrompt({
  stage,
  systemPrompt,
  taskPrompt,
  targetProfile = DEFAULT_REWRITE_PROFILE_ID,
}) {
  const outputContract = shouldReturnJson(stage)
    ? '只输出一个合法 JSON 对象，不要代码块、解释或前后缀。'
    : '只输出最终候选稿正文，不要解释过程、代码块或额外前后缀。';
  return [
    `目标类型：${targetProfile}`,
    '',
    '【系统角色】',
    systemPrompt,
    '',
    '【任务要求】',
    taskPrompt,
    '',
    '【硬性输出约束】',
    outputContract,
  ].join('\n');
}

function extractCodexJsonText(stdout) {
  let finalText = '';
  for (const rawLine of String(stdout || '').split('\n')) {
    const line = rawLine.trim();
    if (!line.startsWith('{')) continue;
    let payload = null;
    try {
      payload = JSON.parse(line);
    } catch {
      continue;
    }
    if (payload?.type !== 'item.completed') continue;
    const item = payload.item;
    if (!item || typeof item !== 'object') continue;
    const text = typeof item.text === 'string'
      ? item.text
      : Array.isArray(item.content)
        ? item.content
          .map((part) => typeof part?.text === 'string' ? part.text : '')
          .join('')
        : '';
    if (text.trim()) {
      finalText = text.trim();
    }
  }
  return finalText;
}

export async function runCodexCli({
  cliPath = getDefaultCodexCliPath(),
  prompt,
  model,
  cwd = os.homedir(),
  timeoutMs = 10 * 60 * 1000,
} = {}, deps = {}) {
  if (typeof prompt !== 'string' || !prompt.trim()) {
    throw new Error('Codex prompt is required');
  }
  if (typeof model !== 'string' || !model.trim()) {
    throw new Error('Codex model is required');
  }
  const resolvedCliPath = expandHomePath(cliPath);
  if (!resolvedCliPath) {
    throw new Error('Codex CLI path is required');
  }
  const spawnImpl = deps.spawnImpl ?? spawn;

  return await new Promise((resolve, reject) => {
    const child = spawnImpl(resolvedCliPath, [
      '-a',
      'never',
      'exec',
      '--skip-git-repo-check',
      '--ephemeral',
      '-s',
      'read-only',
      '-m',
      model,
      '--json',
      prompt,
    ], {
      cwd,
      env: process.env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => {
      child.kill('SIGTERM');
    }, timeoutMs);

    child.stdout.on('data', (chunk) => {
      stdout += chunk.toString();
    });

    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString();
    });

    child.on('error', (error) => {
      clearTimeout(timer);
      reject(error);
    });

    child.on('close', (code, signal) => {
      clearTimeout(timer);
      if (signal) {
        reject(new Error(`codex cli terminated by signal ${signal}`));
        return;
      }
      if (code !== 0) {
        const detail = (stderr || stdout || `codex cli exited with code ${code}`).trim();
        reject(new Error(detail));
        return;
      }

      const text = extractCodexJsonText(stdout);
      if (!text) {
        reject(new Error('codex cli returned empty output'));
        return;
      }
      resolve(text);
    });
  });
}

function buildOutlinePrompt(transcriptText, { targetProfile = DEFAULT_REWRITE_PROFILE_ID, analysisRecord = null } = {}) {
  const profile = getRewriteProfile(targetProfile);
  return profile.buildOutlinePrompt({ transcriptText, analysisRecord });
}

function buildDraftPrompt(outline, { targetProfile = DEFAULT_REWRITE_PROFILE_ID, analysisRecord = null } = {}) {
  const profile = getRewriteProfile(targetProfile);
  return profile.buildDraftPrompt({ outline, analysisRecord });
}

function buildAnalysisPrompt(sourceText, { targetProfile = DEFAULT_REWRITE_PROFILE_ID } = {}) {
  const profile = getRewriteProfile(targetProfile);
  return profile.buildAnalysisPrompt({ sourceText });
}

function shouldReturnJson(stage) {
  return stage === 'outline' || stage === 'analysis';
}

function buildStagePrompt(stage, transcriptText, outline, { targetProfile = DEFAULT_REWRITE_PROFILE_ID, analysisRecord = null } = {}) {
  if (stage === 'outline') {
    return buildOutlinePrompt(transcriptText, { targetProfile, analysisRecord });
  }
  if (stage === 'analysis') {
    return buildAnalysisPrompt(transcriptText, { targetProfile });
  }
  return buildDraftPrompt(outline, { targetProfile, analysisRecord });
}

function buildStageSystemPrompt(stage, { targetProfile = DEFAULT_REWRITE_PROFILE_ID } = {}) {
  const profile = getRewriteProfile(targetProfile);
  return profile.buildStageSystemPrompt(stage);
}

function stripJsonCodeFence(text) {
  const trimmed = String(text || '').trim();
  const fencedMatch = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  return fencedMatch ? fencedMatch[1].trim() : trimmed;
}

function extractBalancedJsonObject(text) {
  const source = String(text || '');
  const start = source.indexOf('{');
  if (start === -1) {
    return source.trim();
  }

  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let index = start; index < source.length; index += 1) {
    const char = source[index];
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (char === '\\') {
        escaped = true;
      } else if (char === '"') {
        inString = false;
      }
      continue;
    }

    if (char === '"') {
      inString = true;
      continue;
    }

    if (char === '{') {
      depth += 1;
      continue;
    }

    if (char === '}') {
      depth -= 1;
      if (depth === 0) {
        return source.slice(start, index + 1).trim();
      }
    }
  }

  return source.slice(start).trim();
}

function cleanupLooseJson(text) {
  return String(text || '')
    .replace(/(^|[^\\])“/g, '$1"')
    .replace(/(^|[^\\])”/g, '$1"')
    .replace(/(^|[^\\])‘/g, "$1'")
    .replace(/(^|[^\\])’/g, "$1'")
    .replace(/,\s*([}\]])/g, '$1')
    .trim();
}

function escapeInnerQuotesInArrayStrings(source) {
  let result = '';
  let inString = false;
  let escaped = false;

  for (let index = 0; index < source.length; index += 1) {
    const char = source[index];

    if (!inString) {
      if (char === '"') {
        inString = true;
      }
      result += char;
      continue;
    }

    if (escaped) {
      result += char;
      escaped = false;
      continue;
    }

    if (char === '\\') {
      result += char;
      escaped = true;
      continue;
    }

    if (char === '"') {
      const nextChar = source[index + 1] ?? '';
      if (/[\]\},:\s]/.test(nextChar)) {
        inString = false;
        result += char;
      } else {
        result += '\\"';
      }
      continue;
    }

    result += char;
  }

  return result;
}

export function parseJsonObject(text) {
  const normalized = cleanupLooseJson(extractBalancedJsonObject(stripJsonCodeFence(text)));
  try {
    return JSON.parse(normalized);
  } catch (primaryError) {
    const repaired = escapeInnerQuotesInArrayStrings(normalized);
    try {
      return JSON.parse(repaired);
    } catch {
      throw primaryError;
    }
  }
}

export function createDefaultModelInvoker({
  modelConfig,
  geminiCliPath = null,
  codexCliPath = null,
  runGeminiCliImpl = runLocalGeminiCli,
  runCodexCliImpl = runCodexCli,
} = {}) {
  return async function invokeModel({
    stage,
    model,
    transcriptText,
    outline,
    transcriptPath = null,
    deliverablesDir = null,
    targetProfile = DEFAULT_REWRITE_PROFILE_ID,
    analysisRecord = null,
  }) {
    if (!model?.modelRef || typeof model.modelRef !== 'string') {
      throw new Error('Rewrite model reference is missing');
    }

    const [provider, modelName] = model.modelRef.split('/', 2);
    const normalizedTargetProfile = normalizeRewriteProfileId(targetProfile);
    if (!provider || !modelName) {
      throw new Error(`Invalid rewrite model reference: ${model.modelRef}`);
    }

    if (provider === LOCAL_GEMINI_PROVIDER) {
      const prompt = buildStagePrompt(stage, transcriptText, outline, {
        targetProfile: normalizedTargetProfile,
        analysisRecord,
      });
      const systemPrompt = buildStageSystemPrompt(stage, { targetProfile: normalizedTargetProfile });
      const resolvedCliPath = getLocalGeminiCliPath(modelConfig, geminiCliPath);
      if (!resolvedCliPath || !await pathExists(resolvedCliPath)) {
        throw createStageError({
          stage,
          code: 'provider_not_configured',
          message: `Local Gemini CLI is not available: ${resolvedCliPath || '(missing path)'}`,
        });
      }
      const requestPrompt = buildLocalGeminiPrompt({
        stage,
        systemPrompt,
        taskPrompt: prompt,
        targetProfile: normalizedTargetProfile,
      });

      await appendRewriteTrace({
        deliverablesDir,
        transcriptPath,
        stage: 'llm_request',
        detail: {
          provider: LOCAL_GEMINI_PROVIDER,
          modelRef: model.modelRef,
          stage,
          cliPath: resolvedCliPath,
          transport: 'stdio',
          promptLength: requestPrompt.length,
          systemPromptLength: systemPrompt.length,
          userPromptLength: prompt.length,
        },
      });

      try {
        const text = await runGeminiCliImpl({
          cliPath: resolvedCliPath,
          prompt: requestPrompt,
          cwd: deliverablesDir || os.homedir(),
        });
        await appendRewriteTrace({
          deliverablesDir,
          transcriptPath,
          stage: 'llm_response_ok',
          detail: {
            provider: LOCAL_GEMINI_PROVIDER,
            modelRef: model.modelRef,
            textPreview: text.slice(0, 240),
          },
        });
        return {
          result: shouldReturnJson(stage) ? parseJsonObject(text) : text.trim(),
          usage: { inputTokens: null, outputTokens: null },
          effectiveModel: { id: model.id, label: model.label, modelRef: model.modelRef },
        };
      } catch (error) {
        await appendRewriteTrace({
          deliverablesDir,
          transcriptPath,
          stage: 'llm_response_error',
          detail: {
            provider: LOCAL_GEMINI_PROVIDER,
            modelRef: model.modelRef,
            error: error instanceof Error ? error.message : String(error),
          },
        });
        throw error;
      }
    }

    if (provider === 'openai-codex') {
      const prompt = buildStagePrompt(stage, transcriptText, outline, {
        targetProfile: normalizedTargetProfile,
        analysisRecord,
      });
      const systemPrompt = buildStageSystemPrompt(stage, { targetProfile: normalizedTargetProfile });
      const requestPrompt = buildCodexCliPrompt({
        stage,
        systemPrompt,
        taskPrompt: prompt,
        targetProfile: normalizedTargetProfile,
      });
      const resolvedCliPath = getCodexCliPath(modelConfig, codexCliPath);

      try {
        await appendRewriteTrace({
          deliverablesDir,
          transcriptPath,
          stage: 'llm_request',
          detail: {
            provider: 'openai-codex',
            modelRef: model.modelRef,
            stage,
            cliPath: resolvedCliPath,
            transport: 'codex-cli',
            promptLength: requestPrompt.length,
            systemPromptLength: systemPrompt.length,
            userPromptLength: prompt.length,
          },
        });

        const text = await runCodexCliImpl({
          cliPath: resolvedCliPath,
          prompt: requestPrompt,
          model: modelName,
          cwd: deliverablesDir || os.homedir(),
        });
        const usage = { inputTokens: null, outputTokens: null };
        await appendRewriteTrace({
          deliverablesDir,
          transcriptPath,
          stage: 'llm_response_ok',
          detail: {
            provider: 'openai-codex',
            modelRef: model.modelRef,
            usage,
            textPreview: text.slice(0, 240),
          },
        });

        return {
          result: shouldReturnJson(stage) ? parseJsonObject(text) : text,
          usage,
          effectiveModel: { id: model.id, label: model.label, modelRef: model.modelRef },
        };
      } catch (error) {
        await appendRewriteTrace({
          deliverablesDir,
          transcriptPath,
          stage: 'llm_response_error',
          detail: {
            provider: 'openai-codex',
            modelRef: model.modelRef,
            error: error instanceof Error ? error.message : String(error),
          },
        });
        throw error;
      }
    }

    throw createStageError({
      stage,
      code: 'provider_not_supported',
      message: `Rewrite provider is not supported yet: ${provider}`,
    });
  };
}

function getConfiguredModelRefs(config) {
  const modelEntries = config?.agents?.defaults?.models;

  if (!modelEntries || typeof modelEntries !== 'object') {
    return new Set();
  }

  return new Set(Object.keys(modelEntries));
}

export async function resolveRewriteModels({
  modelConfigPath = getDefaultModelConfigPath(),
  geminiCliPath = null,
} = {}) {
  const config = await loadModelConfig(modelConfigPath);
  const configuredRefs = getConfiguredModelRefs(config);
  const resolvedLocalGeminiCliPath = getLocalGeminiCliPath(config, geminiCliPath);
  const localGeminiAvailable = Boolean(resolvedLocalGeminiCliPath && await pathExists(resolvedLocalGeminiCliPath));
  const resolved = [];

  for (const candidate of REWRITE_MODEL_CANDIDATES) {
    if (candidate.modelRef === LOCAL_GEMINI_MODEL_REF) {
      if (localGeminiAvailable) {
        resolved.push({
          ...candidate,
          status: 'available',
          reason: null,
        });
      }
      continue;
    }

    const configured = configuredRefs.has(candidate.modelRef);
    resolved.push({
      ...candidate,
      status: configured ? 'available' : 'skipped',
      reason: configured ? null : 'model_not_configured',
    });
  }

  return resolved;
}

async function pathExists(filePath) {
  try {
    await access(filePath);
    return true;
  } catch (error) {
    if (error && error.code === 'ENOENT') {
      return false;
    }

    throw error;
  }
}

async function resolveOutlineSource({ transcriptPath, polishedTranscriptPath }) {
  if (polishedTranscriptPath && await pathExists(polishedTranscriptPath)) {
    return {
      sourcePath: polishedTranscriptPath,
      sourceType: 'polished_transcript',
    };
  }

  return {
    sourcePath: transcriptPath,
    sourceType: 'raw_transcript',
  };
}

function isStringArray(value) {
  return Array.isArray(value) && value.every((item) => typeof item === 'string' && item.length > 0);
}

function buildPassthroughOutlineRecord({ sourcePath, sourceType, transcriptText, userRequirements = '' }) {
  // 跳过 outline LLM 阶段时使用：保留必填字段以兼容 outline.json 结构，
  // 同时携带 sourceText/userRequirements，由 buildDraftPrompt 直接消费。
  return {
    sourcePath,
    sourceType,
    topic: '（passthrough：源文档作为写作指南整体传入）',
    corePoints: [],
    facts: [],
    angles: [],
    constraints: [],
    sourceText: typeof transcriptText === 'string' ? transcriptText : '',
    userRequirements: typeof userRequirements === 'string' ? userRequirements : '',
    generatedBy: 'passthrough',
    generatedAt: new Date().toISOString(),
  };
}

function buildOutlineRecord({ sourcePath, sourceType, outline }) {
  if (!outline || typeof outline !== 'object') {
    throw new Error('Outline generation returned no object payload');
  }

  if (typeof outline.topic !== 'string' || outline.topic.length === 0) {
    throw new Error('Outline generation returned an invalid topic');
  }

  for (const field of ['corePoints', 'facts', 'angles', 'constraints']) {
    if (!isStringArray(outline[field])) {
      throw new Error(`Outline generation returned invalid ${field}`);
    }
  }

  if (typeof outline.generatedBy !== 'string' || outline.generatedBy.length === 0) {
    throw new Error('Outline generation returned an invalid generatedBy');
  }

  return {
    sourcePath,
    sourceType,
    topic: outline.topic,
    corePoints: outline.corePoints,
    facts: outline.facts,
    angles: outline.angles,
    constraints: outline.constraints,
    generatedBy: outline.generatedBy,
    generatedAt: typeof outline.generatedAt === 'string' && outline.generatedAt.length > 0
      ? outline.generatedAt
      : new Date().toISOString(),
  };
}

function createStageError({ stage, code, message }) {
  return { stage, code, message };
}

function isStageError(error) {
  return Boolean(error) && typeof error === 'object' && 'stage' in error && 'code' in error && 'message' in error;
}

function getAvailableModelForStage(models, stage) {
  const availableModel = models.find((model) => model.status === 'available');

  if (!availableModel) {
    throw createStageError({
      stage,
      code: 'model_not_configured',
      message: `No configured rewrite model is available for ${stage} generation`,
    });
  }

  return availableModel;
}

export async function runRewriteAnalysis({
  transcriptPath,
  deliverablesDir,
  modelConfigPath = getDefaultModelConfigPath(),
  geminiCliPath = null,
  codexCliPath = null,
  runGeminiCliImpl,
  runCodexCliImpl,
  modelInvoker,
  targetProfile = DEFAULT_REWRITE_PROFILE_ID,
} = {}) {
  const normalizedTargetProfile = normalizeRewriteProfileId(targetProfile);
  const profile = getRewriteProfile(normalizedTargetProfile);
  if (!profile.requiresAnalysis) {
    return null;
  }

  const transcriptText = await readFile(transcriptPath, 'utf8');
  const modelConfig = await loadModelConfig(modelConfigPath);
  const models = await resolveRewriteModels({ modelConfigPath, geminiCliPath });
  const effectiveModelInvoker = modelInvoker ?? createDefaultModelInvoker({
    modelConfig,
    geminiCliPath,
    codexCliPath,
    runGeminiCliImpl,
    runCodexCliImpl,
  });
  const availableModel = getAvailableModelForStage(models, 'analysis');
  const { result } = await effectiveModelInvoker({
    stage: 'analysis',
    model: availableModel,
    transcriptText,
    transcriptPath,
    deliverablesDir,
    targetProfile: normalizedTargetProfile,
  });

  if (!result || typeof result !== 'object') {
    throw new Error('Analysis generation returned no object payload');
  }

  return {
    ...result,
    formatName: typeof result.formatName === 'string' && result.formatName.trim()
      ? result.formatName.trim()
      : '头条',
    generatedBy: result?.generatedBy ?? availableModel.modelRef,
    generatedAt: typeof result?.generatedAt === 'string' && result.generatedAt.length > 0
      ? result.generatedAt
      : new Date().toISOString(),
  };
}

async function defaultOutlineGenerator({
  sourcePath,
  sourceType,
  transcriptText,
  models,
  modelInvoker,
  deliverablesDir,
  targetProfile = DEFAULT_REWRITE_PROFILE_ID,
  analysisRecord = null,
}) {
  const availableModel = getAvailableModelForStage(models, 'outline');
  const { result: outline } = await modelInvoker({
    stage: 'outline',
    model: availableModel,
    sourcePath,
    sourceType,
    transcriptText,
    transcriptPath: sourcePath,
    deliverablesDir,
    targetProfile,
    analysisRecord,
  });

  return {
    ...outline,
    generatedBy: outline?.generatedBy ?? availableModel.modelRef,
  };
}

async function defaultDraftGenerator({
  model,
  outline,
  outlinePath,
  modelInvoker,
  targetProfile = DEFAULT_REWRITE_PROFILE_ID,
  analysisRecord = null,
}) {
  const { result: draft, usage, effectiveModel } = await modelInvoker({
    stage: 'draft',
    model,
    outline,
    outlinePath,
    transcriptPath: outlinePath,
    deliverablesDir: path.dirname(outlinePath),
    targetProfile,
    analysisRecord,
  });

  if (typeof draft !== 'string' || draft.trim().length === 0) {
    throw new Error(`Draft generation returned invalid content for ${model.id}`);
  }

  return { text: draft, usage, effectiveModel };
}

function getDraftPath(rewriteDir, modelId, targetProfile = DEFAULT_REWRITE_PROFILE_ID) {
  const profile = getRewriteProfile(targetProfile);
  return path.join(rewriteDir, `${profile.draftFilePrefix}-${modelId}.md`);
}

async function generateDraftRecord({
  model,
  rewriteDir,
  outlineRecord,
  outlinePath,
  draftGenerator,
  modelInvoker,
  targetProfile = DEFAULT_REWRITE_PROFILE_ID,
  analysisRecord = null,
}) {
  const draftPath = getDraftPath(rewriteDir, model.id, targetProfile);

  if (model.status === 'skipped') {
    await removeFileIfPresent(draftPath);
    return buildDraftRecord({
      modelId: model.id,
      modelLabel: model.label,
      status: 'skipped',
      path: draftPath,
      reason: model.reason,
    });
  }

  const startedAt = Date.now();

  try {
    const draftResult = await draftGenerator({
      model,
      outline: outlineRecord,
      outlinePath,
      modelInvoker,
      targetProfile,
      analysisRecord,
    });
    // draftGenerator 返回 { text, usage, effectiveModel } 或纯字符串（兼容旧 mock）
    const content = typeof draftResult === 'string' ? draftResult : draftResult.text;
    const usage = typeof draftResult === 'string' ? {} : (draftResult.usage || {});
    const effectiveModel = typeof draftResult !== 'string' ? (draftResult.effectiveModel || null) : null;
    // fallback 时 effectiveModel 存在且与原始 model 不同时，用它更新记录
    const finalModelId = effectiveModel?.id ?? model.id;
    const finalModelLabel = effectiveModel?.label ?? model.label;

    await writeFile(draftPath, content, 'utf8');

    return buildDraftRecord({
      modelId: finalModelId,
      modelLabel: finalModelLabel,
      status: 'success',
      path: draftPath,
      durationMs: Date.now() - startedAt,
      inputTokens: usage.inputTokens ?? null,
      outputTokens: usage.outputTokens ?? null,
    });
  } catch (error) {
    await removeFileIfPresent(draftPath);
    return buildDraftRecord({
      modelId: model.id,
      modelLabel: model.label,
      status: 'failed',
      path: draftPath,
      durationMs: Date.now() - startedAt,
      error: error instanceof Error ? error.message : String(error),
    });
  }
}

function buildDraftIndex({ outlinePath, drafts }) {
  return {
    outlinePath,
    drafts,
  };
}

function buildFeedbackRecord({ entries = [] } = {}) {
  if (!Array.isArray(entries)) {
    throw new Error('Feedback entries must be an array');
  }

  return {
    entries: entries.map((entry) => {
      if (!entry || typeof entry !== 'object') {
        throw new Error('Feedback entry must be an object');
      }

      return {
        selectedModelId: entry.selectedModelId ?? null,
        rawText: entry.rawText ?? null,
        createdAt: entry.createdAt ?? null,
      };
    }),
  };
}

async function loadFeedbackRecord(feedbackPath) {
  try {
    const raw = await readFile(feedbackPath, 'utf8');
    return buildFeedbackRecord(JSON.parse(raw));
  } catch (error) {
    if (error && error.code === 'ENOENT') {
      return null;
    }

    if (error && error.name === 'SyntaxError') {
      throw createStageError({
        stage: 'feedback',
        code: 'feedback_persistence_failed',
        message: `Malformed feedback file: ${feedbackPath}`,
      });
    }

    throw error;
  }
}

async function persistFeedbackFile({ feedbackPath, entries }) {
  const feedbackRecord = await loadFeedbackRecord(feedbackPath) ?? buildFeedbackRecord({ entries });
  await writeFile(feedbackPath, JSON.stringify(feedbackRecord, null, 2), 'utf8');
}

async function removeFileIfPresent(filePath) {
  try {
    await unlink(filePath);
  } catch (error) {
    if (error && error.code === 'ENOENT') {
      return;
    }

    throw error;
  }
}

async function defaultIndexWriter({ indexPath, indexRecord }) {
  await writeFile(indexPath, JSON.stringify(indexRecord, null, 2), 'utf8');
}

function getRewriteStatusFromDrafts(drafts) {
  const successCount = drafts.filter((draft) => draft.status === 'success').length;
  const failedCount = drafts.filter((draft) => draft.status === 'failed').length;

  if (successCount > 0 && failedCount === 0) {
    return 'success';
  }

  if (successCount > 0) {
    return 'partial_success';
  }

  return 'failed';
}

export async function runRewriteForItem({
  transcriptPath,
  polishedTranscriptPath,
  deliverablesDir,
  modelConfigPath = getDefaultModelConfigPath(),
  geminiCliPath = null,
  codexCliPath = null,
  runGeminiCliImpl,
  runCodexCliImpl,
  targetProfile = DEFAULT_REWRITE_PROFILE_ID,
  analysisRecord = null,
  userRequirements = '',
  outlineGenerator,
  modelInvoker,
  draftGenerator,
  indexWriter = defaultIndexWriter,
} = {}) {
  const rewriteDir = path.join(deliverablesDir, 'rewrite');
  const normalizedTargetProfile = normalizeRewriteProfileId(targetProfile);
  const outlinePath = path.join(rewriteDir, 'outline.json');
  const indexPath = path.join(rewriteDir, 'index.json');
  const feedbackPath = path.join(rewriteDir, 'feedback.json');
  const result = {
    status: 'failed',
    outlinePath: null,
    indexPath: null,
    feedbackPath,
    drafts: [],
    errors: [],
  };
  let failureContext = 'outline';

  try {
    await mkdir(rewriteDir, { recursive: true });
    await removeFileIfPresent(outlinePath);
    await removeFileIfPresent(indexPath);
    failureContext = 'feedback';
    await persistFeedbackFile({ feedbackPath, entries: [] });
    failureContext = 'outline';

    const { sourcePath, sourceType } = await resolveOutlineSource({
      transcriptPath,
      polishedTranscriptPath,
    });
    const transcriptText = await readFile(sourcePath, 'utf8');
    const modelConfig = await loadModelConfig(modelConfigPath);
    const models = await resolveRewriteModels({ modelConfigPath, geminiCliPath });
    const effectiveModelInvoker = modelInvoker ?? createDefaultModelInvoker({
      modelConfig,
      geminiCliPath,
      codexCliPath,
      runGeminiCliImpl,
      runCodexCliImpl,
    });
    const profile = getRewriteProfile(normalizedTargetProfile);
    // 当 profile.requiresOutline === false 时跳过 outline LLM 调用，把源文档整体作为
    // 写作指南直接喂给 draft（避免 outline 阶段把分析报告压成 5 字段后丢失大量信息）。
    let outlineRecord;
    if (outlineGenerator || profile.requiresOutline !== false) {
      const generateOutline = outlineGenerator ?? defaultOutlineGenerator;
      const outline = await generateOutline({
        sourcePath,
        sourceType,
        transcriptText,
        models,
        modelInvoker: effectiveModelInvoker,
        deliverablesDir,
        targetProfile: normalizedTargetProfile,
        analysisRecord,
      });
      outlineRecord = buildOutlineRecord({ sourcePath, sourceType, outline });
      if (userRequirements) {
        outlineRecord.userRequirements = userRequirements;
      }
    } else {
      outlineRecord = buildPassthroughOutlineRecord({
        sourcePath,
        sourceType,
        transcriptText,
        userRequirements,
      });
    }

    await writeFile(outlinePath, JSON.stringify(outlineRecord, null, 2), 'utf8');
    result.outlinePath = outlinePath;

    const generateDraft = draftGenerator ?? defaultDraftGenerator;
    const drafts = await Promise.all(models.map((model, index) =>
      new Promise((r) => setTimeout(r, index * 2000)).then(() => generateDraftRecord({
        model,
        rewriteDir,
        outlineRecord,
        outlinePath,
        draftGenerator: generateDraft,
        modelInvoker: effectiveModelInvoker,
        targetProfile: normalizedTargetProfile,
        analysisRecord,
      }))
    ));
    result.drafts = drafts;

    const indexRecord = buildDraftIndex({ outlinePath, drafts });
    const errors = drafts
      .filter((draft) => draft.status === 'failed')
      .map((draft) => createStageError({
        stage: 'draft',
        code: 'draft_generation_failed',
        message: `${draft.modelId}: ${draft.error}`,
      }));
    result.errors = errors;
    result.indexPath = indexPath;
    failureContext = 'draft_index';

    await indexWriter({ indexPath, indexRecord, drafts, outlinePath });

    result.status = getRewriteStatusFromDrafts(drafts);
    return result;
  } catch (error) {
    const structuredError = isStageError(error)
      ? error
      : failureContext === 'draft_index'
        ? createStageError({
          stage: 'draft_index',
          code: 'index_persistence_failed',
          message: error instanceof Error ? error.message : String(error),
        })
        : failureContext === 'feedback'
          ? createStageError({
            stage: 'feedback',
            code: 'feedback_persistence_failed',
            message: error instanceof Error ? error.message : String(error),
          })
        : createStageError({
          stage: 'outline',
          code: 'outline_generation_failed',
          message: error instanceof Error ? error.message : String(error),
        });

    result.status = 'failed';
    result.errors = result.errors.length > 0 && structuredError.stage !== 'draft'
      ? [...result.errors, structuredError]
      : [structuredError];

    return result;
  }
}

// ── CLI 入口 ──────────────────────────────────────────
// 用法: node video_rewrite_runner.mjs '{"transcriptPath":"...","deliverablesDir":"..."}'
// 可选字段: polishedTranscriptPath, modelConfigPath
async function main() {
  const rawPayload = process.argv[2];
  if (!rawPayload) {
    console.error('Usage: node video_rewrite_runner.mjs \'{"transcriptPath":"...","deliverablesDir":"..."}\'');
    process.exit(1);
  }
  let payload;
  try {
    payload = JSON.parse(rawPayload);
  } catch {
    console.error('Invalid JSON payload');
    process.exit(1);
  }
  if (!payload.transcriptPath || !payload.deliverablesDir) {
    console.error('transcriptPath and deliverablesDir are required');
    process.exit(1);
  }
  const result = await runRewriteForItem({
    transcriptPath: payload.transcriptPath,
    polishedTranscriptPath: payload.polishedTranscriptPath ?? null,
    deliverablesDir: payload.deliverablesDir,
    modelConfigPath: payload.modelConfigPath ?? getDefaultModelConfigPath(),
  });
  console.log(JSON.stringify(result, null, 2));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(error.message ?? error);
    process.exit(1);
  });
}
