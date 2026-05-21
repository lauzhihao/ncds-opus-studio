import test from 'node:test';
import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';

test('media-command-router falls back to the xiaozhua workspace instead of the legacy workspace path', async () => {
  const text = await fs.readFile(
    new URL('../../../extensions/media-command-router/index.ts', import.meta.url),
    'utf8'
  );

  assert.equal(text.includes('.openclaw", "workspaces", "xiaozhua"'), true);
  assert.equal(text.includes('return api.workspaceDir ??'), false);
  assert.equal(text.includes('existsSync'), true);
});

test('media-command-router routes /rw into the douyin rewrite profile', async () => {
  const text = await fs.readFile(
    new URL('../../../extensions/media-command-router/index.ts', import.meta.url),
    'utf8'
  );

  assert.equal(text.includes("targetProfile: 'douyin'"), true);
  assert.equal(text.includes('输出: 纠错稿 + 大纲 + 4 个候选稿'), true);
});
