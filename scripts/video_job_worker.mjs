#!/usr/bin/env node
import { existsSync, promises as fs } from 'fs';
import os from 'os';
import path from 'path';
import { spawn } from 'child_process';
import { fileURLToPath } from 'url';
import {
  addMemberPermission,
  createDoc,
  createDriveFolder as createDriveFolderWithSdk,
  createFeishuClient,
  createTask as createTaskWithSdk,
  getUploadedFileInfo as getUploadedFileInfoWithSdk,
  listDriveFolder as listDriveFolderWithSdk,
  patchTask as patchTaskWithSdk,
  resolveFeishuApiBase,
  resolveFeishuDocBase,
  sendImMessage,
  setOrgReadablePermission as setOrgReadablePermissionWithSdk,
  uploadDriveFile as uploadDriveFileWithCli,
  writeMarkdownToDocx,
} from './feishu_sdk_adapter.mjs';
import { runCodexCli, getDefaultCodexCliPath } from './video_rewrite_runner.mjs';

// 精华分析使用 gpt-5.5，统一在 ASR 阶段产出爆款洞察文档；多模型改写在 /rw 命令中独立执行。
const HIGHLIGHT_MODEL = 'gpt-5.5';


const JOB_RETENTION_DAYS = 7;
const TERMINAL_JOB_STATES = new Set(['completed', 'completed_with_errors', 'failed']);

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const defaultWorkspaceDir = path.resolve(scriptDir, '..');

const rawPayload = JSON.parse(process.argv[2] ?? '{}');
const payload = {
  ...rawPayload,
  inputs: Array.isArray(rawPayload.inputs)
    ? rawPayload.inputs
    : Array.isArray(rawPayload.urls)
      ? rawPayload.urls
      : [],
};
// 默认从脚本位置反推 workspace 根目录，避免目录重组后仍然依赖旧硬编码路径。
const workspaceDir = process.env.OPENCLAW_WORKSPACE_DIR || defaultWorkspaceDir;
const jobLayout = getJobLayout(workspaceDir, payload.jobId || 'adhoc');
const jobsDir = jobLayout.jobsDir;
const jobDir = jobLayout.jobDir;
const jobPath = jobLayout.jobPath;
const rawDir = jobLayout.rawDir;
const deliverablesDir = jobLayout.deliverablesDir;
const traceLogPath = path.join(jobDir, 'trace.log');
const VIDEO_PIPELINE_SCRIPT = path.join(workspaceDir, 'skills', 'video-pipeline', 'scripts', 'video_pipeline.py');
const PYTHON_BIN = resolvePythonBin(process.env);
// DRIVE_SIMPLE_UPLOAD_MAX_BYTES 已不再使用：分片/简单上传由 lark-cli +upload 内部自动决定。

export function getJobLayout(baseWorkspaceDir, jobId) {
  const resolvedJobsDir = path.join(baseWorkspaceDir, 'video-jobs');
  const resolvedJobDir = path.join(resolvedJobsDir, jobId);
  return {
    jobsDir: resolvedJobsDir,
    jobDir: resolvedJobDir,
    jobPath: path.join(resolvedJobDir, 'job.json'),
    rawDir: path.join(resolvedJobDir, 'raw'),
    deliverablesDir: path.join(resolvedJobDir, 'deliverables'),
  };
}

export function resolvePythonBin(env = process.env, options = {}) {
  const configured = typeof env.OPENCLAW_PYTHON === 'string'
    ? env.OPENCLAW_PYTHON.trim()
    : '';
  if (configured) {
    return configured;
  }

  const exists = options.exists ?? ((candidate) => existsSync(candidate));
  const pathEnv = typeof env.PATH === 'string' ? env.PATH : process.env.PATH;
  const resolvedPython3 = options.which?.('python3') ?? resolveFromPath('python3', pathEnv, exists);
  const candidates = options.candidates ?? [
    resolvedPython3,
    '/opt/homebrew/bin/python3',
    '/usr/local/bin/python3',
    '/usr/bin/python3',
  ];

  // 优先锁定当前进程可解析到的绝对路径，其次再用常见 Homebrew 路径，避免 gateway 最小 PATH 回落到系统 Python 3.9。
  for (const candidate of candidates) {
    if (candidate && exists(candidate)) {
      return candidate;
    }
  }
  return options.fallback ?? 'python3';
}

function resolveFromPath(commandName, pathEnv, exists) {
  if (!pathEnv) {
    return null;
  }
  for (const segment of pathEnv.split(path.delimiter)) {
    const trimmed = segment.trim();
    if (!trimmed) {
      continue;
    }
    const candidate = path.join(trimmed, commandName);
    if (exists(candidate)) {
      return candidate;
    }
  }
  return null;
}

function isTerminalJobState(state) {
  return TERMINAL_JOB_STATES.has(String(state || ''));
}

// 将完整错误追加写入 error.log，飞书侧只发摘要
async function writeErrorLog(targetJobDir, jobId, stage, errorText) {
  const logPath = path.join(targetJobDir, 'error.log');
  const timestamp = new Date().toISOString();
  const entry = `[${timestamp}] ${stage}\n${errorText}\n\n`;
  await fs.appendFile(logPath, entry);
}

async function appendTraceLog(stage, detail) {
  await ensureDirs();
  const timestamp = new Date().toISOString();
  const body = typeof detail === 'string' ? detail : JSON.stringify(detail, null, 2);
  await fs.appendFile(traceLogPath, `[${timestamp}] [worker] ${stage}\n${body}\n\n`, 'utf8');
}

async function safeReadJson(filePath) {
  try {
    return JSON.parse(await fs.readFile(filePath, 'utf8'));
  } catch {
    return null;
  }
}

function normalizeVariant(variant) {
  if (!variant || typeof variant !== 'object') {
    return null;
  }
  return {
    modelId: variant.modelId ?? null,
    path: variant.path ?? null,
    status: variant.status ?? null,
    errorKind: variant.errorKind ?? null,
    reason: variant.reason ?? null,
  };
}

function normalizeVariantList(variants) {
  if (!Array.isArray(variants)) {
    return [];
  }
  return variants
    .map((variant) => normalizeVariant(variant))
    .filter(Boolean);
}

function resolveTranscriptPath(transcript) {
  if (typeof transcript === 'string') {
    return transcript.trim() || null;
  }
  if (!transcript || typeof transcript !== 'object') {
    return null;
  }
  if (typeof transcript.rawTextPath === 'string' && transcript.rawTextPath.trim()) {
    return transcript.rawTextPath.trim();
  }
  if (typeof transcript.path === 'string' && transcript.path.trim()) {
    return transcript.path.trim();
  }
  return null;
}

