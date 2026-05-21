import test from 'node:test';
import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';

async function loadAsrCommandRunner() {
  return import(new URL('./asr_command_runner.mjs', import.meta.url));
}

test('parseAsrRequest extracts a single URL from /asr input', async () => {
  const { parseAsrRequest } = await loadAsrCommandRunner();

  const parsed = parseAsrRequest('/asr https://lnns.co/demo');

  assert.deepEqual(parsed, {
    command: 'asr',
    inputs: ['https://lnns.co/demo'],
  });
});

test('parseAsrRequest extracts multiple supported URLs from /asr input', async () => {
  const { parseAsrRequest } = await loadAsrCommandRunner();

  const parsed = parseAsrRequest('/asr https://lnns.co/a https://lnns.co/b');

  assert.deepEqual(parsed, {
    command: 'asr',
    inputs: ['https://lnns.co/a', 'https://lnns.co/b'],
  });
});

test('detectBareMediaLinkRequest matches bare URLs and extracts embedded media links from share text', async () => {
  const { detectBareMediaLinkRequest } = await loadAsrCommandRunner();

  assert.deepEqual(detectBareMediaLinkRequest('https://lnns.co/demo'), {
    command: 'asr',
    inputs: ['https://lnns.co/demo'],
  });
  assert.deepEqual(detectBareMediaLinkRequest('分析这个链接 https://lnns.co/demo'), {
    command: 'asr',
    inputs: ['https://lnns.co/demo'],
  });
  assert.deepEqual(detectBareMediaLinkRequest('https://lnns.co/a https://lnns.co/b'), {
    command: 'asr',
    inputs: ['https://lnns.co/a', 'https://lnns.co/b'],
  });
  assert.equal(detectBareMediaLinkRequest('hello there'), null);
  assert.deepEqual(detectBareMediaLinkRequest('  https://lnns.co/demo  '), {
    command: 'asr',
    inputs: ['https://lnns.co/demo'],
  });
  assert.deepEqual(detectBareMediaLinkRequest(
    '5.12 复制打开抖音，看看【用户的作品】描述 [标题](https://v.douyin.com/EI2DPWqkuhM/) T@y.TL oQK:/ 11/16'
  ), {
    command: 'asr',
    inputs: ['https://v.douyin.com/EI2DPWqkuhM/'],
  });
});

test('normalizeAsrPayload accepts raw URL strings, URL arrays, and object payload fallbacks', async () => {
  const { normalizeAsrPayload } = await loadAsrCommandRunner();

  const singleUrlPayload = normalizeAsrPayload('https://lnns.co/demo');
  assert.equal(typeof singleUrlPayload.jobId, 'string');
  assert.deepEqual(singleUrlPayload.inputs, ['https://lnns.co/demo']);

  const multiUrlPayload = normalizeAsrPayload('https://lnns.co/a https://lnns.co/b');
  assert.deepEqual(multiUrlPayload.inputs, ['https://lnns.co/a', 'https://lnns.co/b']);

  const arrayPayload = normalizeAsrPayload(JSON.stringify([
    'https://lnns.co/a',
    'https://v.douyin.com/EI2DPWqkuhM/',
  ]));
  assert.deepEqual(arrayPayload.inputs, ['https://lnns.co/a', 'https://v.douyin.com/EI2DPWqkuhM/']);

  const objectPayload = normalizeAsrPayload(JSON.stringify({
    jobId: 'vj_demo',
    url: 'https://lnns.co/demo',
    chatId: 'user:ou_demo',
    accountId: 'xiaozhua',
  }));
  assert.equal(objectPayload.jobId, 'vj_demo');
  assert.equal(objectPayload.chatId, 'user:ou_demo');
  assert.equal(objectPayload.accountId, 'xiaozhua');
  assert.deepEqual(objectPayload.inputs, ['https://lnns.co/demo']);

  const objectArrayPayload = normalizeAsrPayload({
    inputs: [
      { url: 'https://lnns.co/demo-a' },
      { url: 'https://lnns.co/demo-b' },
    ],
  });
  assert.deepEqual(objectArrayPayload.inputs, ['https://lnns.co/demo-a', 'https://lnns.co/demo-b']);
});

