import test from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { chmod, mkdtemp, readFile, writeFile } from 'node:fs/promises';

import {
  buildDraftRecord,
  createDefaultModelInvoker,
  getDefaultGeminiCliPath,
  getDefaultModelConfigPath,
  parseJsonObject,
  runRewriteForItem,
  resolveRewriteModels,
} from './video_rewrite_runner.mjs';

const TEST_CODEX_AUTH_PROFILES = {
  version: 1,
  profiles: {
    'openai-codex:default': {
      type: 'oauth',
      provider: 'openai-codex',
      access: 'codex-access-token',
      refresh: 'codex-refresh-token',
      expires: 4102444800000,
    },
  },
};

const TEST_CODEX_AUTH_STATE = {
  version: 1,
  order: {
    'openai-codex': ['openai-codex:default'],
  },
  lastGood: {
    'openai-codex': 'openai-codex:default',
  },
};

function getMissingGeminiCliPath(tempRoot) {
  return path.join(tempRoot, 'missing-g.sh');
}

async function createExecutableScript(tempRoot, name, body) {
  const scriptPath = path.join(tempRoot, name);
  await writeFile(scriptPath, body, 'utf8');
  await chmod(scriptPath, 0o755);
  return scriptPath;
}

test('getDefaultModelConfigPath returns ~/.openclaw/openclaw.json', () => {
  assert.equal(
    getDefaultModelConfigPath(),
    path.join(os.homedir(), '.openclaw', 'openclaw.json'),
  );
});

test('getDefaultGeminiCliPath returns ~/.gemini/g.sh', () => {
  assert.equal(
    getDefaultGeminiCliPath(),
    path.join(os.homedir(), '.gemini', 'g.sh'),
  );
});

test('resolveRewriteModels keeps local Gemini first and only exposes openai-codex as remote candidate', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-models-'));
  const modelConfigPath = path.join(tempRoot, 'openclaw.json');
  const geminiCliPath = await createExecutableScript(
    tempRoot,
    'g.sh',
    '#!/bin/bash\nexit 0\n',
  );

  await writeFile(modelConfigPath, JSON.stringify({
    agents: {
      defaults: {
        models: {
          'openai-codex/gpt-5.5': { alias: 'gpt55' },
        },
      },
    },
  }), 'utf8');

  const models = await resolveRewriteModels({ modelConfigPath, geminiCliPath });

  assert.deepEqual(
    models.map((model) => ({
      id: model.id,
      modelRef: model.modelRef,
      label: model.label,
      status: model.status,
      reason: model.reason,
    })),
    [
      {
        id: 'gemini_local',
        modelRef: 'local-gemini/g.sh',
        label: 'Local Gemini via g.sh',
        status: 'available',
        reason: null,
      },
      {
        id: 'gpt5',
        modelRef: 'openai-codex/gpt-5.5',
        label: 'GPT-5.5 via OpenAI Codex',
        status: 'available',
        reason: null,
      },
    ],
  );
});

test('resolveRewriteModels keeps openai-codex when local Gemini is unavailable', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-models-codex-'));
  const modelConfigPath = path.join(tempRoot, 'openclaw.json');
  const geminiCliPath = getMissingGeminiCliPath(tempRoot);

  await writeFile(modelConfigPath, JSON.stringify({
    agents: {
      defaults: {
        models: {
          'openai-codex/gpt-5.5': { alias: 'gpt55' },
        },
      },
    },
  }), 'utf8');

  const models = await resolveRewriteModels({ modelConfigPath, geminiCliPath });

  assert.deepEqual(
    models.map((model) => ({
      id: model.id,
      modelRef: model.modelRef,
      label: model.label,
      status: model.status,
      reason: model.reason,
    })),
    [
      {
        id: 'gpt5',
        modelRef: 'openai-codex/gpt-5.5',
        label: 'GPT-5.5 via OpenAI Codex',
        status: 'available',
        reason: null,
      },
    ],
  );
});

test('buildDraftRecord returns index-compatible contract fields', () => {
  assert.deepEqual(
    buildDraftRecord({
      modelId: 'gpt5',
      modelLabel: 'GPT-5.5 via OpenAI Codex',
      status: 'skipped',
      path: '/tmp/rewrite/douyin-gpt5.md',
      reason: 'model_not_configured',
    }),
    {
      modelId: 'gpt5',
      modelLabel: 'GPT-5.5 via OpenAI Codex',
      status: 'skipped',
      path: '/tmp/rewrite/douyin-gpt5.md',
      durationMs: null,
      error: null,
      reason: 'model_not_configured',
      inputTokens: null,
      outputTokens: null,
    },
  );
});

test('parseJsonObject tolerates fenced JSON and trailing commas', () => {
  const parsed = parseJsonObject([
    '```json',
    '{',
    '  "topic": "梁山",',
    '  "corePoints": ["A", "B",],',
    '  "facts": ["C"],',
    '  "angles": ["D"],',
    '  "constraints": ["E",],',
    '}',
    '```',
  ].join('\n'));

  assert.deepEqual(parsed, {
    topic: '梁山',
    corePoints: ['A', 'B'],
    facts: ['C'],
    angles: ['D'],
    constraints: ['E'],
  });
});

