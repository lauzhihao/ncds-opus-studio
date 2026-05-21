// lark-cli 子进程封装。本项目所有飞书 IO 都从这里出去，不直接调任何 open-apis 端点。
//
// 权威参考（修改前必读，不要靠 --help 反推）：
//   ~/.agents/skills/lark-im/references/lark-im-messages-send.md
//   ~/.agents/skills/lark-doc/references/lark-doc-create.md / lark-doc-update.md / lark-doc-fetch.md / lark-doc-xml.md / lark-doc-md.md
//   ~/.agents/skills/lark-drive/references/lark-drive-upload.md / lark-drive-create-folder.md
//   ~/.agents/skills/lark-task/references/lark-task-create.md / lark-task-update.md
//   lark-cli schema <service.resource.method>  (raw API 的类型化参数定义)
//
// 关键陷阱（被 --help 害过）：
//   - `docs +create/+update --api-version v2` 实际接受 --content / --command / --doc-format / --parent-token；
//     --help 仍打印 v1 的 --markdown / --mode / --folder-token（v1 在淘汰路径上）。
//   - `task +update / task tasks patch` 是 patch，没有 task.patch 子命令。
//   - `drive +upload --file` **拒绝绝对路径**（unsafe file path），必须 cwd-relative。
//   - 出错时 lark-cli 仍输出 JSON：validation 错误 exit=2，API runtime 错误可能 exit=0 但 `ok:false`，
//     还有 `ok:true` 时 body 里 `result:"failed"` 表示 API OK 但目标资源没改成。larkCli() 会检查前两种。

import { spawn } from 'node:child_process';
import path from 'node:path';

const LARK_CLI_BIN = process.env.LARK_CLI_BIN || 'lark-cli';

function asPlainEnv() {
  const env = { ...process.env };
  env.LARK_CLI_NO_PROXY = env.LARK_CLI_NO_PROXY || '1';
  env.NO_PROXY = env.NO_PROXY || 'localhost,127.0.0.1,.local,.feishu.cn,.larksuite.com,.larksuite.cn';
  env.no_proxy = env.no_proxy || env.NO_PROXY;
  return env;
}

export async function larkCliRaw(args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(LARK_CLI_BIN, args, {
      env: asPlainEnv(),
      cwd: options.cwd,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => { stdout += chunk.toString(); });
    child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
    child.once('error', reject);
    child.once('close', (code) => {
      resolve({ code: code ?? 0, stdout, stderr });
    });
    if (typeof options.stdin === 'string' && options.stdin.length > 0) {
      child.stdin.write(options.stdin);
    }
    child.stdin.end();
  });
}

function describeArgs(args) {
  // 取前 3 段作为错误信息抬头（不超过 "drive permission.public patch"）。
  return args.slice(0, 3).join(' ');
}

export async function larkCli(args, options = {}) {
  const { code, stdout, stderr } = await larkCliRaw(args, options);
  const trimmed = stdout.trim();

  // 优先尝试解析 JSON，无论 exit code 是多少——lark-cli 错误时仍返回 JSON 信封。
  let parsed = null;
  if (trimmed) {
    try { parsed = JSON.parse(trimmed); } catch { /* 非 JSON 输出，按字符串处理 */ }
  }

  if (code !== 0 || (parsed && typeof parsed === 'object' && parsed.ok === false)) {
    const errMsg = parsed?.error?.message
      || parsed?.error?.type
      || (stderr || stdout || '').trim().slice(-500)
      || `exit ${code}`;
    throw new Error(`lark-cli ${describeArgs(args)} failed: ${errMsg}`);
  }

  if (!trimmed) return null;
  return parsed ?? trimmed;
}

// ---------------- IM ----------------
// ref: ~/.agents/skills/lark-im/references/lark-im-messages-send.md
//   --text 发纯文本（不做 markdown 转换）
//   --markdown 转成 post 结构（headings 会被改写）
//   --content 自带 payload JSON

export async function sendImTextMessage({ chatId, userId, text, asIdentity = 'bot', idempotencyKey }) {
  if (!chatId && !userId) {
    throw new Error('sendImTextMessage requires chatId or userId');
  }
  const args = ['im', '+messages-send', '--as', asIdentity, '--text', text];
  if (chatId) args.push('--chat-id', chatId);
  if (userId) args.push('--user-id', userId);
  if (idempotencyKey) args.push('--idempotency-key', idempotencyKey);
  return larkCli(args);
}

// ---------------- docs（v2 only；v1 已弃用）----------------
// ref: ~/.agents/skills/lark-doc/references/lark-doc-{create,update,fetch,md,xml}.md
// 注意：v2 文档使用 --content（XML 默认；--doc-format markdown 开启 markdown）。
// 直接传 markdown 给 v1 风格的 --markdown 会被 v2 schema 拒绝。

