import test from 'node:test';
import assert from 'node:assert/strict';

import {
  extractDocxId,
  buildSuccessMessage,
  hydrateRewritePayload,
} from './rewrite_command_runner.mjs';

test('extractDocxId parses feishu docx urls', () => {
  assert.equal(extractDocxId('https://feishu.cn/docx/AbcDEF123'), 'AbcDEF123');
  assert.equal(extractDocxId('https://team.feishu.cn/docx/XYZ987?from=app'), 'XYZ987');
  assert.equal(extractDocxId('https://team.larksuite.com/docx/MnopQrst'), 'MnopQrst');
});

test('extractDocxId accepts raw document id when no url present', () => {
  assert.equal(extractDocxId('AbcDEF1234567890'), 'AbcDEF1234567890');
});

test('extractDocxId returns null when no docx id found', () => {
  assert.equal(extractDocxId(''), null);
  assert.equal(extractDocxId('   '), null);
  assert.equal(extractDocxId('https://example.com/other/path'), null);
});

test('hydrateRewritePayload pulls feishu credentials from openclaw config', () => {
  const fakeConfig = () => ({
    channels: {
      feishu: {
        defaultAccount: 'team',
        accounts: {
          team: { appId: 'cli_demo', appSecret: 'secret_demo' },
        },
      },
    },
  });
  const hydrated = hydrateRewritePayload({ docxUrl: 'https://feishu.cn/docx/abc' }, { loadConfig: fakeConfig });
  assert.equal(hydrated.accountId, 'team');
  assert.equal(hydrated.appId, 'cli_demo');
  assert.equal(hydrated.appSecret, 'secret_demo');
  assert.equal(typeof hydrated.jobId, 'string');
  assert.equal(hydrated.jobId.startsWith('RW_'), true);
});

test('buildSuccessMessage lists uploaded drafts and skips failures', () => {
  const message = buildSuccessMessage({
    jobId: 'RW_demo',
    sourceUrl: 'https://feishu.cn/docx/highlight',
    drafts: [
      { modelId: 'gpt5', modelLabel: 'GPT-5.5', url: 'https://feishu.cn/docx/g5' },
      { modelId: 'gemini_local', modelLabel: 'Gemini Local', url: 'https://feishu.cn/docx/gem' },
      { modelId: 'fallback', modelLabel: 'Fallback', error: 'upload failed' },
    ],
  });
  assert.equal(message.includes('源文档: https://feishu.cn/docx/highlight'), true);
  assert.equal(message.includes('GPT-5.5: https://feishu.cn/docx/g5'), true);
  assert.equal(message.includes('Gemini Local: https://feishu.cn/docx/gem'), true);
  assert.equal(message.includes('Fallback 上传失败: upload failed'), true);
  assert.equal(message.includes('[RW_demo]'), true);
});

test('buildSuccessMessage reports zero successful drafts gracefully', () => {
  const message = buildSuccessMessage({
    jobId: 'RW_empty',
    sourceUrl: 'https://feishu.cn/docx/highlight',
    drafts: [
      { modelId: 'gpt5', modelLabel: 'GPT-5.5', error: 'no draft' },
    ],
  });
  assert.equal(message.includes('未生成任何可用候选稿'), true);
});
