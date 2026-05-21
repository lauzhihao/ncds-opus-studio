#!/usr/bin/env node
import { readFileSync } from 'node:fs';
import { mkdir, appendFile } from 'node:fs/promises';
import crypto from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  addMemberPermission,
  createDoc,
  createFeishuClient,
  resolveFeishuApiBase,
  resolveFeishuDocBase,
  setOrgEditablePermission,
} from './feishu_sdk_adapter.mjs';
import { runContentRewrite } from './content_rewrite_runner.mjs';

const defaultWorkspaceDir = path.resolve(fileURLToPath(new URL('..', import.meta.url)));

export function extractDocxId(value) {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  // 兼容 https://*.feishu.cn/docx/<id>(?...) 或 https://*.larksuite.com/docx/<id>
  const match = trimmed.match(/\/docx\/([A-Za-z0-9]+)/);
  if (match) return match[1];
  // 直接传 documentId 也接受
  if (/^[A-Za-z0-9]{16,}$/.test(trimmed)) return trimmed;
  return null;
}

export function buildJobId(prefix = 'RW') {
  const stamp = Date.now().toString(36);
  const rand = crypto.randomBytes(3).toString('hex');
  return `${prefix}_${stamp}${rand}`;
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
  if (accountIds.length === 1) return accountIds[0];
  return null;
}

export function hydrateRewritePayload(rawPayload = {}, deps = {}) {
  const loadConfig = deps.loadConfig ?? loadOpenClawConfig;
  const hydrated = { ...rawPayload };
  const config = loadConfig() ?? {};
  if (!hydrated.accountId) {
    hydrated.accountId = resolveDefaultFeishuAccountId(config);
  }
  const account = config.channels?.feishu?.accounts?.[hydrated.accountId] ?? null;
  if (!hydrated.appId && account?.appId) hydrated.appId = account.appId;
  if (!hydrated.appSecret && account?.appSecret) hydrated.appSecret = account.appSecret;
  if (!hydrated.jobId) hydrated.jobId = buildJobId('RW');
  return hydrated;
}

function getTracePath(jobId, workspaceDir = defaultWorkspaceDir) {
  return path.join(workspaceDir, 'video-jobs', jobId, 'trace.log');
}

async function appendTrace(jobId, stage, detail, workspaceDir = defaultWorkspaceDir) {
  if (!jobId) return;
  const logPath = getTracePath(jobId, workspaceDir);
  await mkdir(path.dirname(logPath), { recursive: true });
  const timestamp = new Date().toISOString();
  const body = typeof detail === 'string' ? detail : JSON.stringify(detail, null, 2);
  await appendFile(logPath, `[${timestamp}] [rw-runner] ${stage}\n${body}\n\n`, 'utf8');
}

async function fetchTenantToken({ apiBase, appId, appSecret }) {
  const res = await fetch(`${apiBase}/open-apis/auth/v3/tenant_access_token/internal`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ app_id: appId, app_secret: appSecret }),
  });
  const raw = await res.text();
  let data;
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch {
    throw new Error(`Feishu auth returned non-JSON: HTTP ${res.status} ${raw.slice(0, 200)}`);
  }
  if (!res.ok || data.code !== 0 || !data.tenant_access_token) {
    throw new Error(`Feishu auth failed: ${data.msg || res.status}`);
  }
  return data.tenant_access_token;
}

function buildFeishuApi({ apiBase, token }) {
  return async function feishuApi(pathname, options = {}) {
    const headers = {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      ...(options.headers ?? {}),
    };
    const res = await fetch(`${apiBase}${pathname}`, { ...options, headers });
    const raw = await res.text();
    let data;
    try {
      data = raw ? JSON.parse(raw) : {};
    } catch {
      throw new Error(`Feishu API non-JSON: ${pathname} HTTP ${res.status} ${raw.slice(0, 200)}`);
    }
    if (!res.ok || data.code !== 0) {
      throw new Error(`Feishu API failed: ${pathname} ${data.msg || res.status}`);
    }
    return data;
  };
}

async function readDocxRawContent(feishuApi, docId) {
  const result = await feishuApi(`/open-apis/docx/v1/documents/${docId}/raw_content`, {
    method: 'GET',
  });
  const content = result?.data?.content;
  if (typeof content !== 'string' || !content.trim()) {
    throw new Error(`Docx raw_content is empty: ${docId}`);
  }
  return content;
}

async function buildDocBlocks(feishuApi, docId, markdown) {
  const converted = await feishuApi('/open-apis/docx/v1/documents/blocks/convert', {
    method: 'POST',
    body: JSON.stringify({ content_type: 'markdown', content: markdown }),
  });
  const blocks = converted.data?.blocks ?? [];
  const firstLevel = converted.data?.first_level_block_ids ?? [];
  if (blocks.length === 0) return;
  await feishuApi(`/open-apis/docx/v1/documents/${docId}/blocks/${docId}/descendant`, {
    method: 'POST',
    body: JSON.stringify({ children_id: firstLevel, descendants: blocks, index: -1 }),
  });
}