function normalizePipelineResult(result) {
  if (!result || typeof result !== 'object') {
    return null;
  }

  return {
    transcriptPath: resolveTranscriptPath(result.transcript) || resolveTranscriptPath(result.rawTranscriptPath),
    polishedTranscriptPath: typeof result.polishedTranscriptPath === 'string' && result.polishedTranscriptPath.trim()
      ? result.polishedTranscriptPath.trim()
      : null,
    rewritePath: typeof result.rewritePath === 'string' && result.rewritePath.trim()
      ? result.rewritePath.trim()
      : null,
    selectedPolishedModelId: typeof result.selectedPolishedModelId === 'string' && result.selectedPolishedModelId.trim()
      ? result.selectedPolishedModelId.trim()
      : null,
    selectedRewriteModelId: typeof result.selectedRewriteModelId === 'string' && result.selectedRewriteModelId.trim()
      ? result.selectedRewriteModelId.trim()
      : null,
    failureReasons: result.failureReasons && typeof result.failureReasons === 'object'
      ? result.failureReasons
      : null,
    polishedVariants: normalizeVariantList(result.polishedVariants),
    rewriteVariants: normalizeVariantList(result.rewriteVariants),
  };
}

function resolvePipelineResultPath(outputDir) {
  if (!outputDir) {
    return path.join(deliverablesDir, 'result.json');
  }
  const normalizedOutputDir = path.resolve(outputDir);
  const statsTarget = path.basename(normalizedOutputDir) === 'result.json'
    ? normalizedOutputDir
    : path.basename(normalizedOutputDir) === 'raw'
      ? path.join(path.dirname(normalizedOutputDir), 'deliverables', 'result.json')
      : path.join(normalizedOutputDir, 'deliverables', 'result.json');
  return statsTarget;
}

export async function loadPipelineResult(outputDir = rawDir) {
  return safeReadJson(resolvePipelineResultPath(outputDir));
}

export function mergePipelineResult(item, result) {
  const normalized = normalizePipelineResult(result);
  if (!normalized) {
    return item;
  }

  if (normalized.transcriptPath) {
    item.transcriptPath = normalized.transcriptPath;
  }
  if (normalized.polishedTranscriptPath) {
    item.polishedTranscriptPath = normalized.polishedTranscriptPath;
  }
  if (normalized.rewritePath) {
    item.rewritePath = normalized.rewritePath;
  }
  item.selectedPolishedModelId = normalized.selectedPolishedModelId;
  item.selectedRewriteModelId = normalized.selectedRewriteModelId;
  item.failureReasons = normalized.failureReasons;
  item.polishedVariants = normalized.polishedVariants;
  item.rewriteVariants = normalized.rewriteVariants;
  return item;
}

async function movePathsToTrash(paths) {
  if (paths.length === 0) {
    return;
  }
  await new Promise((resolve, reject) => {
    const child = spawn('trash', paths, {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stderr = '';
    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString();
    });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code !== 0) {
        reject(new Error(stderr.trim() || `trash exited with code ${code}`));
        return;
      }
      resolve();
    });
  });
}

export async function cleanupExpiredJobs(currentJobsDir, options = {}) {
  const now = options.now ?? Date.now();
  const retentionDays = options.retentionDays ?? JOB_RETENTION_DAYS;
  const trashPaths = options.trashPaths ?? movePathsToTrash;
  const cutoffMs = now - (retentionDays * 24 * 60 * 60 * 1000);

  let entries = [];
  try {
    entries = await fs.readdir(currentJobsDir, { withFileTypes: true });
  } catch (error) {
    if (error && error.code === 'ENOENT') {
      return [];
    }
    throw error;
  }

  const expiredPaths = [];
  for (const entry of entries) {
    const entryPath = path.join(currentJobsDir, entry.name);
    const stats = await fs.stat(entryPath);
    if (stats.mtimeMs >= cutoffMs) {
      continue;
    }

    if (entry.isDirectory()) {
      const job = await safeReadJson(path.join(entryPath, 'job.json'));
      if (!job || !isTerminalJobState(job.state)) {
        continue;
      }
      expiredPaths.push(entryPath);
      continue;
    }

    if (entry.isFile() && entry.name.endsWith('.json')) {
      expiredPaths.push(entryPath);
    }
  }

  if (expiredPaths.length > 0) {
    await trashPaths(expiredPaths);
  }
  return expiredPaths;
}

const apiBase = resolveFeishuApiBase(payload.domain);
const docBase = resolveFeishuDocBase(payload.domain);
let feishuClientPromise;

async function getFeishuClient() {
  if (!feishuClientPromise) {
    feishuClientPromise = createFeishuClient({
      appId: payload.appId,
      appSecret: payload.appSecret,
      domain: payload.domain,
    });
  }
  return feishuClientPromise;
}

// 历史：本文件曾用 feishuApi / feishuApiMultipart / getTenantToken 直调
// /open-apis/。改造后所有飞书 IO 通过 ./feishu_sdk_adapter.mjs（其内部委托给 lark-cli）。

async function ensureDirs() {
  await fs.mkdir(jobDir, { recursive: true });
  await fs.mkdir(rawDir, { recursive: true });
  await fs.mkdir(deliverablesDir, { recursive: true });
}

async function writeJob(patch) {
  await ensureDirs();
  let current = {};
  try {
    current = JSON.parse(await fs.readFile(jobPath, 'utf8'));
  } catch {}
  const next = { ...current, ...patch, updatedAt: new Date().toISOString() };
  await fs.writeFile(jobPath, `${JSON.stringify(next, null, 2)}\n`, 'utf8');
  await appendTraceLog('job_write', { patch, state: next.state ?? null, currentStage: next.currentStage ?? null, lastMessage: next.lastMessage ?? null });
  return next;
}

// getTenantToken 已移除：lark-cli 自管 tenant_access_token，调用方无需手动获取。

export function buildFeishuMessageTarget(currentPayload = payload) {
  if (typeof currentPayload.chatId === 'string' && currentPayload.chatId.startsWith('user:')) {
    const openId = currentPayload.chatId.slice('user:'.length).trim();
    if (openId) {
      return {
        receiveId: openId,
        receiveIdType: 'open_id',
      };
    }
  }

  const chatType = typeof currentPayload.chatType === 'string'
    ? currentPayload.chatType.trim().toLowerCase()
    : '';
  if (chatType && chatType !== 'direct' && chatType !== 'p2p') {
    if (typeof currentPayload.chatId === 'string' && currentPayload.chatId.trim()) {
      return {
        receiveId: currentPayload.chatId.trim(),
        receiveIdType: 'chat_id',
      };
    }
  }

  if (typeof currentPayload.senderOpenId === 'string' && currentPayload.senderOpenId.trim()) {
    return {
      receiveId: currentPayload.senderOpenId.trim(),
      receiveIdType: 'open_id',
    };
  }

  if (typeof currentPayload.chatId === 'string' && currentPayload.chatId.trim()) {
    return {
      receiveId: currentPayload.chatId.trim(),
      receiveIdType: 'chat_id',
    };
  }

  return null;
}