test('createDefaultModelInvoker calls local-gemini provider via safe g.sh adapter for analysis and draft stages', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-local-gemini-'));
  const geminiCliPath = await createExecutableScript(
    tempRoot,
    'g.sh',
    '#!/bin/bash\nexit 0\n',
  );
  const prompts = [];
  const modelInvoker = createDefaultModelInvoker({
    modelConfig: {},
    geminiCliPath,
    runGeminiCliImpl: async ({ cliPath, prompt }) => {
      prompts.push({ cliPath, prompt });
      assert.equal(cliPath, geminiCliPath);

      if (prompt.includes('今日头条爆款标题分析师')) {
        return JSON.stringify({
          formatName: '头条',
          sourceStyle: ['信息密度高'],
          sourceArticleType: '短视频素材',
          normalizedText: '清洗后的正文',
          summary: '一段总结',
          keyFacts: ['事实A'],
          subjectiveClaims: ['观点A'],
          mustKeep: ['信息点A'],
          mustAvoid: ['不要夸大'],
          rewriteAngles: ['反向切入'],
          coreHighlights: ['核心看点A'],
          conflictPoints: ['冲突点A'],
          emotionPoints: ['情绪点A'],
          stancePoints: ['立场点A'],
          audienceEmotions: ['好奇'],
          propagationHooks: ['信息差'],
          platformContext: '今日头条图文语境',
          headlineType: '反差型',
          headlineApproachReason: '反差感最强。',
          headlineFormula: ['现象 + 反常识结论 + 冲击'],
          headlineCandidates: [
            '现象A背后，真相反着来，后劲太大',
            '大家都看错了，这个变化才最伤人',
            '热闹表面下，真正吃亏的是这群人',
          ],
          bestHeadline: '现象A背后，真相反着来，后劲太大',
          bestHeadlineReason: '反差最强，点击驱动力最足。',
          learnedKnowledge: ['反差型标题更容易出点击。'],
          risks: ['不要编造数据'],
        });
      }

      return '标题1：现象A背后，真相反着来，后劲太大\n标题2：大家都看错了，这个变化才最伤人\n标题3：热闹表面下，真正吃亏的是这群人\n\n正文内容\n——END——';
    },
  });

  const model = {
    id: 'gemini_local',
    label: 'Local Gemini via g.sh',
    modelRef: 'local-gemini/g.sh',
  };
  const { result: analysis } = await modelInvoker({
    stage: 'analysis',
    model,
    transcriptText: '原始素材',
    targetProfile: 'toutiao',
  });
  const { result: draft } = await modelInvoker({
    stage: 'draft',
    model,
    targetProfile: 'toutiao',
    analysisRecord: analysis,
    outline: {
      topic: '测试主题',
      corePoints: ['观点A'],
      facts: ['事实A'],
      angles: ['角度A'],
      constraints: ['约束A'],
    },
  });

  assert.equal(analysis.formatName, '头条');
  assert.equal(prompts.some(({ prompt }) => prompt.includes('【硬性输出约束】')), true);
  assert.equal(prompts.some(({ prompt }) => prompt.includes('合法 JSON 对象')), true);
  assert.equal(prompts.some(({ prompt }) => prompt.includes('3 个标题')), true);
  assert.equal(draft.includes('——END——'), true);
});

test('createDefaultModelInvoker calls openai-codex models through codex cli for outline and draft stages', async () => {
  const calls = [];
  const modelInvoker = createDefaultModelInvoker({
    modelConfig: {
      models: {
        providers: {
          'openai-codex': {
            cliPath: '/tmp/codex',
          },
        },
      },
    },
    runCodexCliImpl: async ({ cliPath, prompt, model }) => {
      calls.push({ cliPath, prompt, model });
      assert.equal(cliPath, '/tmp/codex');
      assert.equal(model, 'gpt-5.5');
      assert.equal(typeof prompt, 'string');

      if (prompt.includes('JSON')) {
        return JSON.stringify({
          topic: '太阳神垄断联盟',
          corePoints: ['观点A'],
          facts: ['事实A'],
          angles: ['角度A'],
          constraints: ['约束A'],
        });
      }

      return '这是一篇抖音稿。';
    },
  });

  const { result: outline } = await modelInvoker({
    stage: 'outline',
    model: { modelRef: 'openai-codex/gpt-5.5' },
    transcriptText: '原始转写',
  });
  const { result: draft } = await modelInvoker({
    stage: 'draft',
    model: { modelRef: 'openai-codex/gpt-5.5' },
    outline,
  });

  assert.equal(outline.topic, '太阳神垄断联盟');
  assert.equal(draft, '这是一篇抖音稿。');
  assert.equal(calls.length, 2);
});

test('createDefaultModelInvoker parses openai-codex json returned by codex cli', async () => {
  const modelInvoker = createDefaultModelInvoker({
    modelConfig: {
      models: {
        providers: {
          'openai-codex': {},
        },
      },
    },
    runCodexCliImpl: async () => JSON.stringify({
      topic: 'CLI 主题',
      corePoints: [],
      facts: [],
      angles: [],
      constraints: [],
    }),
  });

  const { result, usage } = await modelInvoker({
    stage: 'outline',
    model: {
      id: 'gpt5',
      label: 'GPT-5.5 via OpenAI Codex',
      modelRef: 'openai-codex/gpt-5.5',
    },
    transcriptText: '测试 transcript',
    targetProfile: 'toutiao',
  });

  assert.equal(result.topic, 'CLI 主题');
  assert.equal(usage.inputTokens, null);
  assert.equal(usage.outputTokens, null);
});