async function uploadRewriteDoc({ feishuApi, client, title, markdown, senderOpenId }) {
  const created = await createDoc(client, title);
  const docId = created.documentId;
  await setOrgEditablePermission(client, docId, 'docx');
  if (senderOpenId) {
    try {
      await addMemberPermission(client, docId, 'docx', senderOpenId, 'edit');
    } catch {}
  }
  await buildDocBlocks(feishuApi, docId, markdown);
  return { documentId: docId, title };
}

function buildFeishuMessageTarget(payload) {
  if (typeof payload.chatId === 'string' && payload.chatId.startsWith('user:')) {
    const openId = payload.chatId.slice('user:'.length).trim();
    if (openId) return { receiveId: openId, receiveIdType: 'open_id' };
  }
  const chatType = (payload.chatType || '').toString().trim().toLowerCase();
  if (chatType && chatType !== 'direct' && chatType !== 'p2p' && payload.chatId) {
    return { receiveId: payload.chatId, receiveIdType: 'chat_id' };
  }
  if (payload.senderOpenId) {
    return { receiveId: payload.senderOpenId, receiveIdType: 'open_id' };
  }
  if (payload.chatId) {
    return { receiveId: payload.chatId, receiveIdType: 'chat_id' };
  }
  return null;
}

async function sendFeishuText(feishuApi, payload, text) {
  const target = buildFeishuMessageTarget(payload);
  if (!target) return;
  await feishuApi(`/open-apis/im/v1/messages?receive_id_type=${target.receiveIdType}`, {
    method: 'POST',
    body: JSON.stringify({
      receive_id: target.receiveId,
      msg_type: 'text',
      content: JSON.stringify({ text }),
    }),
  });
}

