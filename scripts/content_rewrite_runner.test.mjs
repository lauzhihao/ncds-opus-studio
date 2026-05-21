import test from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises';

import { runContentRewrite } from './content_rewrite_runner.mjs';

test('runContentRewrite skips analysis and passes normalized transcript into outline/draft flow', async () => {
  const sourceText = '第一段原文。\r\n\r\n第二段原文。';
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'content-rewrite-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const rewriteDir = path.join(deliverablesDir, 'rewrite');
  const outlinePath = path.join(deliverablesDir, 'rewrite', 'outline.json');
  const indexPath = path.join(deliverablesDir, 'rewrite', 'index.json');
  const feedbackPath = path.join(deliverablesDir, 'rewrite', 'feedback.json');
  const draftPath = path.join(deliverablesDir, 'rewrite', 'douyin-test.md');

  const result = await runContentRewrite({
    sourceText,
    deliverablesDir,
    targetProfile: 'douyin',
    runRewriteImpl: async ({ transcriptPath }) => {
      const normalizedText = await readFile(transcriptPath, 'utf8');

      assert.ok(normalizedText.trim().length > 0);
      assert.equal(normalizedText.includes('\r\n'), false);

      await mkdir(rewriteDir, { recursive: true });
      await writeFile(outlinePath, JSON.stringify({ topic: '测试大纲' }, null, 2), 'utf8');
      await writeFile(indexPath, JSON.stringify({ drafts: [] }, null, 2), 'utf8');
      await writeFile(feedbackPath, JSON.stringify({ entries: [] }, null, 2), 'utf8');
      await writeFile(draftPath, '测试候选稿', 'utf8');

      return {
        status: 'success',
        outlinePath,
        indexPath,
        feedbackPath,
        drafts: [
          {
            modelId: 'test',
            modelLabel: 'Test Model',
            status: 'success',
            path: draftPath,
            durationMs: 1,
            error: null,
            reason: null,
            inputTokens: null,
            outputTokens: null,
          },
        ],
        errors: [],
      };
    },
  });

  const normalizedText = await readFile(result.normalizedTextPath, 'utf8');
  const analysisRecord = JSON.parse(await readFile(result.analysisPath, 'utf8'));

  assert.equal(result.status, 'success');
  assert.equal(result.targetProfile, 'douyin');
  assert.equal(result.candidateDrafts.length, 1);
  assert.equal(result.generatedBy, null);
  assert.equal(normalizedText.trim().length > 0, true);
  assert.equal(analysisRecord.summary, '未执行 analysis，直接使用转写清洗版进入大纲提取。');
  assert.deepEqual(result.risks, ['请基于原始转写保守改写，避免补充未被原文支持的事实。']);
});