test('createDefaultModelInvoker uses toutiao prompts for analysis and draft stages', async () => {
  const requests = [];
  const modelInvoker = createDefaultModelInvoker({
    modelConfig: {
      models: {
        providers: {
          'openai-codex': {
            cliPath: '/tmp/codex',
          },
        },
      },
    },
    runCodexCliImpl: async ({ prompt }) => {
      requests.push(prompt);

      if (prompt.includes('今日头条爆款标题分析师')) {
        return JSON.stringify({
                  formatName: '头条',
                  sourceStyle: ['强口播', '强种草'],
                  sourceArticleType: '短视频口播素材',
                  normalizedText: '清洗后的正文',
                  summary: '一段总结',
                  keyFacts: ['安装后的第二天开始使用'],
                  subjectiveClaims: ['效果还不错'],
                  mustKeep: ['工具覆盖内容生产与办公场景'],
                  mustAvoid: ['不要写收益承诺'],
                  rewriteAngles: ['从入门门槛切入'],
                  coreHighlights: ['工具能参与内容生产和办公执行'],
                  conflictPoints: ['普通人知道 AI 有用，但卡在第一步'],
                  emotionPoints: ['好奇', '想提效'],
                  stancePoints: ['先看资料再上手更稳'],
                  audienceEmotions: ['想尝试', '怕踩坑'],
                  propagationHooks: ['装上第二天就开始干活'],
                  platformContext: '今日头条图文偏好强钩子与结果导向标题',
                  headlineType: '反差型',
                  headlineApproachReason: '反差感是这篇内容最强的点击来源。',
                  headlineFormula: ['反差场景 + 结果 + 工具定位'],
                  headlineCandidates: [
                    '装上第二天就干活，这个AI助理不太一样',
                    '不是只会聊天，它开始帮我做内容了',
                    '很多人卡在第一步，这个AI工具却先跑起来了',
                  ],
                  bestHeadline: '装上第二天就干活，这个AI助理不太一样',
                  bestHeadlineReason: '反差明确、结果直接、最符合读者点击心理。',
                  learnedKnowledge: ['反差体验适合做今日头条工具类标题。'],
                  risks: ['不要编造数据'],
        });
      }

      return '标题1：看点一，关键变化，别错过\n\n正文内容\n——END——';
    },
  });

  const { result: analysis } = await modelInvoker({
    stage: 'analysis',
    model: { modelRef: 'openai-codex/gpt-5.5' },
    transcriptText: '原始素材',
    targetProfile: 'toutiao',
  });
  const { result: draft } = await modelInvoker({
    stage: 'draft',
    model: { modelRef: 'openai-codex/gpt-5.5' },
    targetProfile: 'toutiao',
    analysisRecord: {
      formatName: '头条',
      formatFeatures: ['图文阅读友好', '开头直接抛出主题或悬念', '分段清晰', '信息密度适中'],
      writingLogic: ['开头用钩子点题', '中段分层展开事实与观点', '结尾做收束或提醒'],
      expressionStyle: ['浅显易懂', '中文互联网图文语体', '兼顾信息量与可读性'],
      articleType: '今日头条图文稿',
      audience: '今日头条普通图文读者',
      platformTone: '今日头条图文',
      coreHighlights: ['工具能参与内容生产和办公执行'],
      conflictPoints: ['普通人知道 AI 有用，但卡在第一步'],
      emotionPoints: ['好奇', '想提效'],
      stancePoints: ['先看资料再上手更稳'],
      audienceEmotions: ['想尝试', '怕踩坑'],
      propagationHooks: ['装上第二天就开始干活'],
      platformContext: '今日头条图文偏好强钩子与结果导向标题',
      headlineType: '反差型',
      headlineApproachReason: '反差感是这篇内容最强的点击来源。',
      headlineFormula: ['反差场景 + 结果 + 工具定位'],
      headlineCandidates: [
        '装上第二天就干活，这个AI助理不太一样',
        '不是只会聊天，它开始帮我做内容了',
        '很多人卡在第一步，这个AI工具却先跑起来了',
      ],
      bestHeadline: '装上第二天就干活，这个AI助理不太一样',
      bestHeadlineReason: '反差明确、结果直接、最符合读者点击心理。',
      learnedKnowledge: ['反差体验适合做今日头条工具类标题。'],
      ...analysis,
    },
    outline: {
      topic: '今日头条选题',
      corePoints: ['观点A'],
      facts: ['事实A'],
      angles: ['角度A'],
      constraints: ['约束A'],
    },
  });

  assert.equal(analysis.formatName, '头条');
  assert.equal(requests.some((content) => content.includes('今日头条平台发布的中文图文稿')), true);
  assert.equal(requests.some((content) => content.includes('输出格式要求')), true);
  assert.equal(requests.some((content) => content.includes('今日头条爆款标题分析师')), true);
  assert.equal(requests.some((content) => content.includes('headlineCandidates 必须固定输出 3 条')), true);
  assert.equal(requests.some((content) => content.includes('标题分析：')), true);
  assert.equal(requests.some((content) => content.includes('字数严格控制在 1600～1700 字之间')), true);
  assert.equal(requests.some((content) => content.includes('现象/事件 + 反常识结论 + 冲击')), true);
  assert.equal(draft.includes('——END——'), true);
});

test('createDefaultModelInvoker surfaces codex cli errors without remote fallback', async () => {
  const calls = [];
  const modelInvoker = createDefaultModelInvoker({
    modelConfig: {
      models: {
        providers: {
          'openai-codex': {},
        },
      },
    },
    runCodexCliImpl: async ({ prompt, model }) => {
      calls.push({ prompt, model });
      throw new Error('codex cli unavailable');
    },
  });

  await assert.rejects(
    () => modelInvoker({
      stage: 'outline',
      model: { modelRef: 'openai-codex/gpt-5.5' },
      transcriptText: '原始转写',
      targetProfile: 'toutiao',
    }),
    /codex cli unavailable/,
  );

  assert.equal(calls.length, 1);
  assert.equal(calls[0].model, 'gpt-5.5');
});

test('createDefaultModelInvoker resolves configured openai-codex cli path', async () => {
  let requestedCliPath = null;
  const modelInvoker = createDefaultModelInvoker({
    modelConfig: {
      models: {
        providers: {
          'openai-codex': {
            cliPath: '/custom/codex',
          },
        },
      },
    },
    runCodexCliImpl: async ({ cliPath }) => {
      requestedCliPath = cliPath;
      return JSON.stringify({
        topic: 'CLI 路径测试',
        corePoints: ['观点A'],
        facts: ['事实A'],
        angles: ['角度A'],
        constraints: ['约束A'],
      });
    },
  });

  await modelInvoker({
    stage: 'outline',
    model: { modelRef: 'openai-codex/gpt-5.5' },
    transcriptText: '原始转写',
  });

  assert.equal(requestedCliPath, '/custom/codex');
});

test('createDefaultModelInvoker ignores openai-codex proxy env because codex cli owns transport', async () => {
  process.env.OPENAI_CODEX_PROXY_URL = 'http://127.0.0.1:10808';

  try {
    let requestedPrompt = null;
    const modelInvoker = createDefaultModelInvoker({
      modelConfig: {
        models: {
          providers: {
            'openai-codex': {},
          },
        },
      },
      runCodexCliImpl: async ({ prompt, model }) => {
        requestedPrompt = prompt;
        assert.equal(model, 'gpt-5.5');
        return '这是一篇改写稿。';
      },
    });

    const result = await modelInvoker({
      stage: 'draft',
      model: { modelRef: 'openai-codex/gpt-5.5' },
      outline: {
        topic: '代理测试',
        corePoints: ['观点A'],
        facts: ['事实A'],
        angles: ['角度A'],
        constraints: ['约束A'],
      },
    });

    assert.equal(requestedPrompt.includes('代理测试'), true);
    assert.equal(result.result, '这是一篇改写稿。');
    assert.deepEqual(result.usage, { inputTokens: null, outputTokens: null });
  } finally {
    delete process.env.OPENAI_CODEX_PROXY_URL;
  }
});