export function buildStartupProgressMessages(inputCount, mediaType = null) {
  const safeCount = Number.isFinite(inputCount) && inputCount > 0 ? inputCount : 0;
  const mediaLabel = mediaType === 'audio'
    ? '音频'
    : mediaType === 'video'
      ? '视频'
      : '媒体';
  return [
    '已收到任务，正在启动媒体任务...',
    `任务已启动，共 ${safeCount} 个${mediaLabel}`,
  ];
}

export function buildTaskCreatePayload(job, currentPayload = payload) {
  const members = [];
  if (typeof currentPayload.senderOpenId === 'string' && currentPayload.senderOpenId.trim()) {
    members.push({
      id: currentPayload.senderOpenId.trim(),
      type: 'open_id',
      role: 'assignee',
    });
  }

  return {
    data: {
      summary: buildTaskSummary(job),
      description: buildTaskDescription(job),
      extra: buildTaskExtra(job),
      ...(members.length > 0 ? { members } : {}),
    },
  };
}

export function normalizeCreatedTask(data = {}) {
  const task = data?.task ?? data?.data?.task ?? data?.data ?? {};
  return {
    guid: task.guid ?? task.task_guid ?? task.taskGuid ?? null,
    url: task.url ?? task.task_url ?? task.taskUrl ?? null,
    id: task.id ?? task.task_id ?? task.taskId ?? null,
  };
}

