// lark-cli 子进程封装。本项目所有飞书 IO 都从这里出去，不直接调任何 open-apis 端点。
//
// 使用：
//   const result = await larkCli(['docs', '+create', '--api-version', 'v2', '--as', 'bot', '--title', 't']);
//   // result 是 lark-cli 输出（默认 JSON）解析后的对象。
//
// 输入用 stdin：
//   await larkCli(['drive', 'permission.public', 'patch', '--as', 'bot'], { stdin: jsonString });

import { spawn } from 'node:child_process';

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

export async function larkCli(args, options = {}) {
  const { code, stdout, stderr } = await larkCliRaw(args, options);
  if (code !== 0) {
    const tail = (stderr || stdout || '').trim().slice(-500);
    throw new Error(`lark-cli ${args.slice(0, 3).join(' ')} failed (exit ${code}): ${tail}`);
  }
  const trimmed = stdout.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    // 部分子命令输出非 JSON（例如纯文本），原样返回字符串
    return trimmed;
  }
}

// 业务封装：每个函数对应一个原先 feishu_sdk_adapter 里的 OpenAPI 调用。
// 这些函数明确表示「业务意图」，便于读者把握迁移前后语义不变。

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

export async function createDocxDocument({ title, folderToken, markdown, asIdentity = 'bot' }) {
  const args = ['docs', '+create', '--api-version', 'v2', '--as', asIdentity, '--title', title];
  if (folderToken) args.push('--folder-token', folderToken);
  let stdin;
  if (typeof markdown === 'string' && markdown.length > 0) {
    args.push('--markdown', '-');
    stdin = markdown;
  }
  return larkCli(args, { stdin });
}

export async function fetchDocxContent({ docId, asIdentity = 'bot' }) {
  if (!docId) throw new Error('fetchDocxContent requires docId');
  const args = ['docs', '+fetch', '--api-version', 'v2', '--as', asIdentity, '--doc', docId];
  return larkCli(args);
}

export async function appendMarkdownToDocx({ docId, markdown, mode = 'append', asIdentity = 'bot' }) {
  if (!docId) throw new Error('appendMarkdownToDocx requires docId');
  const args = [
    'docs', '+update', '--api-version', 'v2',
    '--as', asIdentity,
    '--doc', docId,
    '--mode', mode,
    '--markdown', '-',
  ];
  return larkCli(args, { stdin: markdown ?? '' });
}

export async function createDriveFolder({ name, parentFolderToken, asIdentity = 'bot' }) {
  const args = ['drive', '+create-folder', '--as', asIdentity, '--name', name];
  if (parentFolderToken && parentFolderToken !== '0') {
    args.push('--folder-token', parentFolderToken);
  }
  return larkCli(args);
}

export async function uploadFileToDrive({ filePath, parentFolderToken, name, asIdentity = 'bot' }) {
  const args = ['drive', '+upload', '--as', asIdentity, '--file', filePath];
  if (parentFolderToken && parentFolderToken !== '0') {
    args.push('--folder-token', parentFolderToken);
  }
  if (name) args.push('--name', name);
  return larkCli(args);
}

export async function listDriveFolder({ folderToken, asIdentity = 'bot' }) {
  const args = ['drive', '+file-list', '--as', asIdentity];
  if (folderToken && folderToken !== '0') {
    args.push('--folder-token', folderToken);
  }
  return larkCli(args);
}

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

export async function createTask({ data, asIdentity = 'bot' }) {
  const args = ['task', '+create', '--as', asIdentity, '--data', JSON.stringify(data)];
  return larkCli(args);
}

export async function patchTask({ taskGuid, data, asIdentity = 'bot' }) {
  if (!taskGuid) throw new Error('patchTask requires taskGuid');
  // lark-cli task.task.patch — 实际命令名以最新 schema 为准；用 schema 查询：
  // lark-cli schema task.v2.task.patch
  const args = [
    'task', 'task.patch',
    '--as', asIdentity,
    '--params', JSON.stringify({ task_guid: taskGuid }),
    '--data', JSON.stringify(data),
  ];
  return larkCli(args);
}