function buildXmlSkeleton(title) {
  const safeTitle = String(title || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  return `<title>${safeTitle}</title>`;
}

export async function createDocxDocument({ title, folderToken, markdown, asIdentity = 'bot' }) {
  const args = ['docs', '+create', '--api-version', 'v2', '--as', asIdentity];
  if (folderToken && folderToken !== '0') args.push('--parent-token', folderToken);
  if (typeof markdown === 'string' && markdown.length > 0) {
    args.push('--doc-format', 'markdown', '--content', markdown);
  } else {
    // 没 markdown 时塞个最小 XML 骨架，<title> 会被自动提取为文档标题。
    args.push('--content', buildXmlSkeleton(title));
  }
  return larkCli(args);
}

export async function fetchDocxContent({ docId, format = 'markdown', detail, scope, asIdentity = 'bot' } = {}) {
  if (!docId) throw new Error('fetchDocxContent requires docId');
  const args = ['docs', '+fetch', '--api-version', 'v2', '--as', asIdentity, '--doc', docId];
  // 默认让 lark-cli 回传 Markdown（更适合下游 LLM 改写）；如果要 block-id 编辑则不传 format
  // 让默认 XML 出场。
  if (format) args.push('--doc-format', format);
  if (detail) args.push('--detail', detail);
  if (scope) args.push('--scope', scope);
  return larkCli(args);
}

// `mode` 在老 SDK 里是 append/overwrite/replace_range/etc，v2 lark-cli 的字段叫 --command，
// 取值集合相同（append / overwrite / str_replace / block_*），所以这里直接转发不改名。
export async function appendMarkdownToDocx({ docId, markdown, mode = 'append', asIdentity = 'bot' }) {
  if (!docId) throw new Error('appendMarkdownToDocx requires docId');
  const args = [
    'docs', '+update', '--api-version', 'v2',
    '--as', asIdentity,
    '--doc', docId,
    '--command', mode,
    '--doc-format', 'markdown',
    '--content', markdown ?? '',
  ];
  return larkCli(args);
}

// ---------------- drive ----------------
// ref: ~/.agents/skills/lark-drive/references/lark-drive-{upload,create-folder}.md

export async function createDriveFolder({ name, parentFolderToken, asIdentity = 'bot' }) {
  const args = ['drive', '+create-folder', '--as', asIdentity, '--name', name];
  if (parentFolderToken && parentFolderToken !== '0') {
    args.push('--folder-token', parentFolderToken);
  }
  return larkCli(args);
}

// drive +upload 拒绝绝对路径（"unsafe file path"），必须传相对路径并把 cwd 切到目标目录。
// 这里把绝对路径自动拆成 cwd + basename，对调用方透明。
export async function uploadFileToDrive({ filePath, parentFolderToken, name, asIdentity = 'bot' }) {
  if (!filePath) throw new Error('uploadFileToDrive requires filePath');
  const absolute = path.resolve(filePath);
  const cwd = path.dirname(absolute);
  const base = path.basename(absolute);
  const args = ['drive', '+upload', '--as', asIdentity, '--file', `./${base}`];
  if (parentFolderToken && parentFolderToken !== '0') {
    args.push('--folder-token', parentFolderToken);
  }
  if (name) args.push('--name', name);
  return larkCli(args, { cwd });
}

export async function listDriveFolder({ folderToken, asIdentity = 'bot' }) {
  // lark-cli 内的「列文件夹」走 raw API：drive files list（shortcut 没有 +file-list）。
  // 这里保持原 SDK 调用语义（按 folder_token 过滤）。
  const params = {};
  if (folderToken && folderToken !== '0') params.folder_token = folderToken;
  const args = [
    'drive', 'files', 'list',
    '--as', asIdentity,
  ];
  if (Object.keys(params).length > 0) {
    args.push('--params', JSON.stringify(params));
  }
  return larkCli(args);
}

// ref: lark-cli schema drive.permission.public.patch
//   path: token  query: type   body: full PermissionPublic 对象
export async function patchPublicPermission({ token, type, data, asIdentity = 'bot' }) {
  const params = JSON.stringify({ token, type });
  const args = [
    'drive', 'permission.public', 'patch',
    '--as', asIdentity,
    '--params', params,
    '--data', JSON.stringify(data),
    '--yes',
  ];
  return larkCli(args);
}

// ref: lark-cli schema drive.permission.members.create
//   path: token  query: type, need_notification  body: {member_type, member_id, perm}
export async function addMemberPermission({ token, type, memberId, memberType = 'openid', perm = 'edit', needNotification = false, asIdentity = 'bot' }) {
  const params = JSON.stringify({ token, type, need_notification: needNotification });
  const data = JSON.stringify({ member_type: memberType, member_id: memberId, perm });
  const args = [
    'drive', 'permission.members', 'create',
    '--as', asIdentity,
    '--params', params,
    '--data', data,
    '--yes',
  ];
  return larkCli(args);
}

// ---------------- task ----------------
// ref: ~/.agents/skills/lark-task/references/lark-task-{create,update}.md
//      lark-cli schema task.tasks.create / task.tasks.patch
// 注意：
//   - +create 接受 --data（与 SDK 调用形状一致）
//   - patch 走 `task tasks patch`（不是 task.patch！）；body 必须包 {task, update_fields}

export async function createTask({ data, asIdentity = 'bot' }) {
  const args = ['task', '+create', '--as', asIdentity, '--data', JSON.stringify(data)];
  return larkCli(args);
}

export async function patchTask({ taskGuid, taskFields, updateFields, asIdentity = 'bot' }) {
  if (!taskGuid) throw new Error('patchTask requires taskGuid');
  if (!taskFields || typeof taskFields !== 'object') {
    throw new Error('patchTask requires taskFields (object)');
  }
  if (!Array.isArray(updateFields) || updateFields.length === 0) {
    throw new Error('patchTask requires non-empty updateFields[]');
  }
  const args = [
    'task', 'tasks', 'patch',
    '--as', asIdentity,
    '--params', JSON.stringify({ task_guid: taskGuid }),
    '--data', JSON.stringify({ task: taskFields, update_fields: updateFields }),
  ];
  return larkCli(args);
}