test('buildWorkerPayload includes job and delivery context', async () => {
  const { buildWorkerPayload } = await loadAsrCommandRunner();

  const payload = buildWorkerPayload({
    inputs: ['https://lnns.co/demo'],
    chatId: 'oc_demo',
    accountId: 'xiaozhua',
    appId: 'cli_app',
    appSecret: 'cli_secret',
    taskGuid: 'task_guid_demo',
    mode: 'hq',
    channel: 'feishu',
    provider: 'feishu',
    messageId: 'om_demo',
    chatType: 'direct',
  });

  assert.equal(typeof payload.jobId, 'string');
  assert.notEqual(payload.jobId.length, 0);
  assert.equal(payload.chatId, 'oc_demo');
  assert.equal(payload.accountId, 'xiaozhua');
  assert.equal(payload.appId, 'cli_app');
  assert.equal(payload.appSecret, 'cli_secret');
  assert.equal(payload.taskGuid, 'task_guid_demo');
  assert.equal(payload.mode, 'hq');
  assert.equal(payload.channel, 'feishu');
  assert.equal(payload.provider, 'feishu');
  assert.equal(payload.messageId, 'om_demo');
  assert.equal(payload.chatType, 'direct');
  assert.deepEqual(payload.inputs, ['https://lnns.co/demo']);
});

test('hydrateWorkerPayload fills missing feishu credentials and sender open id', async () => {
  const { hydrateWorkerPayload } = await loadAsrCommandRunner();

  const payload = hydrateWorkerPayload({
    jobId: 'vj_demo',
    inputs: ['https://lnns.co/demo'],
    chatId: 'user:ou_demo_sender',
    accountId: 'xiaozhua',
    appId: null,
    appSecret: null,
  }, {
    loadConfig: () => ({
      channels: {
        feishu: {
          accounts: {
            xiaozhua: {
              appId: 'cli_demo_app',
              appSecret: 'cli_demo_secret',
            },
          },
        },
      },
    }),
  });

  assert.equal(payload.appId, 'cli_demo_app');
  assert.equal(payload.appSecret, 'cli_demo_secret');
  assert.equal(payload.senderOpenId, 'ou_demo_sender');
  assert.equal(payload.chatId, 'user:ou_demo_sender');
});

test('hydrateWorkerPayload falls back to default Feishu account when accountId is missing', async () => {
  const { hydrateWorkerPayload } = await loadAsrCommandRunner();

  const payload = hydrateWorkerPayload({
    jobId: 'vj_demo',
    inputs: ['https://lnns.co/demo'],
    chatId: null,
    accountId: null,
    appId: null,
    appSecret: null,
  }, {
    loadConfig: () => ({
      channels: {
        feishu: {
          defaultAccount: 'xiaozhua',
          accounts: {
            xiaozhua: {
              appId: 'cli_demo_app',
              appSecret: 'cli_demo_secret',
            },
          },
        },
      },
    }),
  });

  assert.equal(payload.accountId, 'xiaozhua');
  assert.equal(payload.appId, 'cli_demo_app');
  assert.equal(payload.appSecret, 'cli_demo_secret');
});

test('launchAsrWorker targets video_job_worker.mjs and never video_pipeline.py', async () => {
  const { launchAsrWorker } = await loadAsrCommandRunner();
  const calls = [];

  await launchAsrWorker({
    payload: {
      jobId: 'vj_demo',
      inputs: ['https://lnns.co/demo'],
    },
  }, {
    spawnImpl: (...args) => {
      calls.push(args);
      return {
        unref() {},
        on() {},
      };
    },
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0][1].some((part) => String(part).includes('video_job_worker.mjs')), true);
  assert.equal(calls[0][1].some((part) => String(part).includes('video_pipeline.py')), false);
});