test('runRewriteForItem writes outline.json from polished transcript source', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-outline-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const transcriptPath = path.join(tempRoot, 'transcript.md');
  const polishedTranscriptPath = path.join(tempRoot, 'polished.md');
  const modelConfigPath = path.join(tempRoot, 'openclaw.json');

  await writeFile(transcriptPath, 'raw transcript', 'utf8');
  await writeFile(polishedTranscriptPath, 'polished transcript', 'utf8');
  await writeFile(modelConfigPath, JSON.stringify({
    agents: {
      defaults: {
        models: {
          'openai-codex/gpt-5.5': { alias: 'gpt55' },
        },
      },
    },
  }), 'utf8');

  const result = await runRewriteForItem({
    workspaceDir: tempRoot,
    jobId: 'job-1',
    input: { url: 'https://example.com/video' },
    transcriptPath,
    polishedTranscriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async ({ sourcePath, sourceType, transcriptText }) => {
      assert.equal(sourcePath, polishedTranscriptPath);
      assert.equal(sourceType, 'polished_transcript');
      assert.equal(transcriptText, 'polished transcript');

      return {
        topic: 'AI workflow',
        corePoints: ['point 1'],
        facts: ['fact 1'],
        angles: ['angle 1'],
        constraints: ['constraint 1'],
        generatedBy: 'test-outline-generator',
      };
    },
    draftGenerator: async ({ model, outline }) => `Draft by ${model.id} for ${outline.topic}`,
  });

  const outlinePath = path.join(deliverablesDir, 'rewrite', 'outline.json');
  const outline = JSON.parse(await readFile(outlinePath, 'utf8'));

  assert.equal(result.status, 'success');
  assert.equal(result.outlinePath, outlinePath);
  assert.equal(outline.sourcePath, polishedTranscriptPath);
  assert.equal(outline.sourceType, 'polished_transcript');
  assert.equal(outline.topic, 'AI workflow');
  assert.deepEqual(outline.corePoints, ['point 1']);
  assert.deepEqual(outline.facts, ['fact 1']);
  assert.deepEqual(outline.angles, ['angle 1']);
  assert.deepEqual(outline.constraints, ['constraint 1']);
  assert.equal(outline.generatedBy, 'test-outline-generator');
  assert.equal(typeof outline.generatedAt, 'string');
});

test('runRewriteForItem falls back to raw transcript when polished transcript is absent', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-outline-fallback-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const transcriptPath = path.join(tempRoot, 'transcript.md');
  const modelConfigPath = path.join(tempRoot, 'openclaw.json');

  await writeFile(transcriptPath, 'raw transcript only', 'utf8');
  await writeFile(modelConfigPath, JSON.stringify({
    agents: {
      defaults: {
        models: {
          'openai-codex/gpt-5.5': { alias: 'gpt55' },
        },
      },
    },
  }), 'utf8');

  const result = await runRewriteForItem({
    workspaceDir: tempRoot,
    jobId: 'job-2',
    input: { url: 'https://example.com/video' },
    transcriptPath,
    polishedTranscriptPath: path.join(tempRoot, 'missing-polished.md'),
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async ({ sourcePath, sourceType, transcriptText }) => {
      assert.equal(sourcePath, transcriptPath);
      assert.equal(sourceType, 'raw_transcript');
      assert.equal(transcriptText, 'raw transcript only');

      return {
        topic: 'Fallback topic',
        corePoints: ['raw point'],
        facts: ['raw fact'],
        angles: ['raw angle'],
        constraints: ['raw constraint'],
        generatedBy: 'test-outline-generator',
      };
    },
    draftGenerator: async ({ model, outline }) => `Draft by ${model.id} for ${outline.topic}`,
  });

  const outline = JSON.parse(await readFile(result.outlinePath, 'utf8'));
  assert.equal(result.status, 'success');
  assert.equal(outline.sourcePath, transcriptPath);
  assert.equal(outline.sourceType, 'raw_transcript');
});

test('runRewriteForItem initializes feedback.json and returns its path', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-feedback-init-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const transcriptPath = path.join(tempRoot, 'transcript.md');
  const modelConfigPath = path.join(tempRoot, 'openclaw.json');

  await writeFile(transcriptPath, 'raw transcript only', 'utf8');
  await writeFile(modelConfigPath, JSON.stringify({
    agents: {
      defaults: {
        models: {
          'openai-codex/gpt-5.5': { alias: 'gpt55' },
        },
      },
    },
  }), 'utf8');

  const result = await runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async () => ({
      topic: 'Feedback topic',
      corePoints: ['point 1'],
      facts: ['fact 1'],
      angles: ['angle 1'],
      constraints: ['constraint 1'],
      generatedBy: 'outline-test-model',
    }),
    draftGenerator: async ({ model, outline }) => `Draft by ${model.id} for ${outline.topic}`,
  });

  const feedbackPath = path.join(deliverablesDir, 'rewrite', 'feedback.json');
  const feedback = JSON.parse(await readFile(feedbackPath, 'utf8'));

  assert.equal(result.feedbackPath, feedbackPath);
  assert.deepEqual(feedback, {
    entries: [],
  });
});

test('runRewriteForItem preserves seeded feedback.json entries on rerun', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-feedback-preserve-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const transcriptPath = path.join(tempRoot, 'transcript.md');
  const modelConfigPath = path.join(tempRoot, 'openclaw.json');
  const feedbackPath = path.join(deliverablesDir, 'rewrite', 'feedback.json');

  await writeFile(transcriptPath, 'raw transcript only', 'utf8');
  await writeFile(modelConfigPath, JSON.stringify({
    agents: {
      defaults: {
        models: {
          'openai-codex/gpt-5.5': { alias: 'gpt55' },
        },
      },
    },
  }), 'utf8');

  await runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async () => ({
      topic: 'Feedback topic',
      corePoints: ['point 1'],
      facts: ['fact 1'],
      angles: ['angle 1'],
      constraints: ['constraint 1'],
      generatedBy: 'outline-test-model',
    }),
    draftGenerator: async ({ model, outline }) => `Draft by ${model.id} for ${outline.topic}`,
  });

  const seededFeedback = {
    entries: [
      {
        selectedModelId: 'opus',
        rawText: 'Keep this version',
        createdAt: '2026-03-19T12:00:00.000Z',
      },
    ],
  };
  await writeFile(feedbackPath, JSON.stringify(seededFeedback, null, 2), 'utf8');

  await runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async () => ({
      topic: 'Feedback topic rerun',
      corePoints: ['point 2'],
      facts: ['fact 2'],
      angles: ['angle 2'],
      constraints: ['constraint 2'],
      generatedBy: 'outline-test-model',
    }),
    draftGenerator: async ({ model, outline }) => `Draft by ${model.id} for ${outline.topic}`,
  });

  const feedback = JSON.parse(await readFile(feedbackPath, 'utf8'));
  assert.deepEqual(feedback, seededFeedback);
});

