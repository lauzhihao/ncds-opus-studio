// feishu_sdk_adapter — 仅作为兼容层存在。
//
// 本项目硬约束：不直接调用任何飞书 OpenAPI；所有飞书 IO 都走 `lark-cli` 子进程。
// 历史代码里大量调用本模块导出的函数（createDoc / createDriveFolder / addMemberPermission
// 等等），为减少调用方改动，这些导出名都保留下来，内部实现委托给 ./lark_cli.mjs。
//
// 关键变化：
//   - createFeishuClient(...) 不再创建 Lark.Client 实例；返回一个仅含 {asIdentity, domain}
//     的轻对象。后续所有 SDK 方法（client.drive.*）都不再使用——业务函数自己 spawn lark-cli。
//   - 老 SDK 调用的入参里那些 appId / appSecret 等飞书凭据**不会被使用**：lark-cli 自管账号
//     凭据（通过 lark-cli auth login）。如果需要切换 bot 身份，配置 lark-cli 而非传 client。
//
// 兼容性优先：所有函数保留原签名（含 client 参数）和返回结构。

import {
  larkCli,
  sendImTextMessage,
  createDocxDocument,
  appendMarkdownToDocx,
  fetchDocxContent,
  createDriveFolder as createDriveFolderCli,
  uploadFileToDrive,
  listDriveFolder as listDriveFolderCli,
  patchPublicPermission,
  addMemberPermission as addMemberPermissionCli,
  createTask as createTaskCli,
  patchTask as patchTaskCli,
} from './lark_cli.mjs';

const PUBLIC_PERMISSION_TYPES = new Set([
  'doc',
  'sheet',
  'file',
  'wiki',
  'bitable',
  'docx',
  'mindnote',
  'minutes',
  'slides',
]);

// ---------------- 纯函数（无副作用，方便单测） ----------------

export function resolveFeishuApiBase(domain) {
  if (domain === 'lark') {
    return 'https://open.larksuite.com';
  }
  if (typeof domain === 'string' && domain.startsWith('http')) {
    return domain.replace(/\/+$/, '');
  }
  return 'https://open.feishu.cn';
}

export function resolveFeishuDocBase(domain) {
  if (domain === 'lark') {
    return 'https://larksuite.com';
  }
  if (typeof domain === 'string' && domain.startsWith('http')) {
    return domain.replace(/\/+$/, '');
  }
  return 'https://feishu.cn';
}

export function buildPublicPermissionData() {
  return {
    external_access_entity: 'closed',
    security_entity: 'anyone_can_view',
    comment_entity: 'anyone_can_view',
    share_entity: 'same_tenant',
    link_share_entity: 'tenant_readable',
  };
}

export function buildOrgEditablePermissionData() {
  return {
    external_access_entity: 'closed',
    security_entity: 'anyone_can_edit',
    comment_entity: 'anyone_can_edit',
    share_entity: 'same_tenant',
    link_share_entity: 'tenant_editable',
  };
}

export function normalizePublicPermissionType(type) {
  return PUBLIC_PERMISSION_TYPES.has(type) ? type : null;
}

// 历史兼容：原本用于定位 @larksuiteoapi/node-sdk 入口；现在不再加载 SDK，返回空数组。
export function buildSdkEntryCandidates(_globalNodeModuleRoots = []) {
  return [];
}

// ---------------- "客户端"（实际是个无状态描述符） ----------------

export async function createFeishuClient(options = {}) {
  return {
    asIdentity: options.asIdentity || 'bot',
    domain: resolveFeishuApiBase(options.domain),
    docBase: resolveFeishuDocBase(options.domain),
    // 保留入参用于审计；lark-cli 不会用这些去鉴权。
    appId: options.appId ?? null,
    accountId: options.accountId ?? null,
  };
}

function clientIdentity(client) {
  return (client && client.asIdentity) || 'bot';
}

// ---------------- 业务函数（与老 API 同签名） ----------------

export async function getRootFolderToken(client) {
  const list = await listDriveFolderCli({ asIdentity: clientIdentity(client) });
  // 注意：lark-cli +file-list 返回当前用户/机器人的根目录内容；
  // 老 SDK 的 root_folder/meta 返回的是 root folder token 本身（不同语义）。
  // 上游 worker 调用此函数仅是为了拿到一个 fallback token，这里返回 '0' 表示「root」更稳。
  // 真实 token 可在 lark-cli +file-list 输出的 parent_token 字段里找，但这里保持原行为。
  return list?.parent_token || '0';
}

export async function createDriveFolder(client, name, folderToken) {
  const res = await createDriveFolderCli({
    name,
    parentFolderToken: folderToken,
    asIdentity: clientIdentity(client),
  });
  const token = res?.token || res?.data?.token;
  const url = res?.url || res?.data?.url;
  if (!token) {
    throw new Error('Failed to create drive folder (no token in response)');
  }
  return { token, url };
}