test('runContentRewrite executes toutiao analysis before rewrite and passes analysis record through', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'content-rewrite-toutiao-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const rewriteDir = path.join(deliverablesDir, 'rewrite');
  const outlinePath = path.join(rewriteDir, 'outline.json');
  const indexPath = path.join(rewriteDir, 'index.json');
  const feedbackPath = path.join(rewriteDir, 'feedback.json');
  const draftPath = path.join(rewriteDir, 'toutiao-test.md');

  const result = await runContentRewrite({
    sourceText: '原始材料\r\n\r\n包含噪声。',
    deliverablesDir,
    targetProfile: 'toutiao',
    runAnalysisImpl: async ({ transcriptPath, targetProfile }) => {
      assert.equal(targetProfile, 'toutiao');
      assert.equal(await readFile(transcriptPath, 'utf8'), '原始材料\n\n包含噪声。');
      return {
        formatName: '头条',
        formatFeatures: ['错误的口播格式'],
        writingLogic: ['错误的直播推进逻辑'],
        expressionStyle: ['错误的强营销语气'],
        articleType: '错误的原文类型',
        normalizedText: '分析后的清洗正文',
        summary: '头条总结',
        audience: '错误受众',
        platformTone: '错误语气',
        sourceStyle: ['强口语', '种草感明显'],
        sourceArticleType: '口播种草素材',
        keyFacts: ['下载并安装后的第二天开始使用'],
        subjectiveClaims: ['效果还不错'],
        mustKeep: ['工具的使用场景覆盖内容生产和办公协助'],
        mustAvoid: ['不要扩写成收益承诺'],
        rewriteAngles: ['从入门门槛切入'],
        coreHighlights: ['AI 助理工具入门门槛低，且兼顾内容生产与办公提效'],
        conflictPoints: ['普通人想用 AI，但往往卡在下载、安装和信任门槛'],
        emotionPoints: ['对效率提升的期待', '对踩坑被骗的担心'],
        stancePoints: ['先看教程资料，再决定是否深入使用更稳妥'],
        audienceEmotions: ['好奇', '焦虑', '想尝试但怕踩坑'],
        propagationHooks: ['装上第二天就开始干活', '不是聊天而是执行', '普通人也能上手'],
        platformContext: '今日头条图文偏好强钩子、强相关、结果导向标题',
        headlineType: '反差型',
        headlineApproachReason: '素材最强传播点是“装上第二天就开始干活”的反差体验。',
        headlineFormula: ['反差场景 + 核心结果 + 工具定位'],
        headlineCandidates: [
          '装上第二天就干活，这个AI助理不太一样',
          '不是只会聊天，它开始帮我做内容了',
          '很多人卡在第一步，这个AI工具却先跑起来了',
          '从找爆款到做PPT，它把流程串起来了',
          '普通人想学AI，先过这道入门门槛',
        ],
        bestHeadline: '装上第二天就干活，这个AI助理不太一样',
        bestHeadlineReason: '反差强、结果明确、和正文最强相关，最容易触发点击。',
        learnedKnowledge: ['这类内容最适合用“反差体验 + 结果导向”标题公式。'],
        risks: ['不要编造数据'],
        generatedBy: 'analysis-model',
      };
    },
    runRewriteImpl: async ({ transcriptPath, targetProfile, analysisRecord }) => {
      assert.equal(targetProfile, 'toutiao');
      assert.equal(await readFile(transcriptPath, 'utf8'), '分析后的清洗正文');
      assert.equal(analysisRecord.formatName, '头条');
      assert.deepEqual(analysisRecord.writingLogic, ['开头用钩子点题', '中段分层展开事实与观点', '结尾做收束或提醒']);
      assert.deepEqual(analysisRecord.sourceStyle, ['强口语', '种草感明显']);
      assert.deepEqual(analysisRecord.mustAvoid, ['不要扩写成收益承诺']);
      assert.equal(analysisRecord.platformTone, '今日头条图文');
      assert.equal(analysisRecord.headlineType, '反差型');
      assert.equal(analysisRecord.bestHeadline, '装上第二天就干活，这个AI助理不太一样');
      assert.deepEqual(analysisRecord.headlineFormula, ['反差场景 + 核心结果 + 工具定位']);

      await mkdir(rewriteDir, { recursive: true });
      await writeFile(outlinePath, JSON.stringify({ topic: '测试大纲' }, null, 2), 'utf8');
      await writeFile(indexPath, JSON.stringify({ drafts: [] }, null, 2), 'utf8');
      await writeFile(feedbackPath, JSON.stringify({ entries: [] }, null, 2), 'utf8');
      await writeFile(draftPath, '标题1：看点一，看点二，看点三\n\n正文\n——END——', 'utf8');

      return {
        status: 'success',
        outlinePath,
        indexPath,
        feedbackPath,
        drafts: [
          {
            modelId: 'test',
            modelLabel: 'Test Model',
            status: 'success',
            path: draftPath,
            durationMs: 1,
            error: null,
            reason: null,
            inputTokens: null,
            outputTokens: null,
          },
        ],
        errors: [],
      };
    },
  });

  const normalizedText = await readFile(result.normalizedTextPath, 'utf8');
  const analysisRecord = JSON.parse(await readFile(result.analysisPath, 'utf8'));

  assert.equal(result.status, 'success');
  assert.equal(result.targetProfile, 'toutiao');
  assert.equal(result.generatedBy, 'analysis-model');
  assert.equal(normalizedText, '分析后的清洗正文');
  assert.equal(analysisRecord.analysisStatus, 'success');
  assert.equal(analysisRecord.formatName, '头条');
  assert.equal(analysisRecord.articleType, '今日头条图文稿');
  assert.deepEqual(analysisRecord.formatFeatures, ['图文阅读友好', '开头直接抛出主题或悬念', '分段清晰', '信息密度适中']);
  assert.deepEqual(analysisRecord.keyFacts, ['下载并安装后的第二天开始使用']);
  assert.deepEqual(analysisRecord.subjectiveClaims, ['效果还不错']);
  assert.deepEqual(analysisRecord.rewriteAngles, ['从入门门槛切入']);
  assert.deepEqual(analysisRecord.coreHighlights, ['AI 助理工具入门门槛低，且兼顾内容生产与办公提效']);
  assert.equal(analysisRecord.headlineType, '反差型');
  assert.equal(analysisRecord.bestHeadline, '装上第二天就干活，这个AI助理不太一样');
  assert.equal(analysisRecord.bestHeadlineReason, '反差强、结果明确、和正文最强相关，最容易触发点击。');
  assert.deepEqual(analysisRecord.headlineCandidates, [
    '装上第二天就干活，这个AI助理不太一样',
    '不是只会聊天，它开始帮我做内容了',
    '很多人卡在第一步，这个AI工具却先跑起来了',
    '从找爆款到做PPT，它把流程串起来了',
    '普通人想学AI，先过这道入门门槛',
  ]);
  assert.equal(result.candidateDrafts[0].path, draftPath);
});
