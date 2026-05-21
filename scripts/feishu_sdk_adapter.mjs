import { access } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const GLOBAL_NODE_MODULE_CANDIDATES = [
  process.env.OPENCLAW_GLOBAL_NODE_MODULES,
  '/opt/homebrew/lib/node_modules',
  '/usr/local/lib/node_modules',
].filter(Boolean);

const SDK_ENTRY_SUFFIXES = [
  ['lib', 'index.js'],
  ['es', 'index.js'],
];

const SDK_PACKAGE_ROOT_CANDIDATES = [
  ['openclaw', 'dist', 'extensions', 'feishu', 'node_modules', '@larksuiteoapi', 'node-sdk'],
  ['@openclaw', 'feishu', 'node_modules', '@larksuiteoapi', 'node-sdk'],
  ['openclaw', 'node_modules', '@larksuiteoapi', 'node-sdk'],
];

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

export function buildSdkEntryCandidates(globalNodeModuleRoots = GLOBAL_NODE_MODULE_CANDIDATES) {
  return [...new Set(globalNodeModuleRoots.flatMap((root) => (
    SDK_PACKAGE_ROOT_CANDIDATES.flatMap((segments) => (
      SDK_ENTRY_SUFFIXES.map((suffix) => path.join(root, ...segments, ...suffix))
    ))
  )))];
}

async function resolveSdkEntry() {
  const candidates = buildSdkEntryCandidates();

  for (const candidate of candidates) {
    try {
      await access(candidate);
      return candidate;
    } catch {}
  }

  throw new Error('Unable to locate @larksuiteoapi/node-sdk from the OpenClaw installation');
}

let larkSdkPromise;

async function loadLarkSdk() {
  if (!larkSdkPromise) {
    larkSdkPromise = resolveSdkEntry().then((entry) => import(pathToFileURL(entry).href));
  }
  return larkSdkPromise;
}

function resolveSdkDomain(Lark, domain) {
  if (domain === 'lark') {
    return Lark.Domain.Lark;
  }
  if (domain === 'feishu' || !domain) {
    return Lark.Domain.Feishu;
  }
  return domain.replace(/\/+$/, '');
}

export async function createFeishuClient(options) {
  const Lark = await loadLarkSdk();
  return new Lark.Client({
    appId: options.appId,
    appSecret: options.appSecret,
    appType: Lark.AppType.SelfBuild,
    domain: resolveSdkDomain(Lark, options.domain),
  });
}

export async function getRootFolderToken(client) {
  const domain = client.domain ?? 'https://open.feishu.cn';
  const res = await client.httpInstance.get(`${domain}/open-apis/drive/explorer/v2/root_folder/meta`);
  if (res.code !== 0) {
    throw new Error(res.msg ?? 'Failed to get root folder');
  }
  const token = res.data?.token;
  if (!token) {
    throw new Error('Root folder token not found');
  }
  return token;
}

export async function createDriveFolder(client, name, folderToken) {
  const effectiveToken = folderToken && folderToken !== '0' ? folderToken : '0';
  const res = await client.drive.file.createFolder({
    data: {
      name,
      folder_token: effectiveToken,
    },
  });
  if (res.code !== 0) {
    throw new Error(res.msg ?? 'Failed to create drive folder');
  }
  return {
    token: res.data?.token,
    url: res.data?.url,
  };
}

export async function listDriveFolder(client, folderToken) {
  const res = await client.drive.file.list({
    params: folderToken ? { folder_token: folderToken } : {},
  });
  if (res.code !== 0) {
    throw new Error(res.msg ?? 'Failed to list drive folder');
  }
  return res.data?.files || [];
}

export async function getUploadedFileInfo(client, folderToken, fileToken) {
  const files = await listDriveFolder(client, folderToken);
  return files.find((item) => item.token === fileToken) || null;
}

export async function setOrgReadablePermission(client, token, type) {
  const normalizedType = normalizePublicPermissionType(type);
  if (!normalizedType) {
    return {
      skipped: true,
      reason: `public permission unsupported for type: ${type}`,
    };
  }

  const res = await client.drive.permissionPublic.patch({
    path: { token },
    params: { type: normalizedType },
    data: buildPublicPermissionData(),
  });
  if (res.code !== 0) {
    throw new Error(res.msg ?? `Failed to update public permission for ${normalizedType}`);
  }
  return {
    skipped: false,
    permission: res.data?.permission_public,
  };
}

export async function setOrgEditablePermission(client, token, type) {
  const normalizedType = normalizePublicPermissionType(type);
  if (!normalizedType) {
    return {
      skipped: true,
      reason: `public permission unsupported for type: ${type}`,
    };
  }

  const res = await client.drive.permissionPublic.patch({
    path: { token },
    params: { type: normalizedType },
    data: buildOrgEditablePermissionData(),
  });
  if (res.code !== 0) {
    throw new Error(res.msg ?? `Failed to update editable permission for ${normalizedType}`);
  }
  return {
    skipped: false,
    permission: res.data?.permission_public,
  };
}

export async function addMemberPermission(client, token, fileType, memberId, perm = 'edit') {
  const res = await client.drive.permissionMember.create({
    path: { token },
    params: { type: fileType, need_notification: false },
    data: {
      member_type: 'openid',
      member_id: memberId,
      perm,
    },
  });
  if (res.code !== 0) {
    throw new Error(res.msg ?? 'Failed to add member permission');
  }
  return res.data?.member || null;
}

export async function createDoc(client, title, folderToken) {
  const res = await client.docx.document.create({
    data: { title, folder_token: folderToken },
  });
  if (res.code !== 0) {
    throw new Error(res.msg ?? 'Failed to create docx document');
  }
  const doc = res.data?.document;
  if (!doc?.document_id) {
    throw new Error('Document creation succeeded but no document_id was returned');
  }
  return {
    documentId: doc.document_id,
    title: doc.title,
  };
}

export async function createTask(client, payload) {
  const res = await client.task.v2.task.create(payload);
  if (res.code !== 0) {
    throw new Error(res.msg ?? 'Failed to create task');
  }
  return res.data ?? {};
}

export async function patchTask(client, taskGuid, payload) {
  const res = await client.task.v2.task.patch({
    path: { task_guid: taskGuid },
    ...payload,
  });
  if (res.code !== 0) {
    throw new Error(res.msg ?? 'Failed to update task');
  }
  return res.data ?? {};
}