async function ensureFeishuTaskForJob(job) {
  if (payload.taskGuid || !payload.senderOpenId) {
    return job;
  }

  try {
    const client = await getFeishuClient();
    const response = await createTaskWithSdk(client, buildTaskCreatePayload(job));
    const createdTask = normalizeCreatedTask(response);
    if (!createdTask.guid) {
      throw new Error('Feishu task create succeeded but no guid returned');
    }

    payload.taskGuid = createdTask.guid;
    payload.taskUrl = createdTask.url;
    payload.taskId = createdTask.id;
    await appendTraceLog('task_create_ok', {
      taskGuid: payload.taskGuid,
      taskUrl: payload.taskUrl ?? null,
      taskId: payload.taskId ?? null,
      senderOpenId: payload.senderOpenId,
    });
    return await writeJob({
      taskGuid: payload.taskGuid,
      taskUrl: payload.taskUrl ?? null,
      taskId: payload.taskId ?? null,
      taskStatus: 'running',
      taskSyncError: null,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    await appendTraceLog('task_create_failed', {
      error: message,
      senderOpenId: payload.senderOpenId ?? null,
    });
    return await writeJob({ taskCreateError: message });
  }
}

function resolveProgressTarget() {
  if (payload.taskGuid) return 'task';
  if (buildFeishuMessageTarget()) return 'feishu';
  return 'none';
}

async function sendFeishu(text) {
  const target = buildFeishuMessageTarget();
  if (!target?.receiveId) {
    return;
  }
  // lark-cli +messages-send 用 --chat-id（chat_id 类型）或 --user-id（open_id 类型）。
  // 老 receiveIdType 取值集合：chat_id / open_id（参见 buildFeishuMessageTarget）。
  if (target.receiveIdType === 'chat_id') {
    await sendImMessage({ chatId: target.receiveId, text });
  } else if (target.receiveIdType === 'open_id') {
    await sendImMessage({ userId: target.receiveId, text });
  } else {
    // 兜底：未知的 receiveIdType 不发送，避免错路由
    await appendTraceLog('progress_skip_unknown_receive_type', { receiveIdType: target.receiveIdType });
  }
}

async function sendProgress(text, patch = {}) {
  await appendTraceLog('progress_emit', { text, patch });
  const next = await writeJob({ ...patch, lastMessage: text });
  if (payload.taskGuid) {
    await syncTaskFromJob(next);
    return;
  }
  const target = buildFeishuMessageTarget();
  await appendTraceLog('progress_target', {
    target: target?.receiveIdType ?? 'none',
    receiveIdPresent: Boolean(target?.receiveId),
    chatId: payload.chatId ?? null,
    senderOpenId: payload.senderOpenId ?? null,
  });
  try {
    await sendFeishu(`[${payload.jobId}] ${text}`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    await appendTraceLog('progress_delivery_failed', message);
    await writeErrorLog(jobDir, payload.jobId, 'progress_delivery', message);
  }
}

export function buildTaskSummary(job) {
  const state = String(job.state || 'running');
  const stage = String(job.currentStage || 'idle');
  const results = Array.isArray(job.results) ? job.results : [];
  const titles = results.map((item) => item.title).filter(Boolean);
  const titleHint = titles.length > 0 ? ` | ${titles.slice(0, 2).join('、')}${titles.length > 2 ? '等' : ''}` : '';
  const stageLabelMap = {
    queued: '已排队',
    starting: '启动中',
    resolve: '解析链接中',
    download: '下载中',
    download_done: '下载完成',
    extract_audio: '提取音频中',
    transcribe: '转写中',
    transcribe_done: '转写完成',
    highlight: '提取爆款精华中',
    highlight_done: '精华提取完成',
    highlight_failed: '精华提取失败',
    upload: '上传中',
    upload_done: '上传完成',
  };
  if (state === 'completed') {
    return `视频转写任务 ${payload.jobId}（已完成）${titleHint}`;
  }
  if (state === 'completed_with_errors') {
    return `视频转写任务 ${payload.jobId}（部分完成）${titleHint}`;
  }
  if (state === 'failed') {
    return `视频转写任务 ${payload.jobId}（失败）${titleHint}`;
  }
  const stageLabel = stageLabelMap[stage] || '处理中';
  return `视频转写任务 ${payload.jobId}（${stageLabel}）${titleHint}`;
}

function buildTaskExtra(job) {
  return JSON.stringify({
    kind: 'video_transcription',
    job_id: payload.jobId,
    account_id: payload.accountId,
    mode: payload.mode,
    workflow_status: String(job.taskStatus || job.state || 'running'),
    current_stage: String(job.currentStage || 'idle'),
    current_index: job.currentIndex ?? null,
    input_count: Array.isArray(payload.inputs) ? payload.inputs.length : 0,
    latest_message: String(job.lastMessage || ''),
    task_guid: payload.taskGuid,
    task_url: payload.taskUrl,
    artifact_folder_url: job.artifactFolderUrl,
    artifact_upload_error: job.artifactUploadError,
    results: Array.isArray(job.results)
      ? job.results.map((item) => ({
          input: item.input,
          platform: item.platform,
          error: item.error,
          transcript_path: item.transcriptPath,
          polished_transcript_path: item.polishedTranscriptPath,
          rewrite_path: item.rewritePath,
          selected_polished_model_id: item.selectedPolishedModelId,
          selected_rewrite_model_id: item.selectedRewriteModelId,
          failure_reasons: item.failureReasons,
          polished_variants: item.polishedVariants,
          rewrite_variants: item.rewriteVariants,
          doc_url: item.doc?.url,
        }))
      : [],
  });
}

function buildTaskDescription(job) {
  const results = Array.isArray(job.results) ? job.results : [];
  const successCount = results.filter((item) => !item.error).length;
  const failCount = results.length - successCount;
  const lines = [
    `任务ID: ${payload.jobId}`,
    `状态: ${String(job.taskStatus || job.state || 'running')}`,
    `模式: ${payload.mode === 'hq' ? 'high-quality / medium' : 'fast / turbo'}`,
    `当前阶段: ${String(job.currentStage || 'idle')}`,
    `最近进度: ${String(job.lastMessage || '无')}`,
    `视频数量: ${Array.isArray(payload.inputs) ? payload.inputs.length : 0}`,
    `已完成: ${successCount}`,
    `失败: ${failCount}`,
    `查询命令: /video-status ${payload.jobId}`,
  ];
  const inputLines = (Array.isArray(payload.inputs) ? payload.inputs : [])
    .slice(0, 10)
    .map((url, index) => {
      const title = results[index]?.title;
      return title ? `${index + 1}. ${title} | ${url}` : `${index + 1}. ${url}`;
    });
  if (inputLines.length > 0) {
    lines.push('原始链接:', ...inputLines);
  }
  const docLines = results
    .filter((item) => item.doc?.url)
    .slice(0, 10)
    .map((item, index) => `${index + 1}. ${item.doc.url}`);
  if (docLines.length > 0) {
    lines.push('结果文档:', ...docLines);
  }
  const failedLines = results
    .filter((item) => item.error)
    .slice(0, 5)
    .map((item, index) => `${index + 1}. ${item.input} | ${item.error}`);
  if (failedLines.length > 0) {
    lines.push('失败项:', ...failedLines);
  }
  if (job.artifactFolderUrl) {
    lines.push(`产物目录: ${job.artifactFolderUrl}`);
  }
  if (job.artifactUploadError) {
    lines.push(`产物上传异常: ${job.artifactUploadError}`);
  }
  return lines.join('\n');
}

async function getRootFolderToken() {
  // 老逻辑：调用 /open-apis/drive/explorer/v2/root_folder/meta 取根目录 token。
  // 新逻辑：lark-cli 的 +create-folder / +upload 接受 --folder-token 缺省（即根目录），
  // 调用方传 '0' 同样会被新 helper 视作根目录。这里返回 '0' 即可。
  return '0';
}

async function createDriveFolder(name, folderToken) {
  const client = await getFeishuClient();
  const folder = await createDriveFolderWithSdk(client, name, folderToken);
  if (!folder?.token) {
    throw new Error('Feishu drive folder create succeeded but no token returned');
  }
  return folder;
}

async function setOrgReadablePermission(token, type) {
  const client = await getFeishuClient();
  return setOrgReadablePermissionWithSdk(client, token, type);
}

async function listDriveFolder(folderToken) {
  const client = await getFeishuClient();
  return listDriveFolderWithSdk(client, folderToken);
}

async function getUploadedFileInfo(folderToken, fileToken) {
  const client = await getFeishuClient();
  return getUploadedFileInfoWithSdk(client, folderToken, fileToken);
}

// 老逻辑里手写 adler32 + multipart upload_all / upload_prepare / upload_part / upload_finish；
// 改造后由 lark-cli +upload 统一处理（自动选简单上传或分片上传，阈值见其文档）。
async function uploadDriveFile(parentNode, filePath, fileName = path.basename(filePath)) {
  const stat = await fs.stat(filePath);
  const res = await uploadDriveFileWithCli({
    filePath,
    parentFolderToken: parentNode,
    name: fileName,
  });
  const fileToken = res?.file_token || res?.token || res?.data?.file_token;
  if (!fileToken) {
    throw new Error(`Feishu drive upload returned no file_token for ${fileName}`);
  }
  const url = res?.url || res?.data?.url || `${docBase}/file/${fileToken}`;
  return {
    fileToken,
    name: fileName,
    url,
    size: stat.size,
  };
}

async function packageJobDirectory() {
  const tempZipPath = path.join(jobsDir, `${payload.jobId}.uploading.zip`);
  const finalZipPath = path.join(jobDir, `${payload.jobId}.zip`);
  try {
    await fs.unlink(tempZipPath);
  } catch {}
  try {
    await fs.unlink(finalZipPath);
  } catch {}

  // 只打包业务产物与元数据，排除 raw/ 原始媒体（动辄几十 MB）和历史 zip 自身，
  // 避免分块上传走 large-file 流程，缩短上传耗时并降低 fetch failed 几率
  const includeEntries = [];
  for (const entry of ['deliverables', 'job.json', 'trace.log']) {
    try {
      await fs.access(path.join(jobDir, entry));
      includeEntries.push(`${payload.jobId}/${entry}`);
    } catch {}
  }
  if (includeEntries.length === 0) {
    throw new Error(`No deliverables or metadata to package for job ${payload.jobId}`);
  }

  await new Promise((resolve, reject) => {
    const child = spawn('zip', ['-qry', tempZipPath, ...includeEntries], {
      cwd: jobsDir,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stderr = '';
    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString();
    });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code !== 0) {
        reject(new Error(stderr.trim() || `zip exited with code ${code}`));
        return;
      }
      resolve();
    });
  });

  await fs.rename(tempZipPath, finalZipPath);
  return finalZipPath;
}

