#!/usr/bin/env node
import { readFileSync } from 'node:fs';
import { appendFile, mkdir } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const defaultWorkspaceDir = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const supportedMediaDomains = [
  'douyin.com',
  'iesdouyin.com',
  'youtube.com',
  'youtu.be',
  'bilibili.com',
  'b23.tv',
  'xiaohongshu.com',
  'xhslink.com',
  'lnns.co',
  'listennotes.com',
  'xiaoyuzhoufm.com',
  'podcasts.apple.com',
  'ximalaya.com',
];

function isSupportedMediaUrl(value) {
  if (typeof value !== 'string' || !value.trim()) {
    return false;
  }

  try {
    const url = new URL(value.trim());
    return supportedMediaDomains.some((domain) => url.hostname === domain || url.hostname.endsWith(`.${domain}`));
  } catch {
    return false;
  }
}

function buildJobId() {
  return `vj_${Date.now()}_${crypto.randomUUID().slice(0, 8)}`;
}

function extractSupportedMediaUrls(text) {
  const urls = String(text ?? '').match(/https?:\/\/[^\s\]）)>]+/g);
  if (!urls) return [];
  const matched = [];
  for (const url of urls) {
    const cleaned = url.replace(/[.,;!?]+$/, '');
    if (isSupportedMediaUrl(cleaned) && !matched.includes(cleaned)) {
      matched.push(cleaned);
    }
  }
  return matched;
}

function normalizeMediaInputs(value) {
  if (typeof value === 'string') {
    return extractSupportedMediaUrls(value.trim());
  }

  if (Array.isArray(value)) {
    const matched = [];
    for (const item of value) {
      for (const url of normalizeMediaInputs(item)) {
        if (!matched.includes(url)) {
          matched.push(url);
        }
      }
    }
    return matched;
  }

  if (value && typeof value === 'object') {
    if (typeof value.url === 'string') {
      return normalizeMediaInputs(value.url);
    }
    if (typeof value.text === 'string') {
      return normalizeMediaInputs(value.text);
    }
  }

  return [];
}

export function parseAsrRequest(text) {
  const match = /^\s*\/asr\b([\s\S]*)$/i.exec(String(text ?? ''));
  if (!match) {
    return null;
  }

  const inputs = normalizeMediaInputs(match[1]);
  if (!inputs.length) {
    return null;
  }

  return {
    command: 'asr',
    inputs,
  };
}

export function detectBareMediaLinkRequest(text) {
  const trimmed = String(text ?? '').trim();
  if (!trimmed) return null;

  const inputs = normalizeMediaInputs(trimmed);
  if (inputs.length) {
    return { command: 'asr', inputs };
  }

  return null;
}

export function buildWorkerPayload({
  inputs,
  chatId = null,
  accountId = null,
  appId = null,
  appSecret = null,
  taskGuid = null,
  mode = null,
  channel = null,
  provider = null,
  messageId = null,
  chatType = null,
} = {}) {
  return {
    jobId: buildJobId(),
    inputs: Array.isArray(inputs) ? inputs : [],
    chatId,
    accountId,
    appId,
    appSecret,
    taskGuid,
    mode,
    channel,
    provider,
    messageId,
    chatType,
  };
}

export function normalizeAsrPayload(rawValue) {
  if (typeof rawValue === 'string') {
    const trimmed = rawValue.trim();
    if (!trimmed) {
      throw new Error('Expected JSON payload or supported media URL(s)');
    }

    try {
      return normalizeAsrPayload(JSON.parse(trimmed));
    } catch {
      const inputs = normalizeMediaInputs(trimmed);
      if (!inputs.length) {
        throw new Error('Expected JSON payload or supported media URL(s)');
      }
      return buildWorkerPayload({ inputs });
    }
  }

  if (Array.isArray(rawValue)) {
    const inputs = normalizeMediaInputs(rawValue);
    if (!inputs.length) {
      throw new Error('Expected payload array with supported media URL(s)');
    }
    return buildWorkerPayload({ inputs });
  }

  if (rawValue && typeof rawValue === 'object') {
    const inputs = normalizeMediaInputs(rawValue.inputs ?? rawValue.urls ?? rawValue.url ?? rawValue.text ?? []);
    if (!inputs.length) {
      throw new Error('Expected payload.inputs or supported media URL(s)');
    }

    return {
      ...rawValue,
      jobId: typeof rawValue.jobId === 'string' && rawValue.jobId.trim() ? rawValue.jobId : buildJobId(),
      inputs,
    };
  }

  throw new Error('Expected JSON payload or supported media URL(s)');
}

function loadOpenClawConfig() {
  try {
    return JSON.parse(readFileSync(path.join(os.homedir(), '.openclaw', 'openclaw.json'), 'utf8'));
  } catch {
    return {};
  }
}

function resolveDefaultFeishuAccountId(config = {}) {
  const feishu = config.channels?.feishu ?? {};
  if (typeof feishu.defaultAccount === 'string' && feishu.defaultAccount.trim()) {
    return feishu.defaultAccount.trim();
  }

  const accountIds = Object.keys(feishu.accounts ?? {}).filter((accountId) => accountId.trim());
  if (accountIds.length === 1) {
    return accountIds[0];
  }

  return null;
}

