import test from 'node:test';
import assert from 'node:assert/strict';
import { getRewriteProfile, normalizeRewriteProfileId } from './rewrite_profiles.mjs';

test('paper_card_talk profile is registered and normalizable', () => {
  assert.equal(normalizeRewriteProfileId('paper_card_talk'), 'paper_card_talk');
  const profile = getRewriteProfile('paper_card_talk');
  assert.equal(profile.id, 'paper_card_talk');
  assert.equal(profile.requiresAnalysis, false);
  assert.equal(profile.requiresOutline, false);
  assert.equal(profile.draftFilePrefix, 'beats');
  assert.equal(profile.draftFileExt, '.json');
});

test('paper_card_talk draft prompt enforces JSON-only Beat[] schema', () => {
  const profile = getRewriteProfile('paper_card_talk');
  const prompt = profile.buildDraftPrompt({
    outline: {
      sourceText: '示例源文档：今天讲一个反共识的小故事。',
      userRequirements: '',
    },
  });
  // 必须把 schema 关键字段明确告诉模型
  assert.ok(prompt.includes('"beats"'), 'prompt should declare beats key');
  assert.ok(prompt.includes('"zh"'), 'prompt should declare zh field');
  assert.ok(prompt.includes('"en"'), 'prompt should declare en field');
  assert.ok(prompt.includes('"scene"'), 'prompt should declare scene field');
  assert.ok(prompt.includes('"chapter"'), 'prompt should declare chapter field');
  // 必须明确禁止 markdown 包裹
  assert.ok(prompt.includes('JSON'), 'prompt should require JSON output');
  // 源文档必须被注入
  assert.ok(prompt.includes('示例源文档'), 'prompt should embed sourceText');
});

test('paper_card_talk draft prompt embeds user_requirements when present', () => {
  const profile = getRewriteProfile('paper_card_talk');
  const prompt = profile.buildDraftPrompt({
    outline: {
      sourceText: 'src',
      userRequirements: '请把所有 beats 控制在 40 条以内',
    },
  });
  assert.ok(prompt.includes('请把所有 beats 控制在 40 条以内'));
  assert.ok(prompt.includes('最高优先级'));
});

test('paper_card_talk draft system prompt forces JSON-only', () => {
  const profile = getRewriteProfile('paper_card_talk');
  const sys = profile.buildStageSystemPrompt('draft');
  assert.ok(sys.includes('JSON'), 'system prompt should mention JSON');
  assert.ok(sys.includes('beats'), 'system prompt should mention beats');
});

test('paper_card_talk profile rejects analysis/outline calls', () => {
  const profile = getRewriteProfile('paper_card_talk');
  assert.throws(() => profile.buildAnalysisPrompt({ sourceText: 'x' }), /does not require analysis/);
  assert.throws(() => profile.buildOutlinePrompt({ transcriptText: 'x' }), /does not require outline/);
});

test('unknown profile id falls back to default (still works)', () => {
  // 已注册 profile 不变
  assert.equal(normalizeRewriteProfileId('paper_card_talk'), 'paper_card_talk');
  assert.equal(normalizeRewriteProfileId('douyin'), 'douyin');
  assert.equal(normalizeRewriteProfileId('toutiao'), 'toutiao');
});