async function uploadJobArtifacts(job) {
  const rootFolderToken = await getRootFolderToken();
  const folder = await createDriveFolder(payload.jobId, rootFolderToken);
  const folderPermission = await setOrgReadablePermission(folder.token, 'folder');
  let folderMemberPermissionError = null;
  if (payload.senderOpenId) {
    try {
      const client = await getFeishuClient();
      await addMemberPermission(client, folder.token, 'folder', payload.senderOpenId, 'view');
    } catch (error) {
      folderMemberPermissionError = error instanceof Error ? error.message : String(error);
    }
  }
  const uploads = [];
  const zipPath = await packageJobDirectory();
  uploads.push(await uploadDriveFile(folder.token, zipPath, path.basename(zipPath)));

  const results = Array.isArray(job.results) ? job.results : [];
  for (const item of results) {
    if (!item?.transcriptPath) {
      continue;
    }
    try {
      uploads.push(await uploadDriveFile(folder.token, item.transcriptPath, path.basename(item.transcriptPath)));
    } catch (error) {
      uploads.push({
        name: path.basename(item.transcriptPath),
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  return {
    artifactFolderToken: folder.token,
    artifactFolderUrl: folder.url,
    ...(folderPermission?.skipped ? { artifactFolderPermissionWarning: folderPermission.reason } : {}),
    ...(folderMemberPermissionError ? { artifactFolderMemberPermissionError: folderMemberPermissionError } : {}),
    artifactUploads: uploads,
    artifactZipPath: zipPath,
  };
}

async function patchTask(update) {
  if (!payload.taskGuid) {
    return;
  }
  const task = {};
  const updateFields = [];
  if (update.summary !== undefined) {
    task.summary = update.summary;
    updateFields.push('summary');
  }
  if (update.description !== undefined) {
    task.description = update.description;
    updateFields.push('description');
  }
  if (update.extra !== undefined) {
    task.extra = update.extra;
    updateFields.push('extra');
  }
  if (update.completedAt !== undefined) {
    task.completed_at = update.completedAt;
    updateFields.push('completed_at');
  }
  if (updateFields.length === 0) {
    return;
  }
  const client = await getFeishuClient();
  await patchTaskWithSdk(client, payload.taskGuid, {
    data: task,
    params: {
      update_fields: updateFields,
    },
  });
}

async function syncTaskFromJob(job, options = {}) {
  if (!payload.taskGuid) {
    return job;
  }
  const completedAt = Object.prototype.hasOwnProperty.call(options, 'completedAt')
    ? options.completedAt
    : undefined;
  try {
    await patchTask({
      summary: buildTaskSummary(job),
      description: buildTaskDescription(job),
      extra: buildTaskExtra(job),
      completedAt,
    });
    return await writeJob({ taskSyncError: null });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return writeJob({ taskSyncError: message });
  }
}

export function parsePipelineSuccessLine(text) {
  if (text.startsWith('✅ 下载:')) {
    const videoPath = text.replace(/^✅ 下载:\s*/, '').trim();
    return videoPath ? { videoPath } : null;
  }
  if (text.startsWith('✅ 转写:')) {
    const transcriptPath = text.replace(/^✅ 转写:\s*/, '').trim();
    return transcriptPath ? { transcriptPath } : null;
  }
  if (text.startsWith('✅ 清洗稿:')) {
    const polishedTranscriptPath = text.replace(/^✅ 清洗稿:\s*/, '').trim();
    return polishedTranscriptPath ? { polishedTranscriptPath } : null;
  }
  return null;
}

export function consumePipelineLine(item, line, mode) {
  const text = line.trim();
  if (!text) {
    return null;
  }

  item.rawLines.push(text);

  const pipelineSuccess = parsePipelineSuccessLine(text);
  if (text.startsWith('平台:')) {
    item.platform = text.replace(/^平台:\s*/, '');
    return null;
  }
  if (text.startsWith('标题:')) {
    item.title = text.replace(/^标题:\s*/, '');
    return null;
  }
  if (text.includes('解析短链')) {
    return { message: '解析分享链接...', patch: { currentStage: 'resolve' } };
  }
  if (text.includes('下载中')) {
    return { message: `开始下载 (${item.platform || 'auto'})`, patch: { currentStage: 'download' } };
  }
  if (pipelineSuccess?.videoPath) {
    item.videoPath = pipelineSuccess.videoPath;
    return { message: `下载完成: ${item.videoPath}`, patch: { currentStage: 'download_done' } };
  }
  if (text.includes('提取音频') || text.includes('转换音频格式')) {
    return { message: '提取音频中...', patch: { currentStage: 'extract_audio' } };
  }
  if (text.includes('转写中')) {
    return {
      message: `开始转写: ${mode === 'hq' ? '高质量模式（base + 并行/校对）' : '快速模式'}`,
      patch: { currentStage: 'transcribe' },
    };
  }
  if (/^✓\s+\d+\/\d+\s+片完成$/.test(text)) {
    return { message: `分片进度: ${text.replace(/^✓\s*/, '')}`, patch: { currentStage: 'transcribe' } };
  }
  if (text.includes('LLM 校对中')) {
    return { message: '转写后校对中...', patch: { currentStage: 'proofread' } };
  }
  if (pipelineSuccess?.transcriptPath) {
    item.transcriptPath = pipelineSuccess.transcriptPath;
    return { message: `转写完成: ${item.transcriptPath}`, patch: { currentStage: 'transcribe_done' } };
  }
  if (pipelineSuccess?.polishedTranscriptPath) {
    item.polishedTranscriptPath = pipelineSuccess.polishedTranscriptPath;
    return { message: `清洗稿已生成: ${item.polishedTranscriptPath}`, patch: { currentStage: 'polish_done' } };
  }
  if (text.startsWith('⚠️')) {
    return { message: text, patch: { currentStage: 'warning' } };
  }
  return null;
}

export function buildPipelineArgs(input, outputDir = jobDir) {
  return [VIDEO_PIPELINE_SCRIPT, '--output', outputDir, input];
}

async function runPipelineForInput(input, mode, onProgress) {
  const args = buildPipelineArgs(input);
  const item = { input, mode, rawLines: [] };
  await appendTraceLog('pipeline_start', { input, mode, pythonBin: PYTHON_BIN, args });

  await new Promise((resolve, reject) => {
    const child = spawn(PYTHON_BIN, args, {
      cwd: jobDir,
      stdio: ['ignore', 'pipe', 'pipe'],
      // 关闭 Python stdout 的全缓冲，让 print 实时回到 worker，
      // 避免下载/转写阶段的进度全堆到进程退出时才一次性 flush
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
    });

    let stdoutBuffer = '';
    let stderrBuffer = '';

    const consumeLine = async (line) => {
      await appendTraceLog('pipeline_line', line);
      const progress = consumePipelineLine(item, line, mode);
      if (progress) {
        await onProgress(progress.message, progress.patch);
      }
    };

    const flushBuffer = async (buffer, isFinal = false) => {
      const parts = buffer.split(/\r?\n/);
      const remain = isFinal ? '' : parts.pop() ?? '';
      for (const line of parts) {
        // eslint-disable-next-line no-await-in-loop
        await consumeLine(line);
      }
      return remain;
    };

    let flushChain = Promise.resolve();

    child.stdout.on('data', (chunk) => {
      stdoutBuffer += chunk.toString();
      flushChain = flushChain.then(async () => {
        stdoutBuffer = await flushBuffer(stdoutBuffer);
      });
    });
    child.stderr.on('data', (chunk) => {
      stderrBuffer += chunk.toString();
      flushChain = flushChain.then(async () => {
        stderrBuffer = await flushBuffer(stderrBuffer);
      });
    });

    child.on('error', async (error) => {
      await appendTraceLog('pipeline_spawn_error', error instanceof Error ? error.stack || error.message : String(error));
      reject(error);
    });
    child.on('close', async (code) => {
      await flushChain;
      stdoutBuffer = await flushBuffer(stdoutBuffer, true);
      stderrBuffer = await flushBuffer(stderrBuffer, true);
      await appendTraceLog('pipeline_exit', {
        code,
        transcriptPath: item.transcriptPath ?? null,
        polishedTranscriptPath: item.polishedTranscriptPath ?? null,
        rewritePath: item.rewritePath ?? null,
      });
      if (code !== 0) {
        const tail = item.rawLines.slice(-8).join(' | ');
        await appendTraceLog('pipeline_nonzero_exit', tail || `video-pipeline exited with code ${code}`);
        reject(new Error(tail || `video-pipeline exited with code ${code}`));
        return;
      }
      const pipelineResult = await loadPipelineResult(jobDir);
      await appendTraceLog('pipeline_result_loaded', {
        hasResult: Boolean(pipelineResult),
        resultKeys: pipelineResult && typeof pipelineResult === 'object' ? Object.keys(pipelineResult) : [],
      });
      mergePipelineResult(item, pipelineResult);
      if (!item.transcriptPath) {
        const rawEntries = await fs.readdir(rawDir).catch(() => []);
        await appendTraceLog('pipeline_missing_transcript', {
          rawEntries,
          transcriptPath: item.transcriptPath ?? null,
          polishedTranscriptPath: item.polishedTranscriptPath ?? null,
          rewritePath: item.rewritePath ?? null,
        });
        reject(new Error('video-pipeline 已结束，但未识别到转写结果文件'));
        return;
      }
      await appendTraceLog('pipeline_completed', {
        transcriptPath: item.transcriptPath,
        polishedTranscriptPath: item.polishedTranscriptPath ?? null,
      });
      resolve();
    });
  });

  return item;
}

async function uploadContentDoc(title, markdown) {
  const client = await getFeishuClient();
  // lark-cli docs +create --api-version v2 --markdown 一次完成「创建 docx + 写入 markdown 内容」。
  // 老代码分两步走（先 createDoc，再 blocks/convert + descendant insert），这里合并以减少飞书 API 往返。
  const created = await createDoc(client, title);
  const docId = created.documentId;

  await setOrgReadablePermission(docId, 'docx');

  if (payload.senderOpenId) {
    try {
      await addMemberPermission(client, docId, 'docx', payload.senderOpenId, 'edit');
    } catch {}
  }

  if (typeof markdown === 'string' && markdown.length > 0) {
    await writeMarkdownToDocx({ docId, markdown, mode: 'append' });
  }

  return {
    documentId: docId,
    url: `${docBase}/docx/${docId}`,
    title,
  };
}

function isFeishuDocPermissionError(message) {
  return /docx:document|docx:document:create|Access denied/i.test(message);
}

async function writeLocalDeliverableSummary(results, finalJob) {
  const summaryMarkdown = buildLocalDeliverableSummaryMarkdown(results, finalJob, { jobId: payload.jobId });
  const resultsPayload = buildResultsPayload(results, finalJob, { jobId: payload.jobId });

  await fs.writeFile(path.join(deliverablesDir, 'summary.md'), `${summaryMarkdown}\n`, 'utf8');
  await fs.writeFile(path.join(deliverablesDir, 'results.json'), `${JSON.stringify(resultsPayload, null, 2)}\n`, 'utf8');
}

export function buildLocalDeliverableSummaryMarkdown(results, finalJob, options = {}) {
  const lines = [
    '# 视频任务本地结果',
    '',
    `- 任务ID: ${options.jobId || payload.jobId}`,
    `- 状态: ${finalJob.state}`,
    `- 完成时间: ${finalJob.completedAt || new Date().toISOString()}`,
    `- 产物目录: ${finalJob.artifactFolderUrl || '(未上传)'}`,
    `- 产物上传异常: ${finalJob.artifactUploadError || '(无)'}`,
    '',
    '## 结果概览',
    '',
  ];

  for (const [index, item] of results.entries()) {
    lines.push(`### ${index + 1}. ${item.title || item.input}`);
    lines.push(`- 输入: ${item.input}`);
    if (item.platform) lines.push(`- 平台: ${item.platform}`);
    if (item.videoPath) lines.push(`- 原始视频/音频: ${item.videoPath}`);
    if (item.transcriptPath) lines.push(`- 原始转写: ${item.transcriptPath}`);
    if (item.polishedTranscriptPath) lines.push(`- 清洗稿: ${item.polishedTranscriptPath}`);
    if (item.rewritePath) lines.push(`- 默认改写稿: ${item.rewritePath}`);
    if (item.selectedPolishedModelId) lines.push(`- 选中润色模型: ${item.selectedPolishedModelId}`);
    if (item.selectedRewriteModelId) lines.push(`- 选中改写模型: ${item.selectedRewriteModelId}`);
    if (item.failureReasons) lines.push(`- 失败原因: ${JSON.stringify(item.failureReasons)}`);
    if (item.polishedVariants?.length) lines.push(`- 润色变体: ${JSON.stringify(item.polishedVariants)}`);
    if (item.rewriteVariants?.length) lines.push(`- 改写变体: ${JSON.stringify(item.rewriteVariants)}`);
    if (item.doc?.url) lines.push(`- 飞书文档: ${item.doc.url}`);
    if (item.highlightDoc?.url) lines.push(`- 爆款精华文档: ${item.highlightDoc.url}`);
    if (item.highlightError) lines.push(`- 爆款精华提取异常: ${item.highlightError}`);
    if (item.subTaskId) lines.push(`- 子任务ID: ${item.subTaskId}`);
    if (item.error) lines.push(`- 错误: ${item.error}`);
    if (item.uploadError) lines.push(`- 上传异常: ${item.uploadError}`);
    lines.push('');
  }

  return lines.join('\n');
}

export function buildResultsPayload(results, finalJob, options = {}) {
  return {
    jobId: options.jobId || payload.jobId,
    state: finalJob.state,
    completedAt: finalJob.completedAt,
    artifactFolderUrl: finalJob.artifactFolderUrl,
    artifactUploadError: finalJob.artifactUploadError,
    results,
  };
}

export function buildCompletionLine(item, index) {
  const subTaskLabel = item.subTaskId ? `[${item.subTaskId}] ` : '';
  const labelPrefix = `${index + 1}. ${subTaskLabel}`.trimEnd();
  if (item.error) {
    return `${labelPrefix} 失败 | ${item.input} | ${item.error}`;
  }

  return `${labelPrefix} 完成 | ${item.input} | ${item.doc?.url || item.transcriptPath}`;
}

export function buildCompletionMessage(results, finalJob, options = {}) {
  const lines = results.map((item, index) => buildCompletionLine(item, index));
  const highlightDocUrl = results.find((item) => item?.highlightDoc?.url)?.highlightDoc?.url || null;
  const highlightError = !highlightDocUrl
    ? results.find((item) => item?.highlightError)?.highlightError || null
    : null;
  const extraLines = [
    highlightDocUrl ? `爆款精华文档: ${highlightDocUrl}\n下一步：发送 "/rw ${highlightDocUrl}" 触发多模型改写` : null,
    highlightError ? `爆款精华提取失败: ${highlightError.split('\n').pop()?.trim() || highlightError}` : null,
    finalJob.artifactFolderUrl ? `产物目录: ${finalJob.artifactFolderUrl}` : null,
    finalJob.artifactFolderMemberPermissionError ? `产物目录授权异常: ${finalJob.artifactFolderMemberPermissionError}` : null,

    finalJob.artifactUploadError ? `产物上传异常: ${finalJob.artifactUploadError}` : null,
  ].filter(Boolean);

  const success = results.filter((item) => !item.error).length;
  const fail = results.length - success;
  return `[${options.jobId || payload.jobId}] 全部完成。成功 ${success}，失败 ${fail}\n${lines.join('\n')}${extraLines.length ? `\n${extraLines.join('\n')}` : ''}`;
}

export async function finalizeFailedJobArtifacts(failedJob, options = {}) {
  const uploadArtifacts = options.uploadArtifacts ?? uploadJobArtifacts;
  const writeJobPatch = options.writeJobPatch ?? writeJob;
  const writeLocalSummary = options.writeLocalSummary ?? writeLocalDeliverableSummary;

  let finalJob = failedJob;
  try {
    const artifactData = await uploadArtifacts(finalJob);
    finalJob = await writeJobPatch(artifactData);
  } catch (artifactError) {
    finalJob = await writeJobPatch({
      artifactUploadError: artifactError instanceof Error ? artifactError.message : String(artifactError),
    });
  }

  await writeLocalSummary(Array.isArray(finalJob.results) ? finalJob.results : [], finalJob);
  return finalJob;
}

export function buildHighlightPrompt({ items, jobId } = {}) {
  const safeItems = Array.isArray(items) ? items : [];
  const blocks = safeItems.map((item, idx) => {
    const subTaskId = item.subTaskId || `${jobId || 'ASR'}_${idx + 1}`;
    const transcript = item.polishedTranscriptText || item.transcriptText || '(空)';
    const title = item.title || '(无标题)';
    const platform = item.platform || '(未知平台)';
    return [
      `### 子任务 ${subTaskId}`,
      `- 来源链接: ${item.input}`,
      `- 标题: ${title}`,
      `- 平台: ${platform}`,
      '',
      '【转写稿】',
      transcript,
    ].join('\n');
  }).join('\n\n---\n\n');

  return [
    '你是资深短视频/新媒体内容分析师，任务是从以下若干抖音作品转写稿中，提取共性的"爆款精华"。',
    '请聚焦三个维度：',
    '1. 叙事角度：每条内容采用了怎样的切入视角、叙述顺序与信息层次？',
    '2. 写作风格：语言节奏、情绪色彩、句式特征、关键词倾向。',
    '3. 爆款原因：为什么这些内容容易吸引点击、停留与互动？给出可复用的结构化总结。',
    '',
    '输出要求：',
    '- 直接输出 Markdown 文档，不要任何前后缀、代码块、解释。',
    '- 文档结构包含：## 概览 / ## 叙事角度 / ## 写作风格 / ## 爆款原因 / ## 可复用的内容模板。',
    '- 在"叙事角度""写作风格"段落中，分别对每个子任务（按上面给出的子任务 ID）给出 2-4 条要点。',
    '- "爆款原因"和"可复用的内容模板"以总结性 bullet 输出，不必逐条点名子任务。',
    '',
    '---',
    '',
    blocks,
  ].join('\n');
}

export async function runHighlightStage({
  jobId,
  items,
  runCodexCliImpl = runCodexCli,
  codexCliPath = getDefaultCodexCliPath(),
  uploadDoc = uploadContentDoc,
  writeMarkdown = async (filePath, content) => {
    await fs.mkdir(path.dirname(filePath), { recursive: true });
    await fs.writeFile(filePath, content, 'utf8');
  },
  deliverablesRoot = deliverablesDir,
  model = HIGHLIGHT_MODEL,
} = {}) {
  if (!Array.isArray(items) || items.length === 0) {
    throw new Error('runHighlightStage requires at least one transcribed item');
  }

  const prompt = buildHighlightPrompt({ items, jobId });
  await appendTraceLog('highlight_prompt_built', {
    jobId,
    itemCount: items.length,
    promptLength: prompt.length,
    model,
  });

  const markdown = await runCodexCliImpl({
    cliPath: codexCliPath,
    prompt,
    model,
    cwd: deliverablesRoot,
  });

  const markdownPath = path.join(deliverablesRoot, 'highlight.md');
  await writeMarkdown(markdownPath, markdown);

  const titleId = jobId || payload.jobId || 'asr';
  const doc = await uploadDoc(`爆款精华-${titleId}`, markdown);

  await appendTraceLog('highlight_uploaded', {
    jobId,
    markdownPath,
    docUrl: doc?.url || null,
  });

  return { markdownPath, doc, markdown };
}

async function main() {
  await appendTraceLog('job_bootstrap', {
    jobId: payload.jobId,
    inputCount: Array.isArray(payload.inputs) ? payload.inputs.length : 0,
    mode: payload.mode ?? null,
    accountId: payload.accountId ?? null,
    chatId: payload.chatId ?? null,
    senderOpenId: payload.senderOpenId ?? null,
    progressTarget: resolveProgressTarget(),
    taskGuid: payload.taskGuid ?? null,
  });
  await cleanupExpiredJobs(jobsDir);
  const results = [];
  await writeJob({
    id: payload.jobId,
    jobDir,
    jobPath,
    mode: payload.mode,
    state: 'running',
    taskGuid: payload.taskGuid,
    taskUrl: payload.taskUrl,
    taskId: payload.taskId,
    taskStatus: payload.taskGuid ? 'running' : 'chat_fallback',
    createdAt: new Date().toISOString(),
    inputs: payload.inputs,
    results: [],
  });
  const startupMessages = buildStartupProgressMessages(payload.inputs.length, payload.mediaType);
  await sendProgress(startupMessages[0], { currentStage: 'queued' });
  await sendProgress(startupMessages[1], { currentStage: 'starting' });
  for (let i = 0; i < payload.inputs.length; i += 1) {
    const input = payload.inputs[i];
    const label = `[${i + 1}/${payload.inputs.length}]`;
    const item = { input };
    results.push(item);
    try {
      const pipelineResult = await runPipelineForInput(input, payload.mode, async (message, patch = {}) => {
        await sendProgress(`${label} ${message}`, { currentIndex: i + 1, ...patch });
      });
      item.videoPath = pipelineResult.videoPath;
      item.transcriptPath = pipelineResult.transcriptPath;
      item.polishedTranscriptPath = pipelineResult.polishedTranscriptPath;
      item.rewritePath = pipelineResult.rewritePath;
      item.platform = pipelineResult.platform;
      item.title = pipelineResult.title;
      item.selectedPolishedModelId = pipelineResult.selectedPolishedModelId;
      item.selectedRewriteModelId = pipelineResult.selectedRewriteModelId;
      item.failureReasons = pipelineResult.failureReasons;
      item.polishedVariants = pipelineResult.polishedVariants;
      item.rewriteVariants = pipelineResult.rewriteVariants;
      const transcript = await fs.readFile(item.transcriptPath, 'utf8');
      const polishedTranscript = item.polishedTranscriptPath
        ? await fs.readFile(item.polishedTranscriptPath, 'utf8')
        : transcript;
      item.transcriptText = transcript;
      item.polishedTranscriptText = polishedTranscript;
      item.subTaskId = `${payload.jobId}_${i + 1}`;
      await sendProgress(`${label}[${item.subTaskId}] 转写完成`, { currentStage: 'transcribe_done' });
      item.preservedLocalOutputs = true;
    } catch (error) {
      const fullError = error instanceof Error ? error.message : String(error);
      item.error = fullError;
      await appendTraceLog('item_failure', { input, error: fullError });
      await writeErrorLog(jobDir, payload.jobId, `${label} pipeline_failure`, fullError);
      const briefError = fullError.split('\n').pop()?.trim() || fullError.slice(0, 80);
      await sendProgress(`${label} 处理失败: ${briefError}\n错误日志: video-jobs/${payload.jobId}/error.log`);
    }
    await writeJob({ results });
  }

  // 在所有转写完成后，聚合产生爆款精华分析文档（替代旧的多模型改写流程）。
  const transcribedResults = results.filter((item) => !item.error && (item.polishedTranscriptText || item.transcriptText));
  if (transcribedResults.length > 0) {
    await sendProgress('开始提取爆款精华', { currentStage: 'highlight' });
    try {
      const highlight = await runHighlightStage({ jobId: payload.jobId, items: transcribedResults });
      for (const item of transcribedResults) {
        item.highlightDoc = highlight.doc;
        item.highlightMarkdownPath = highlight.markdownPath;
      }
      await sendProgress(`爆款精华文档已生成: ${highlight.doc?.url || '(未上传)'}`, { currentStage: 'highlight_done' });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await appendTraceLog('highlight_failure', message);
      await writeErrorLog(jobDir, payload.jobId, 'highlight_failure', message);
      const briefError = message.split('\n').pop()?.trim() || message.slice(0, 120);
      await sendProgress(`爆款精华提取失败: ${briefError}`, { currentStage: 'highlight_failed' });
      for (const item of transcribedResults) {
        item.highlightError = message;
      }
    }
  }

  const success = results.filter((item) => !item.error).length;
  const fail = results.length - success;
  const completedAt = new Date().toISOString();
  const finalState = fail > 0 ? 'completed_with_errors' : 'completed';
  const finalTaskStatus = fail > 0 ? 'partial_success' : 'completed';
  let finalJob = await writeJob({
    state: finalState,
    taskStatus: finalTaskStatus,
    results,
    completedAt,
  });
  try {
    const artifactData = await uploadJobArtifacts(finalJob);
    finalJob = await writeJob(artifactData);
  } catch (error) {
    finalJob = await writeJob({
      artifactUploadError: error instanceof Error ? error.message : String(error),
    });
  }
  await writeLocalDeliverableSummary(results, finalJob);
  await syncTaskFromJob(finalJob, { completedAt: fail > 0 ? undefined : completedAt });
  const completionMessage = buildCompletionMessage(results, finalJob, { jobId: payload.jobId });
  await appendTraceLog('completion_message_prepare', {
    textPreview: completionMessage.slice(0, 500),
    progressTarget: resolveProgressTarget(),
  });
  try {
    await sendFeishu(completionMessage);
    await appendTraceLog('completion_message_sent', { progressTarget: resolveProgressTarget() });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    await appendTraceLog('completion_message_failed', message);
    await writeErrorLog(jobDir, payload.jobId, 'completion_message', message);
  }
}

const isDirectRun = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

  if (isDirectRun) {
  main().catch(async (error) => {
    const message = error instanceof Error ? error.message : String(error);
    await appendTraceLog('worker_fatal', error instanceof Error ? error.stack || error.message : String(error));
    await writeErrorLog(jobDir, payload.jobId, 'global_failure', message);
    const briefMessage = message.split('\n').pop()?.trim() || message.slice(0, 80);
    let failedJob = await writeJob({
      state: 'failed',
      taskStatus: 'failed',
      error: message,
      lastMessage: `任务失败: ${briefMessage}`,
      completedAt: new Date().toISOString(),
    });
    failedJob = await finalizeFailedJobArtifacts(failedJob);
    await syncTaskFromJob(failedJob);
    try {
      const extraLines = [
        failedJob.artifactFolderUrl ? `产物目录: ${failedJob.artifactFolderUrl}` : null,
        failedJob.artifactUploadError ? `产物上传异常: ${failedJob.artifactUploadError}` : null,
        `错误日志: video-jobs/${payload.jobId}/error.log`,
      ].filter(Boolean);
      await sendFeishu(`[${payload.jobId}] 任务失败: ${briefMessage}${extraLines.length ? `\n${extraLines.join('\n')}` : ''}`);
    } catch {}
    process.exitCode = 1;
  });
}