export async function listDriveFolder(client, folderToken) {
  const res = await listDriveFolderCli({
    folderToken,
    asIdentity: clientIdentity(client),
  });
  if (Array.isArray(res)) return res;
  if (Array.isArray(res?.files)) return res.files;
  if (Array.isArray(res?.data?.files)) return res.data.files;
  return [];
}

export async function getUploadedFileInfo(client, folderToken, fileToken) {
  const files = await listDriveFolder(client, folderToken);
  return files.find((item) => item.token === fileToken || item.file_token === fileToken) || null;
}

export async function setOrgReadablePermission(client, token, type) {
  const normalizedType = normalizePublicPermissionType(type);
  if (!normalizedType) {
    return { skipped: true, reason: `public permission unsupported for type: ${type}` };
  }
  const res = await patchPublicPermission({
    token,
    type: normalizedType,
    data: buildPublicPermissionData(),
    asIdentity: clientIdentity(client),
  });
  return { skipped: false, permission: res?.permission_public ?? res?.data?.permission_public };
}

export async function setOrgEditablePermission(client, token, type) {
  const normalizedType = normalizePublicPermissionType(type);
  if (!normalizedType) {
    return { skipped: true, reason: `public permission unsupported for type: ${type}` };
  }
  const res = await patchPublicPermission({
    token,
    type: normalizedType,
    data: buildOrgEditablePermissionData(),
    asIdentity: clientIdentity(client),
  });
  return { skipped: false, permission: res?.permission_public ?? res?.data?.permission_public };
}

export async function addMemberPermission(client, token, fileType, memberId, perm = 'edit') {
  const res = await addMemberPermissionCli({
    token,
    type: fileType,
    memberId,
    perm,
    asIdentity: clientIdentity(client),
  });
  return res?.member ?? res?.data?.member ?? null;
}

export async function createDoc(client, title, folderToken) {
  const res = await createDocxDocument({
    title,
    folderToken,
    asIdentity: clientIdentity(client),
  });
  // lark-cli 输出结构兼容多种 shape
  const documentId = res?.document?.document_id ?? res?.document_id ?? res?.data?.document?.document_id;
  if (!documentId) {
    throw new Error('Document creation succeeded but no document_id was returned');
  }
  return {
    documentId,
    title: res?.document?.title ?? res?.title ?? title,
  };
}

export async function createTask(client, payload) {
  const data = payload?.data ?? payload;
  const res = await createTaskCli({ data, asIdentity: clientIdentity(client) });
  return res?.data ?? res ?? {};
}

export async function patchTask(client, taskGuid, payload) {
  // 老 SDK 用法：patchTask(client, guid, { data: {...task fields...}, params: { update_fields: [...] } })
  // 新 helper：patchTask({ taskGuid, taskFields, updateFields })
  const taskFields = payload?.data ?? payload ?? {};
  const updateFields = Array.isArray(payload?.params?.update_fields)
    ? payload.params.update_fields
    : Array.isArray(payload?.update_fields)
      ? payload.update_fields
      : Object.keys(taskFields); // 兜底：取 task body 里出现的字段
  const res = await patchTaskCli({
    taskGuid,
    taskFields,
    updateFields,
    asIdentity: clientIdentity(client),
  });
  return res?.data ?? res ?? {};
}

// ---------------- 新增导出：worker 直调 feishuApi 的场景 ----------------
// 这些是为了让 video_job_worker.mjs 把直调改为「通过 adapter」。

export async function sendImMessage({ chatId, userId, text, asIdentity = 'bot', idempotencyKey } = {}) {
  return sendImTextMessage({ chatId, userId, text, asIdentity, idempotencyKey });
}

export async function uploadDriveFile({ filePath, parentFolderToken, name, asIdentity = 'bot' } = {}) {
  return uploadFileToDrive({ filePath, parentFolderToken, name, asIdentity });
}

export async function writeMarkdownToDocx({ docId, markdown, mode = 'append', asIdentity = 'bot' } = {}) {
  return appendMarkdownToDocx({ docId, markdown, mode, asIdentity });
}

export async function readDocxContent({ docId, asIdentity = 'bot' } = {}) {
  const res = await fetchDocxContent({ docId, asIdentity });
  // lark-cli docs +fetch v2 实测 shape：
  //   { ok, identity, data: { document: { content, document_id, revision_id }, log_id } }
  // 老版本 / v1 可能直接是 content 或 markdown，全兜底。
  const content =
    (typeof res === 'string' ? res : null) ||
    res?.data?.document?.content ||
    res?.document?.content ||
    res?.data?.content ||
    res?.data?.markdown ||
    res?.content ||
    res?.markdown ||
    '';
  return content;
}

// 透出底层 helper，便于个别场景拼装定制命令
export { larkCli };