test('runRewriteForItem default path fails fast when no rewrite model is configured', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-outline-no-model-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const transcriptPath = path.join(tempRoot, 'transcript.md');
  const modelConfigPath = path.join(tempRoot, 'openclaw.json');

  await writeFile(transcriptPath, 'raw transcript only', 'utf8');
  await writeFile(modelConfigPath, JSON.stringify({ agents: { defaults: { models: {} } } }), 'utf8');

  const result = await runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
  });

  await assert.rejects(readFile(path.join(deliverablesDir, 'rewrite', 'outline.json'), 'utf8'));
  assert.equal(result.status, 'failed');
  assert.deepEqual(result.errors, [
    {
      stage: 'outline',
      code: 'model_not_configured',
      message: 'No configured rewrite model is available for outline generation',
    },
  ]);
});

test('runRewriteForItem default path uses configured model invocation and persists outline', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-outline-default-model-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const transcriptPath = path.join(tempRoot, 'transcript.md');
  const polishedTranscriptPath = path.join(tempRoot, 'polished.md');
  const modelConfigPath = path.join(tempRoot, 'openclaw.json');
  let invocationCount = 0;

  await writeFile(transcriptPath, 'raw transcript only', 'utf8');
  await writeFile(polishedTranscriptPath, 'polished transcript body', 'utf8');
  await writeFile(modelConfigPath, JSON.stringify({
    models: {
      providers: {
        'openai-codex': {
          cliPath: '/tmp/codex',
        },
      },
    },
    agents: {
      defaults: {
        models: {
          'openai-codex/gpt-5.5': { alias: 'gpt55' },
        },
      },
    },
  }), 'utf8');
  const result = await runRewriteForItem({
    transcriptPath,
    polishedTranscriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    runCodexCliImpl: async ({ prompt }) => {
      invocationCount += 1;

      if (prompt.includes('JSON')) {
        assert.equal(prompt.includes('polished transcript body'), true);

        return JSON.stringify({
          topic: 'Default path topic',
          corePoints: ['point A'],
          facts: ['fact A'],
          angles: ['angle A'],
          constraints: ['constraint A'],
        });
      }

      assert.equal(prompt.includes('Default path topic'), true);
      return 'Draft by gpt5';
    },
  });

  assert.deepEqual(result.errors, []);
  assert.equal(result.status, 'success');
  assert.equal(result.outlinePath, path.join(deliverablesDir, 'rewrite', 'outline.json'));
  assert.equal(invocationCount, 2);
});

test('runRewriteForItem returns the full success contract when one configured model succeeds and others are skipped', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-success-contract-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const transcriptPath = path.join(tempRoot, 'transcript.md');
  const modelConfigPath = path.join(tempRoot, 'openclaw.json');

  await writeFile(transcriptPath, 'raw transcript only', 'utf8');
  await writeFile(modelConfigPath, JSON.stringify({
    agents: {
      defaults: {
        models: {
          'openai-codex/gpt-5.5': { alias: 'gpt55' },
        },
      },
    },
  }), 'utf8');

  const result = await runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async () => ({
      topic: 'Single model success',
      corePoints: ['point 1'],
      facts: ['fact 1'],
      angles: ['angle 1'],
      constraints: ['constraint 1'],
      generatedBy: 'outline-test-model',
    }),
    draftGenerator: async ({ model, outline, outlinePath }) => {
      const persistedOutline = JSON.parse(await readFile(outlinePath, 'utf8'));
      assert.equal(persistedOutline.topic, outline.topic);
      return `Draft by ${model.id} for ${outline.topic}`;
    },
  });

  assert.deepEqual(result, {
    status: 'success',
    outlinePath: path.join(deliverablesDir, 'rewrite', 'outline.json'),
    indexPath: path.join(deliverablesDir, 'rewrite', 'index.json'),
    feedbackPath: path.join(deliverablesDir, 'rewrite', 'feedback.json'),
    drafts: [
      {
        modelId: 'gpt5',
        modelLabel: 'GPT-5.5 via OpenAI Codex',
        status: 'success',
        path: path.join(deliverablesDir, 'rewrite', 'toutiao-gpt5.md'),
        durationMs: result.drafts[0].durationMs,
        error: null,
        reason: null,
        inputTokens: null,
        outputTokens: null,
      },
    ],
    errors: [],
  });
  assert.equal(typeof result.drafts[0].durationMs, 'number');
});

test('runRewriteForItem uses toutiao draft prefix when targetProfile is toutiao', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-toutiao-prefix-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const transcriptPath = path.join(tempRoot, 'transcript.md');
  const modelConfigPath = path.join(tempRoot, 'openclaw.json');

  await writeFile(transcriptPath, 'raw transcript only', 'utf8');
  await writeFile(modelConfigPath, JSON.stringify({
    agents: {
      defaults: {
        models: {
          'openai-codex/gpt-5.5': { alias: 'gpt55' },
        },
      },
    },
  }), 'utf8');

  const result = await runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    targetProfile: 'toutiao',
    analysisRecord: {
      formatName: '头条',
      formatFeatures: ['图文分段'],
      writingLogic: ['先抛问题'],
      expressionStyle: ['浅显易懂'],
      articleType: '图文稿',
      audience: '大众读者',
      platformTone: '头条图文',
      risks: ['不要编造数据'],
    },
    outlineGenerator: async () => ({
      topic: '头条图文主题',
      corePoints: ['point 1'],
      facts: ['fact 1'],
      angles: ['angle 1'],
      constraints: ['constraint 1'],
      generatedBy: 'outline-test-model',
    }),
    draftGenerator: async () => '标题1：切口一，重点二，结论三\n\n正文\n——END——',
  });

  assert.equal(result.drafts[0].path, path.join(deliverablesDir, 'rewrite', 'toutiao-gpt5.md'));
  assert.equal(await readFile(result.drafts[0].path, 'utf8'), '标题1：切口一，重点二，结论三\n\n正文\n——END——');
});