export function hydrateWorkerPayload(payload = {}, deps = {}) {
  const loadConfig = deps.loadConfig ?? loadOpenClawConfig;
  const hydrated = { ...payload };
  const config = loadConfig() ?? {};
  if (!hydrated.accountId) {
    hydrated.accountId = resolveDefaultFeishuAccountId(config);
  }
  const account = config.channels?.feishu?.accounts?.[hydrated.accountId] ?? null;

  if (!hydrated.appId && account?.appId) {
    hydrated.appId = account.appId;
  }
  if (!hydrated.appSecret && account?.appSecret) {
    hydrated.appSecret = account.appSecret;
  }
  if (!hydrated.senderOpenId && typeof hydrated.chatId === 'string' && hydrated.chatId.startsWith('user:')) {
    hydrated.senderOpenId = hydrated.chatId.slice('user:'.length) || null;
  }

  return hydrated;
}

function getJobTracePath(workspaceDir, jobId) {
  return path.join(workspaceDir, 'video-jobs', jobId, 'trace.log');
}

async function appendTraceLog(workspaceDir, jobId, stage, detail) {
  if (!workspaceDir || !jobId) return;
  const logPath = getJobTracePath(workspaceDir, jobId);
  await mkdir(path.dirname(logPath), { recursive: true });
  const timestamp = new Date().toISOString();
  const body = typeof detail === 'string' ? detail : JSON.stringify(detail, null, 2);
  await appendFile(logPath, `[${timestamp}] [runner] ${stage}\n${body}\n\n`, 'utf8');
}

function pipeChildOutput(child) {
  if (child.stdout) {
    child.stdout.on('data', (chunk) => {
      process.stdout.write(chunk);
    });
  }
  if (child.stderr) {
    child.stderr.on('data', (chunk) => {
      process.stderr.write(chunk);
    });
  }
}

function waitForChildExit(child) {
  return new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', (code, signal) => {
      resolve({ code: code ?? 0, signal: signal ?? null });
    });
  });
}

export async function launchAsrWorker({ payload, workspaceDir = defaultWorkspaceDir, wait = false }, deps = {}) {
  const spawnImpl = deps.spawnImpl ?? spawn;
  const workerScript = path.join(workspaceDir, 'scripts', 'video_job_worker.mjs');
  const hydratedPayload = hydrateWorkerPayload(payload, deps);
  const resolvedPython = typeof process.env.OPENCLAW_PYTHON === 'string' && process.env.OPENCLAW_PYTHON.trim()
    ? process.env.OPENCLAW_PYTHON.trim()
    : '/opt/homebrew/bin/python3';
  await appendTraceLog(workspaceDir, hydratedPayload.jobId, 'worker_spawn_prepare', {
    jobId: hydratedPayload.jobId,
    inputCount: Array.isArray(hydratedPayload.inputs) ? hydratedPayload.inputs.length : 0,
    mode: hydratedPayload.mode ?? null,
    accountId: hydratedPayload.accountId ?? null,
    chatId: hydratedPayload.chatId ?? null,
    workerScript,
  });
  const child = spawnImpl(process.execPath, [workerScript, JSON.stringify(hydratedPayload)], {
    cwd: workspaceDir,
    detached: !wait,
    env: {
      ...process.env,
      OPENCLAW_PYTHON: resolvedPython,
      OPENCLAW_WORKSPACE_DIR: workspaceDir,
    },
    stdio: wait ? ['ignore', 'pipe', 'pipe'] : 'ignore',
  });

  if (!wait && typeof child.unref === 'function') {
    child.unref();
  }

  await appendTraceLog(workspaceDir, hydratedPayload.jobId, 'worker_spawned', {
    pid: child.pid ?? null,
    detached: !wait,
    wait,
  });

  if (wait) {
    pipeChildOutput(child);
    const exit = await waitForChildExit(child);
    await appendTraceLog(workspaceDir, hydratedPayload.jobId, 'worker_exited', exit);
    if (exit.code !== 0) {
      throw new Error(`ASR worker exited with code ${exit.code}${exit.signal ? ` signal ${exit.signal}` : ''}`);
    }
  }

  return child;
}

async function main() {
  const wait = process.argv[2] === '--sync';
  const rawPayload = wait ? process.argv[3] : process.argv[2];
  if (!rawPayload) {
    throw new Error('Expected JSON payload or supported media URL(s)');
  }

  const payload = hydrateWorkerPayload(normalizeAsrPayload(rawPayload));
  await appendTraceLog(defaultWorkspaceDir, payload.jobId, 'request_normalized', {
    rawPayload,
    normalizedInputs: payload.inputs,
    accountId: payload.accountId ?? null,
    chatId: payload.chatId ?? null,
  });
  await launchAsrWorker({ payload, wait });
  process.stdout.write(`${JSON.stringify({
    status: wait ? 'completed' : 'started',
    jobId: payload.jobId,
    inputCount: Array.isArray(payload.inputs) ? payload.inputs.length : 0,
    accountId: payload.accountId ?? null,
    chatId: payload.chatId ?? null,
    senderOpenId: payload.senderOpenId ?? null,
    traceLog: getJobTracePath(defaultWorkspaceDir, payload.jobId),
  })}\n`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch(async (error) => {
    try {
      const rawPayload = process.argv[2] ?? '';
      const payload = normalizeAsrPayload(rawPayload);
      await appendTraceLog(defaultWorkspaceDir, payload.jobId, 'runner_fatal', error instanceof Error ? error.stack || error.message : String(error));
    } catch {}
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  });
}