test('launchAsrWorker injects OPENCLAW_PYTHON into worker env', async () => {
  const { launchAsrWorker } = await loadAsrCommandRunner();
  const calls = [];
  const originalPython = process.env.OPENCLAW_PYTHON;
  process.env.OPENCLAW_PYTHON = '/opt/homebrew/bin/python3';

  try {
    await launchAsrWorker({
      payload: {
        jobId: 'vj_python_env',
        inputs: ['https://lnns.co/demo'],
      },
      workspaceDir: '/tmp/workspace',
    }, {
      spawnImpl: (...args) => {
        calls.push(args);
        return {
          unref() {},
          on() {},
        };
      },
    });
  } finally {
    if (originalPython === undefined) {
      delete process.env.OPENCLAW_PYTHON;
    } else {
      process.env.OPENCLAW_PYTHON = originalPython;
    }
  }

  assert.equal(calls.length, 1);
  assert.equal(calls[0][2].env.OPENCLAW_PYTHON, '/opt/homebrew/bin/python3');
});

test('launchAsrWorker supports sync mode without detaching worker', async () => {
  const { launchAsrWorker } = await loadAsrCommandRunner();
  const calls = [];
  let unrefCalled = false;

  await launchAsrWorker({
    payload: {
      jobId: 'vj_sync',
      inputs: ['https://lnns.co/demo'],
    },
    workspaceDir: '/tmp/workspace',
    wait: true,
  }, {
    spawnImpl: (...args) => {
      calls.push(args);
      const child = {
        pid: 12345,
        stdout: { on() {} },
        stderr: { on() {} },
        unref() {
          unrefCalled = true;
        },
        once(event, callback) {
          if (event === 'close') {
            setImmediate(() => callback(0, null));
          }
          return child;
        },
      };
      return child;
    },
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0][2].detached, false);
  assert.deepEqual(calls[0][2].stdio, ['ignore', 'pipe', 'pipe']);
  assert.equal(unrefCalled, false);
});

test('asr skill prefers plugin tools and falls back to the workspace command runner contract', async () => {
  const text = await fs.readFile(new URL('../skills/asr/SKILL.md', import.meta.url), 'utf8');

  assert.equal(text.includes('name: asr'), true);
  assert.equal(text.includes('/asr <url>'), true);
  assert.equal(text.includes('优先调用 `asr` tool'), true);
  assert.equal(text.includes('再调用 `media_command_router`'), true);
  assert.equal(text.includes('node scripts/asr_command_runner.mjs'), true);
  assert.equal(text.includes('node workspace/scripts/asr_command_runner.mjs'), false);
  assert.equal(text.includes('chatId'), true);
  assert.equal(text.includes('senderOpenId'), true);
  assert.equal(text.includes('messageId'), true);
  assert.equal(text.includes('chatType'), true);
  assert.equal(
    text.includes('不要输出“当前会话里没有可用的 `asr` / `media_command_router` 插件工具”这类拒绝文案'),
    true
  );
  assert.equal(text.includes('禁止直接运行 `video_job_worker.mjs` 或 `video_pipeline.py`'), true);
});

test('video-pipeline internal doc routes chat-triggered transcription through /asr and worker path', async () => {
  const text = await fs.readFile(new URL('../skills/video-pipeline/SKILL.md', import.meta.url), 'utf8');

  assert.equal(text.includes('/asr <url>'), true);
  assert.equal(text.includes('统一只走 `/asr <url>` 这条入口'), true);
  assert.equal(text.includes('node scripts/asr_command_runner.mjs'), true);
  assert.equal(text.includes('node workspace/scripts/asr_command_runner.mjs'), false);
  assert.equal(text.includes('video_job_worker.mjs'), true);
  assert.equal(text.includes('只有当前会话里 `asr` 与 `media_command_router` 都不可用时'), true);
  assert.equal(text.includes('不要在 agent 会话里手动创建 job 目录'), true);
  assert.equal(text.includes('不要在 chat-triggered handling 里直接执行 `python3 skills/video-pipeline/scripts/video_pipeline.py`'), true);
  assert.equal(text.includes('这里描述的是底层处理能力，不是 agent 的前台可选技能'), true);
  assert.equal(text.includes('启动成功后不得再次手动调用 `video_job_worker.mjs`'), true);
  assert.equal(text.includes('启动成功后不得再直接调用 `video_pipeline.py`'), true);
  assert.equal(text.includes('只能等待 worker 推送、或轮询 `video-jobs/<job_id>/job.json` 查看状态'), true);
  assert.equal(text.includes('启动成功后不要自己读取转写文本并直接总结成交付结果'), true);
});