test('runRewriteForItem fails before draft generation when outline generation is unusable', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-outline-failure-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const transcriptPath = path.join(tempRoot, 'transcript.md');
  let draftCallCount = 0;

  await writeFile(transcriptPath, 'raw transcript only', 'utf8');

  const result = await runRewriteForItem({
    workspaceDir: tempRoot,
    jobId: 'job-3',
    input: { url: 'https://example.com/video' },
    transcriptPath,
    deliverablesDir,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async () => ({
      topic: 'Incomplete outline',
      corePoints: 'not-an-array',
    }),
    draftGenerator: async () => {
      draftCallCount += 1;
    },
  });

  await assert.rejects(readFile(path.join(deliverablesDir, 'rewrite', 'outline.json'), 'utf8'));
  assert.equal(result.status, 'failed');
  assert.equal(draftCallCount, 0);
  assert.deepEqual(result.errors, [
    {
      stage: 'outline',
      code: 'outline_generation_failed',
      message: 'Outline generation returned invalid corePoints',
    },
  ]);
  assert.equal(await readFile(transcriptPath, 'utf8'), 'raw transcript only');
});

test('parseJsonObject tolerates fenced JSON with surrounding prose and trailing commas', async () => {
  const payload = [
    'Here is the outline JSON:',
    '```json',
    '{',
    '  "topic": "测试主题",',
    '  "corePoints": ["点一",],',
    '  "facts": ["事实一",],',
    '  "angles": ["角度一",],',
    '  "constraints": ["约束一",],',
    '  "generatedBy": "openai-codex/gpt-5.5",',
    '}',
    '```',
    'Use it carefully.',
  ].join('\n');

  assert.deepEqual(parseJsonObject(payload), {
    topic: '测试主题',
    corePoints: ['点一'],
    facts: ['事实一'],
    angles: ['角度一'],
    constraints: ['约束一'],
    generatedBy: 'openai-codex/gpt-5.5',
  });
});

test('runRewriteForItem removes stale outline and index artifacts when a rerun fails during outline generation', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-stale-outline-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const transcriptPath = path.join(tempRoot, 'transcript.md');
  const modelConfigPath = path.join(tempRoot, 'openclaw.json');
  const outlinePath = path.join(deliverablesDir, 'rewrite', 'outline.json');
  const indexPath = path.join(deliverablesDir, 'rewrite', 'index.json');

  await writeFile(transcriptPath, 'raw transcript only', 'utf8');
  await writeFile(modelConfigPath, JSON.stringify({
    agents: {
      defaults: {
        models: {
          'openai-codex/gpt-5.5': { alias: 'gpt55' },
        },
      },
    },
  }), 'utf8');

  await runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async () => ({
      topic: 'First run topic',
      corePoints: ['point 1'],
      facts: ['fact 1'],
      angles: ['angle 1'],
      constraints: ['constraint 1'],
      generatedBy: 'outline-test-model',
    }),
    draftGenerator: async ({ model, outline }) => `Draft by ${model.id} for ${outline.topic}`,
  });

  assert.equal(typeof JSON.parse(await readFile(outlinePath, 'utf8')).topic, 'string');
  assert.equal(typeof JSON.parse(await readFile(indexPath, 'utf8')).outlinePath, 'string');

  const result = await runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async () => ({
      topic: 'Broken outline',
      corePoints: 'not-an-array',
    }),
  });

  assert.equal(result.status, 'failed');
  await assert.rejects(readFile(outlinePath, 'utf8'));
  await assert.rejects(readFile(indexPath, 'utf8'));
});

test('runRewriteForItem writes per-model draft files and index.json after outline persistence', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-drafts-success-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const transcriptPath = path.join(tempRoot, 'transcript.md');
  const modelConfigPath = path.join(tempRoot, 'openclaw.json');
  const draftCalls = [];

  await writeFile(transcriptPath, 'raw transcript only', 'utf8');
  await writeFile(modelConfigPath, JSON.stringify({
    agents: {
      defaults: {
        models: {
          'openai-codex/gpt-5.5': { alias: 'gpt55' },
        },
      },
    },
  }), 'utf8');

  const result = await runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async () => ({
      topic: 'Shared outline topic',
      corePoints: ['point 1'],
      facts: ['fact 1'],
      angles: ['angle 1'],
      constraints: ['constraint 1'],
      generatedBy: 'outline-test-model',
    }),
    draftGenerator: async ({ model, outline, outlinePath, transcriptText, sourcePath, sourceType }) => {
      const persistedOutline = JSON.parse(await readFile(outlinePath, 'utf8'));

      draftCalls.push({
        modelId: model.id,
        outline,
        persistedOutline,
        transcriptText,
        sourcePath,
        sourceType,
      });

      return `# ${model.label}\n\nHook for ${outline.topic}`;
    },
  });

  const indexPath = path.join(deliverablesDir, 'rewrite', 'index.json');
  const index = JSON.parse(await readFile(indexPath, 'utf8'));
  const gptDraftPath = path.join(deliverablesDir, 'rewrite', 'toutiao-gpt5.md');

  assert.equal(result.status, 'success');
  assert.equal(result.indexPath, indexPath);
  assert.equal(index.outlinePath, result.outlinePath);
  assert.deepEqual(
    index.drafts.map(({ modelId, status, path: draftPath, reason }) => ({ modelId, status, path: draftPath, reason })),
    [
      { modelId: 'gpt5', status: 'success', path: gptDraftPath, reason: null },
    ],
  );
  assert.equal(await readFile(gptDraftPath, 'utf8'), '# GPT-5.5 via OpenAI Codex\n\nHook for Shared outline topic');
  assert.deepEqual(
    draftCalls.map(({ modelId }) => modelId).sort(),
    ['gpt5'],
  );

  for (const draftCall of draftCalls) {
    assert.deepEqual(draftCall.outline, draftCall.persistedOutline);
    assert.equal(draftCall.transcriptText, undefined);
    assert.equal(draftCall.sourcePath, undefined);
    assert.equal(draftCall.sourceType, undefined);
  }
});