export async function runRewriteCommand(rawPayload, deps = {}) {
  const payload = hydrateRewritePayload(rawPayload);
  if (!payload.appId || !payload.appSecret) {
    throw new Error('rewrite payload missing appId/appSecret');
  }
  const docId = extractDocxId(payload.docxUrl);
  if (!docId) {
    throw new Error(`Invalid docxUrl, cannot extract documentId: ${payload.docxUrl}`);
  }
  payload.documentId = docId;

  const apiBase = resolveFeishuApiBase(payload.domain);
  const docBase = resolveFeishuDocBase(payload.domain);
  const token = await fetchTenantToken({ apiBase, appId: payload.appId, appSecret: payload.appSecret });
  const feishuApi = buildFeishuApi({ apiBase, token });
  const client = await createFeishuClient({
    appId: payload.appId,
    appSecret: payload.appSecret,
    domain: payload.domain,
  });

  const userRequirements = typeof payload.userRequirements === 'string' ? payload.userRequirements.trim() : '';

  await appendTrace(payload.jobId, 'request_normalized', {
    docId,
    accountId: payload.accountId,
    chatId: payload.chatId,
    senderOpenId: payload.senderOpenId,
    targetProfile: payload.targetProfile || 'douyin',
    userRequirementsLength: userRequirements.length,
  });

  const sourceText = await readDocxRawContent(feishuApi, docId);
  await appendTrace(payload.jobId, 'source_loaded', { length: sourceText.length });

  // 中间汇报 1：已读取源文档，开始双模型改写
  try {
    const startMessage = userRequirements
      ? `[${payload.jobId}] /rw 已读取源文档（${sourceText.length} 字），开始 GPT-5.5 + Local Gemini 双模型改写...\n用户附加要求：${userRequirements}`
      : `[${payload.jobId}] /rw 已读取源文档（${sourceText.length} 字），开始 GPT-5.5 + Local Gemini 双模型改写...`;
    await sendFeishuText(feishuApi, payload, startMessage);
    await appendTrace(payload.jobId, 'rewrite_start_notice_sent', { textPreview: startMessage.slice(0, 200) });
  } catch (error) {
    await appendTrace(payload.jobId, 'rewrite_start_notice_failed', error instanceof Error ? error.message : String(error));
  }

  const deliverablesDir = path.join(defaultWorkspaceDir, 'video-jobs', payload.jobId, 'deliverables');
  await mkdir(deliverablesDir, { recursive: true });

  const runRewrite = deps.runContentRewriteImpl ?? runContentRewrite;
  const rewrite = await runRewrite({
    sourceText,
    deliverablesDir,
    targetProfile: payload.targetProfile || 'douyin',
    userRequirements,
  });
  await appendTrace(payload.jobId, 'rewrite_done', {
    status: rewrite.status,
    draftCount: Array.isArray(rewrite.drafts) ? rewrite.drafts.length : 0,
    errors: rewrite.errors,
  });

  // 中间汇报 2：draft 完成，开始上传飞书
  try {
    const drafts = Array.isArray(rewrite.drafts) ? rewrite.drafts : [];
    const ok = drafts.filter((d) => d.status === 'success').length;
    const fail = drafts.length - ok;
    const draftDoneMessage = `[${payload.jobId}] /rw 双模型 draft 完成（成功 ${ok}/失败 ${fail}），开始上传到飞书...`;
    await sendFeishuText(feishuApi, payload, draftDoneMessage);
    await appendTrace(payload.jobId, 'draft_done_notice_sent', { textPreview: draftDoneMessage.slice(0, 200) });
  } catch (error) {
    await appendTrace(payload.jobId, 'draft_done_notice_failed', error instanceof Error ? error.message : String(error));
  }

  const drafts = Array.isArray(rewrite.drafts) ? rewrite.drafts : [];
  const successfulDrafts = drafts.filter((draft) => draft.status === 'success' && draft.path);

  const uploadedDocs = [];
  for (const draft of successfulDrafts) {
    try {
      const content = await readFileSafe(draft.path);
      if (!content.trim()) {
        uploadedDocs.push({ modelId: draft.modelId, modelLabel: draft.modelLabel, error: 'draft empty' });
        continue;
      }
      const title = `改写稿-${shortDraftName(draft)}-${payload.jobId}`;
      const doc = await uploadRewriteDoc({
        feishuApi,
        client,
        title,
        markdown: `# 改写候选 (${draft.modelLabel || draft.modelId})\n\n${content}`,
        senderOpenId: payload.senderOpenId,
      });
      uploadedDocs.push({
        modelId: draft.modelId,
        modelLabel: draft.modelLabel,
        documentId: doc.documentId,
        title: doc.title,
        url: `${docBase}/docx/${doc.documentId}`,
      });
    } catch (error) {
      uploadedDocs.push({
        modelId: draft.modelId,
        modelLabel: draft.modelLabel,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }
  await appendTrace(payload.jobId, 'uploaded_docs', uploadedDocs);

  const successMessage = buildSuccessMessage({ jobId: payload.jobId, drafts: uploadedDocs, sourceUrl: payload.docxUrl });
  try {
    await sendFeishuText(feishuApi, payload, successMessage);
    await appendTrace(payload.jobId, 'completion_message_sent', { textPreview: successMessage.slice(0, 240) });
  } catch (error) {
    await appendTrace(payload.jobId, 'completion_message_failed', error instanceof Error ? error.message : String(error));
  }

  return {
    status: 'completed',
    jobId: payload.jobId,
    sourceDocumentId: docId,
    rewriteStatus: rewrite.status,
    drafts: uploadedDocs,
    message: successMessage,
  };
}

const DRAFT_TITLE_NAMES = {
  gemini_local: 'GEMINI3',
  gpt5: 'GPT5.5',
};

export function shortDraftName(draft) {
  return DRAFT_TITLE_NAMES[draft.modelId] || draft.modelLabel || draft.modelId;
}

export function buildSuccessMessage({ jobId, drafts, sourceUrl }) {
  // 飞书客户端会自动把 docx 链接渲染成带标题的卡片，所以正文里不再加前缀文案，
  // 改写稿的辨识度由文档标题（改写稿-GEMINI3-<jobId> 等）承担。
  const lines = [`[${jobId}] /rw 改写完成。`, `- ${sourceUrl}`];
  const succeeded = drafts.filter((d) => d.url);
  const failed = drafts.filter((d) => d.error);
  if (succeeded.length === 0) {
    lines.push('- 未生成任何可用候选稿。');
  } else {
    for (const draft of succeeded) {
      lines.push(`- ${draft.url}`);
    }
  }
  for (const draft of failed) {
    lines.push(`- ${shortDraftName(draft)} 上传失败: ${draft.error}`);
  }
  return lines.join('\n');
}

async function readFileSafe(filePath) {
  try {
    return await (await import('node:fs/promises')).readFile(filePath, 'utf8');
  } catch (error) {
    throw new Error(`failed to read draft file ${filePath}: ${error instanceof Error ? error.message : String(error)}`);
  }
}

async function main() {
  const rawPayloadStr = process.argv[2];
  if (!rawPayloadStr) {
    throw new Error('Expected JSON payload');
  }
  let parsed;
  try {
    parsed = JSON.parse(rawPayloadStr);
  } catch {
    throw new Error('Payload must be valid JSON');
  }
  const result = await runRewriteCommand(parsed);
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch(async (error) => {
    try {
      const parsed = process.argv[2] ? JSON.parse(process.argv[2]) : {};
      const hydrated = hydrateRewritePayload(parsed);
      await appendTrace(hydrated.jobId, 'rw_runner_fatal', error instanceof Error ? error.stack || error.message : String(error));
    } catch {}
    process.stderr.write(`${error.message ?? error}\n`);
    process.exitCode = 1;
  });
}