test('runRewriteForItem starts multiple draft generators before any resolve and reports partial_success on single-model failure', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-drafts-partial-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const transcriptPath = path.join(tempRoot, 'transcript.md');
  const modelConfigPath = path.join(tempRoot, 'openclaw.json');
  const startedModels = [];
  const settledModels = [];
  let releaseDrafts;
  const releaseDraftsPromise = new Promise((resolve) => {
    releaseDrafts = resolve;
  });
  let allDraftsStarted;
  const allDraftsStartedPromise = new Promise((resolve) => {
    allDraftsStarted = resolve;
  });

  await writeFile(transcriptPath, 'raw transcript only', 'utf8');
  await writeFile(modelConfigPath, JSON.stringify({
    agents: {
      defaults: {
        models: {
          'openai-codex/gpt-5.5': { alias: 'gpt55' },
        },
      },
    },
  }), 'utf8');

  const resultPromise = runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async () => ({
      topic: 'Shared outline topic',
      corePoints: ['point 1'],
      facts: ['fact 1'],
      angles: ['angle 1'],
      constraints: ['constraint 1'],
      generatedBy: 'outline-test-model',
    }),
    draftGenerator: async ({ model, outline }) => {
      startedModels.push(model.id);

      if (startedModels.length === 1) {
        allDraftsStarted();
      }

      await releaseDraftsPromise;
      settledModels.push(model.id);

      if (model.id === 'gpt5') {
        throw new Error(`draft failed for ${model.id}`);
      }

      return `Draft by ${model.id} for ${outline.topic}`;
    },
  });

  await allDraftsStartedPromise;
  assert.deepEqual(startedModels.sort(), ['gpt5']);
  assert.deepEqual(settledModels, []);

  releaseDrafts();

  const result = await resultPromise;
  const index = JSON.parse(await readFile(path.join(deliverablesDir, 'rewrite', 'index.json'), 'utf8'));

  assert.equal(result.status, 'failed');
  assert.deepEqual(settledModels.sort(), ['gpt5']);
  assert.equal(index.drafts.find((draft) => draft.modelId === 'gpt5').status, 'failed');
  assert.equal(index.drafts.find((draft) => draft.modelId === 'gpt5').error, 'draft failed for gpt5');
  await assert.rejects(readFile(path.join(deliverablesDir, 'rewrite', 'toutiao-gpt5.md'), 'utf8'));
});

test('runRewriteForItem clears stale draft file when a previous success becomes skipped', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-stale-skipped-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const transcriptPath = path.join(tempRoot, 'transcript.md');
  const modelConfigPath = path.join(tempRoot, 'openclaw.json');
  const gptDraftPath = path.join(deliverablesDir, 'rewrite', 'toutiao-gpt5.md');
  
  await writeFile(transcriptPath, 'raw transcript only', 'utf8');
  await writeFile(modelConfigPath, JSON.stringify({
    agents: {
      defaults: {
        models: {
          'openai-codex/gpt-5.5': { alias: 'gpt55' },
        },
      },
    },
  }), 'utf8');

  await runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async () => ({
      topic: 'First run topic',
      corePoints: ['point 1'],
      facts: ['fact 1'],
      angles: ['angle 1'],
      constraints: ['constraint 1'],
      generatedBy: 'outline-test-model',
    }),
    draftGenerator: async ({ model, outline }) => `Draft by ${model.id} for ${outline.topic}`,
  });

  assert.equal(await readFile(gptDraftPath, 'utf8'), 'Draft by gpt5 for First run topic');

  await writeFile(modelConfigPath, JSON.stringify({ agents: { defaults: { models: {} } } }), 'utf8');

  await runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async () => ({
      topic: 'Second run topic',
      corePoints: ['point 2'],
      facts: ['fact 2'],
      angles: ['angle 2'],
      constraints: ['constraint 2'],
      generatedBy: 'outline-test-model',
    }),
  });

  await assert.rejects(readFile(gptDraftPath, 'utf8'));
});

test('runRewriteForItem clears stale draft file when a previous success becomes failed', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-stale-failed-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const transcriptPath = path.join(tempRoot, 'transcript.md');
  const modelConfigPath = path.join(tempRoot, 'openclaw.json');
  const gptDraftPath = path.join(deliverablesDir, 'rewrite', 'toutiao-gpt5.md');
  
  await writeFile(transcriptPath, 'raw transcript only', 'utf8');
  await writeFile(modelConfigPath, JSON.stringify({
    agents: {
      defaults: {
        models: {
          'openai-codex/gpt-5.5': { alias: 'gpt55' },
        },
      },
    },
  }), 'utf8');

  await runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async () => ({
      topic: 'First run topic',
      corePoints: ['point 1'],
      facts: ['fact 1'],
      angles: ['angle 1'],
      constraints: ['constraint 1'],
      generatedBy: 'outline-test-model',
    }),
    draftGenerator: async ({ model, outline }) => `Draft by ${model.id} for ${outline.topic}`,
  });

  assert.equal(await readFile(gptDraftPath, 'utf8'), 'Draft by gpt5 for First run topic');

  await runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async () => ({
      topic: 'Second run topic',
      corePoints: ['point 2'],
      facts: ['fact 2'],
      angles: ['angle 2'],
      constraints: ['constraint 2'],
      generatedBy: 'outline-test-model',
    }),
    draftGenerator: async ({ model }) => {
      if (model.id === 'gpt5') {
        throw new Error('draft failed for gpt5');
      }

      return `Draft by ${model.id}`;
    },
  });

  await assert.rejects(readFile(gptDraftPath, 'utf8'));
});

test('runRewriteForItem removes stale index artifact when a rerun fails during index persistence', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-stale-index-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const transcriptPath = path.join(tempRoot, 'transcript.md');
  const modelConfigPath = path.join(tempRoot, 'openclaw.json');
  const outlinePath = path.join(deliverablesDir, 'rewrite', 'outline.json');
  const indexPath = path.join(deliverablesDir, 'rewrite', 'index.json');

  await writeFile(transcriptPath, 'raw transcript only', 'utf8');
  await writeFile(modelConfigPath, JSON.stringify({
    agents: {
      defaults: {
        models: {
          'openai-codex/gpt-5.5': { alias: 'gpt55' },
        },
      },
    },
  }), 'utf8');

  await runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async () => ({
      topic: 'First run topic',
      corePoints: ['point 1'],
      facts: ['fact 1'],
      angles: ['angle 1'],
      constraints: ['constraint 1'],
      generatedBy: 'outline-test-model',
    }),
    draftGenerator: async ({ model, outline }) => `Draft by ${model.id} for ${outline.topic}`,
  });

  assert.equal(typeof JSON.parse(await readFile(indexPath, 'utf8')).outlinePath, 'string');

  const result = await runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async () => ({
      topic: 'Second run topic',
      corePoints: ['point 2'],
      facts: ['fact 2'],
      angles: ['angle 2'],
      constraints: ['constraint 2'],
      generatedBy: 'outline-test-model',
    }),
    draftGenerator: async ({ model, outline }) => `Draft by ${model.id} for ${outline.topic}`,
    indexWriter: async () => {
      throw {
        stage: 'draft_index',
        code: 'index_persistence_failed',
        message: 'Failed to persist rewrite index',
      };
    },
  });

  assert.equal(result.status, 'failed');
  assert.equal(typeof JSON.parse(await readFile(outlinePath, 'utf8')).topic, 'string');
  await assert.rejects(readFile(indexPath, 'utf8'));
});

test('runRewriteForItem fails with structured error when feedback.json is malformed', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-bad-feedback-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const transcriptPath = path.join(tempRoot, 'transcript.md');
  const modelConfigPath = path.join(tempRoot, 'openclaw.json');
  const feedbackPath = path.join(deliverablesDir, 'rewrite', 'feedback.json');

  await writeFile(transcriptPath, 'raw transcript only', 'utf8');
  await writeFile(modelConfigPath, JSON.stringify({
    agents: {
      defaults: {
        models: {
          'openai-codex/gpt-5.5': { alias: 'gpt55' },
        },
      },
    },
  }), 'utf8');

  await runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async () => ({
      topic: 'First run topic',
      corePoints: ['point 1'],
      facts: ['fact 1'],
      angles: ['angle 1'],
      constraints: ['constraint 1'],
      generatedBy: 'outline-test-model',
    }),
    draftGenerator: async ({ model, outline }) => `Draft by ${model.id} for ${outline.topic}`,
  });

  await writeFile(feedbackPath, '{bad json', 'utf8');

  const result = await runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async () => ({
      topic: 'Second run topic',
      corePoints: ['point 2'],
      facts: ['fact 2'],
      angles: ['angle 2'],
      constraints: ['constraint 2'],
      generatedBy: 'outline-test-model',
    }),
  });

  assert.equal(result.status, 'failed');
  assert.deepEqual(result.errors, [
    {
      stage: 'feedback',
      code: 'feedback_persistence_failed',
      message: `Malformed feedback file: ${feedbackPath}`,
    },
  ]);
  assert.equal(await readFile(feedbackPath, 'utf8'), '{bad json');
});

test('runRewriteForItem preserves draft metadata and reports index persistence failures with post-outline stage/code', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-index-failure-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const transcriptPath = path.join(tempRoot, 'transcript.md');
  const modelConfigPath = path.join(tempRoot, 'openclaw.json');

  await writeFile(transcriptPath, 'raw transcript only', 'utf8');
  await writeFile(modelConfigPath, JSON.stringify({
    agents: {
      defaults: {
        models: {
          'openai-codex/gpt-5.5': { alias: 'gpt55' },
        },
      },
    },
  }), 'utf8');

  const result = await runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async () => ({
      topic: 'Shared outline topic',
      corePoints: ['point 1'],
      facts: ['fact 1'],
      angles: ['angle 1'],
      constraints: ['constraint 1'],
      generatedBy: 'outline-test-model',
    }),
    draftGenerator: async ({ model, outline }) => `Draft by ${model.id} for ${outline.topic}`,
    indexWriter: async () => {
      throw {
        stage: 'draft_index',
        code: 'index_persistence_failed',
        message: 'Failed to persist rewrite index',
      };
    },
  });

  assert.equal(result.status, 'failed');
  assert.equal(result.outlinePath, path.join(deliverablesDir, 'rewrite', 'outline.json'));
  assert.equal(result.indexPath, path.join(deliverablesDir, 'rewrite', 'index.json'));
  assert.deepEqual(result.drafts, [
    {
      modelId: 'gpt5',
      modelLabel: 'GPT-5.5 via OpenAI Codex',
      status: 'success',
      path: path.join(deliverablesDir, 'rewrite', 'toutiao-gpt5.md'),
      durationMs: result.drafts[0].durationMs,
      error: null,
      reason: null,
      inputTokens: null,
      outputTokens: null,
    },
  ]);
  assert.equal(typeof result.drafts[0].durationMs, 'number');
  assert.deepEqual(result.errors, [
    {
      stage: 'draft_index',
      code: 'index_persistence_failed',
      message: 'Failed to persist rewrite index',
    },
  ]);
  assert.equal(await readFile(path.join(deliverablesDir, 'rewrite', 'toutiao-gpt5.md'), 'utf8'), 'Draft by gpt5 for Shared outline topic');
  await assert.rejects(readFile(path.join(deliverablesDir, 'rewrite', 'index.json'), 'utf8'));
});

test('runRewriteForItem does not report success when all draft candidates are skipped', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'rewrite-all-skipped-'));
  const deliverablesDir = path.join(tempRoot, 'deliverables');
  const transcriptPath = path.join(tempRoot, 'transcript.md');
  const modelConfigPath = path.join(tempRoot, 'openclaw.json');

  await writeFile(transcriptPath, 'raw transcript only', 'utf8');
  await writeFile(modelConfigPath, JSON.stringify({ agents: { defaults: { models: {} } } }), 'utf8');

  const result = await runRewriteForItem({
    transcriptPath,
    deliverablesDir,
    modelConfigPath,
    geminiCliPath: getMissingGeminiCliPath(tempRoot),
    outlineGenerator: async () => ({
      topic: 'Shared outline topic',
      corePoints: ['point 1'],
      facts: ['fact 1'],
      angles: ['angle 1'],
      constraints: ['constraint 1'],
      generatedBy: 'outline-test-model',
    }),
  });

  const index = JSON.parse(await readFile(path.join(deliverablesDir, 'rewrite', 'index.json'), 'utf8'));

  assert.equal(result.status, 'failed');
  assert.equal(result.errors.length, 0);
  assert.deepEqual(index.drafts.map((draft) => draft.status), ['skipped']);
});
