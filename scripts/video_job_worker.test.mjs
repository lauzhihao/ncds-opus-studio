import test from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { mkdtemp, mkdir, writeFile, readdir, utimes } from 'node:fs/promises';

import {
  buildCompletionLine,
  buildCompletionMessage,
  buildStartupProgressMessages,
  buildTaskSummary,
  buildLocalDeliverableSummaryMarkdown,
  buildResultsPayload,
  buildFeishuMessageTarget,
  cleanupExpiredJobs,
  finalizeFailedJobArtifacts,
  buildPipelineArgs,
  consumePipelineLine,
  getJobLayout,
  loadPipelineResult,
  mergePipelineResult,
  normalizeCreatedTask,
  parsePipelineSuccessLine,
  resolvePythonBin,
  buildHighlightPrompt,
  runHighlightStage,
} from './video_job_worker.mjs';
import { buildPublicPermissionData } from './feishu_sdk_adapter.mjs';

test('getJobLayout returns normalized task directories', () => {
  const layout = getJobLayout('/tmp/workspace', 'vj_demo123');

  assert.equal(layout.jobsDir, path.join('/tmp/workspace', 'video-jobs'));
  assert.equal(layout.jobDir, path.join('/tmp/workspace', 'video-jobs', 'vj_demo123'));
  assert.equal(layout.jobPath, path.join('/tmp/workspace', 'video-jobs', 'vj_demo123', 'job.json'));
  assert.equal(layout.rawDir, path.join('/tmp/workspace', 'video-jobs', 'vj_demo123', 'raw'));
  assert.equal(layout.deliverablesDir, path.join('/tmp/workspace', 'video-jobs', 'vj_demo123', 'deliverables'));
});

test('cleanupExpiredJobs selects only terminal entries older than retention window', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'openclaw-jobs-'));
  const jobsDir = path.join(tempRoot, 'video-jobs');
  await mkdir(jobsDir, { recursive: true });

  const oldCompletedDir = path.join(jobsDir, 'vj_old_completed');
  await mkdir(oldCompletedDir, { recursive: true });
  await writeFile(path.join(oldCompletedDir, 'job.json'), JSON.stringify({ state: 'completed' }), 'utf8');

  const oldRunningDir = path.join(jobsDir, 'vj_old_running');
  await mkdir(oldRunningDir, { recursive: true });
  await writeFile(path.join(oldRunningDir, 'job.json'), JSON.stringify({ state: 'running' }), 'utf8');

  const oldJson = path.join(jobsDir, 'vj_old.json');
  await writeFile(oldJson, JSON.stringify({ state: 'failed' }), 'utf8');

  const recentCompletedDir = path.join(jobsDir, 'vj_recent_completed');
  await mkdir(recentCompletedDir, { recursive: true });
  await writeFile(path.join(recentCompletedDir, 'job.json'), JSON.stringify({ state: 'completed_with_errors' }), 'utf8');

  const now = Date.now();
  const oldTime = new Date(now - (9 * 24 * 60 * 60 * 1000));
  const recentTime = new Date(now - (2 * 24 * 60 * 60 * 1000));

  await utimes(oldCompletedDir, oldTime, oldTime);
  await utimes(path.join(oldCompletedDir, 'job.json'), oldTime, oldTime);
  await utimes(oldRunningDir, oldTime, oldTime);
  await utimes(path.join(oldRunningDir, 'job.json'), oldTime, oldTime);
  await utimes(oldJson, oldTime, oldTime);
  await utimes(recentCompletedDir, recentTime, recentTime);
  await utimes(path.join(recentCompletedDir, 'job.json'), recentTime, recentTime);

  const trashed = [];
  const deleted = await cleanupExpiredJobs(jobsDir, {
    now,
    retentionDays: 7,
    trashPaths: async (paths) => {
      trashed.push(...paths.map((item) => path.basename(item)));
    },
  });

  assert.deepEqual(deleted.map((item) => path.basename(item)).sort(), ['vj_old.json', 'vj_old_completed']);
  assert.deepEqual(trashed.sort(), ['vj_old.json', 'vj_old_completed']);

  const remaining = (await readdir(jobsDir)).sort();
  assert.deepEqual(remaining, ['vj_old.json', 'vj_old_completed', 'vj_old_running', 'vj_recent_completed']);
});

test('parsePipelineSuccessLine returns videoPath for download success lines', () => {
  assert.deepEqual(
    parsePipelineSuccessLine('✅ 下载: /tmp/job/raw/podcast_demo.m4a'),
    { videoPath: '/tmp/job/raw/podcast_demo.m4a' },
  );
});

test('parsePipelineSuccessLine returns transcriptPath for transcription success lines', () => {
  assert.deepEqual(
    parsePipelineSuccessLine('✅ 转写: /tmp/job/raw/podcast_demo.txt'),
    { transcriptPath: '/tmp/job/raw/podcast_demo.txt' },
  );
});

test('parsePipelineSuccessLine returns polishedTranscriptPath for polished success lines', () => {
  assert.deepEqual(
    parsePipelineSuccessLine('✅ 清洗稿: /tmp/job/deliverables/podcast_demo.polished.txt'),
    { polishedTranscriptPath: '/tmp/job/deliverables/podcast_demo.polished.txt' },
  );
});

test('parsePipelineSuccessLine returns null for unrelated lines', () => {
  assert.equal(parsePipelineSuccessLine('下载中...'), null);
});

test('parsePipelineSuccessLine treats rewrite success lines as non-final progress lines', () => {
  assert.equal(parsePipelineSuccessLine('✅ 复写稿: /tmp/job/deliverables/rewrite.md'), null);
  assert.equal(parsePipelineSuccessLine('✅ 润色(opus): /tmp/job/deliverables/polished.md'), null);
  assert.equal(parsePipelineSuccessLine('✅ 改写(opus): /tmp/job/deliverables/rewrite.md'), null);
  assert.equal(parsePipelineSuccessLine('✅ 润色: /tmp/job/deliverables/polished.md'), null);
  assert.equal(parsePipelineSuccessLine('✅ 改写: /tmp/job/deliverables/rewrite.md'), null);
});

test('parsePipelineSuccessLine returns null for empty success paths', () => {
  assert.equal(parsePipelineSuccessLine('✅ 下载:'), null);
  assert.equal(parsePipelineSuccessLine('✅ 下载:   '), null);
  assert.equal(parsePipelineSuccessLine('✅ 转写:'), null);
  assert.equal(parsePipelineSuccessLine('✅ 转写:   '), null);
});

test('consumePipelineLine applies parsed success lines to worker state and progress', () => {
  const item = { input: 'demo', mode: 'fast', rawLines: [] };

  assert.deepEqual(
    consumePipelineLine(item, '✅ 下载: /tmp/job/raw/podcast_demo.m4a', 'fast'),
    {
      message: '下载完成: /tmp/job/raw/podcast_demo.m4a',
      patch: { currentStage: 'download_done' },
    },
  );
  assert.equal(item.videoPath, '/tmp/job/raw/podcast_demo.m4a');

  assert.deepEqual(
    consumePipelineLine(item, '✅ 转写: /tmp/job/raw/podcast_demo.txt', 'fast'),
    {
      message: '转写完成: /tmp/job/raw/podcast_demo.txt',
      patch: { currentStage: 'transcribe_done' },
    },
  );
  assert.equal(item.transcriptPath, '/tmp/job/raw/podcast_demo.txt');

  assert.deepEqual(
    consumePipelineLine(item, '✅ 清洗稿: /tmp/job/deliverables/podcast_demo.polished.txt', 'fast'),
    {
      message: '清洗稿已生成: /tmp/job/deliverables/podcast_demo.polished.txt',
      patch: { currentStage: 'polish_done' },
    },
  );
  assert.equal(item.polishedTranscriptPath, '/tmp/job/deliverables/podcast_demo.polished.txt');
});

test('consumePipelineLine ignores non-contract success labels for path fields', () => {
  const item = { input: 'demo', mode: 'fast', rawLines: [] };

  assert.equal(consumePipelineLine(item, '✅ 下载完成: /tmp/job/raw/podcast_demo.m4a', 'fast'), null);
  assert.equal(consumePipelineLine(item, '✅ 转写完成: /tmp/job/raw/podcast_demo.txt', 'fast'), null);
  assert.equal(consumePipelineLine(item, '✅ 视频: /tmp/job/raw/podcast_demo.m4a', 'fast'), null);

  assert.equal(item.videoPath, undefined);
  assert.equal(item.transcriptPath, undefined);
});

test('buildPublicPermissionData creates organization-readable settings', () => {
  assert.deepEqual(buildPublicPermissionData(), {
    external_access_entity: 'closed',
    security_entity: 'anyone_can_view',
    comment_entity: 'anyone_can_view',
    share_entity: 'same_tenant',
    link_share_entity: 'tenant_readable',
  });
});

test('buildFeishuMessageTarget prefers open_id when chatId carries a user prefix', () => {
  assert.deepEqual(buildFeishuMessageTarget({
    chatId: 'user:ou_demo_sender',
    senderOpenId: null,
  }), {
    receiveId: 'ou_demo_sender',
    receiveIdType: 'open_id',
  });
});

test('buildFeishuMessageTarget sends group progress back to the source chat', () => {
  assert.deepEqual(buildFeishuMessageTarget({
    chatId: 'oc_demo_group_chat',
    chatType: 'group',
    senderOpenId: 'ou_demo_sender',
  }), {
    receiveId: 'oc_demo_group_chat',
    receiveIdType: 'chat_id',
  });
});

test('buildFeishuMessageTarget falls back to chat_id and returns null without any target', () => {
  assert.deepEqual(buildFeishuMessageTarget({
    chatId: 'oc_demo_chat',
    senderOpenId: null,
  }), {
    receiveId: 'oc_demo_chat',
    receiveIdType: 'chat_id',
  });

  assert.equal(buildFeishuMessageTarget({
    chatId: null,
    senderOpenId: null,
  }), null);
});

test('video job worker accepts legacy urls payloads via normalized inputs fallback', async () => {
  const legacyRawPayload = {
    jobId: 'vj_demo',
    urls: ['https://lnns.co/demo'],
  };

  const normalizedPayload = {
    ...legacyRawPayload,
    inputs: Array.isArray(legacyRawPayload.inputs)
      ? legacyRawPayload.inputs
      : Array.isArray(legacyRawPayload.urls)
        ? legacyRawPayload.urls
        : [],
  };

  assert.deepEqual(normalizedPayload.inputs, ['https://lnns.co/demo']);
});

test('buildStartupProgressMessages emits immediate acknowledgement before detailed start message', () => {
  assert.deepEqual(buildStartupProgressMessages(1), [
    '已收到任务，正在启动媒体任务...',
    '任务已启动，共 1 个媒体',
  ]);

  assert.deepEqual(buildStartupProgressMessages(0), [
    '已收到任务，正在启动媒体任务...',
    '任务已启动，共 0 个媒体',
  ]);

  assert.deepEqual(buildStartupProgressMessages(2, 'video'), [
    '已收到任务，正在启动媒体任务...',
    '任务已启动，共 2 个视频',
  ]);

  assert.deepEqual(buildStartupProgressMessages(3, 'audio'), [
    '已收到任务，正在启动媒体任务...',
    '任务已启动，共 3 个音频',
  ]);
});

test('normalizeCreatedTask accepts multiple Feishu task response shapes', () => {
  assert.deepEqual(
    normalizeCreatedTask({
      data: {
        task: {
          guid: 'guid_demo',
          url: 'https://applink.feishu.cn/client/todo/detail?guid=guid_demo',
          id: 't100001',
        },
      },
    }),
    {
      guid: 'guid_demo',
      url: 'https://applink.feishu.cn/client/todo/detail?guid=guid_demo',
      id: 't100001',
    },
  );

  assert.deepEqual(
    normalizeCreatedTask({
      task: {
        guid: 'guid_sdk',
        url: 'https://applink.feishu.cn/client/todo/detail?guid=guid_sdk',
        id: 't100003',
      },
    }),
    {
      guid: 'guid_sdk',
      url: 'https://applink.feishu.cn/client/todo/detail?guid=guid_sdk',
      id: 't100003',
    },
  );

  assert.deepEqual(
    normalizeCreatedTask({
      data: {
        task_guid: 'guid_alt',
        task_url: 'https://applink.feishu.cn/client/todo/detail?guid=guid_alt',
        task_id: 't100002',
      },
    }),
    {
      guid: 'guid_alt',
      url: 'https://applink.feishu.cn/client/todo/detail?guid=guid_alt',
      id: 't100002',
    },
  );
});

test('buildCompletionLine falls back to feishu doc url when present', () => {
  assert.equal(
    buildCompletionLine({
      input: 'https://example.com/video',
      doc: { url: 'https://feishu.cn/docx/rawdoc' },
    }, 0),
    '1. 完成 | https://example.com/video | https://feishu.cn/docx/rawdoc',
  );
});

test('buildCompletionMessage includes folder permission hints', () => {
  const message = buildCompletionMessage([
    {
      input: 'https://example.com/video',
      transcriptPath: '/tmp/job/transcript.txt',
    },
  ], {
    artifactFolderUrl: 'https://feishu.cn/drive/folder/demo',
    artifactFolderPermissionWarning: 'public permission unsupported for type: folder',
    artifactFolderMemberPermissionError: 'member add failed',
  }, { jobId: 'vj_demo' });

  assert.equal(message.includes('1. 完成 | https://example.com/video | /tmp/job/transcript.txt'), true);
  assert.equal(message.includes('产物目录: https://feishu.cn/drive/folder/demo'), true);
  assert.equal(message.includes('产物目录权限提示: public permission unsupported for type: folder'), false);
  assert.equal(message.includes('产物目录授权异常: member add failed'), true);
});

test('buildTaskSummary includes current stage while running', () => {
  const summary = buildTaskSummary({
    state: 'running',
    currentStage: 'highlight',
    results: [],
  });

  assert.equal(summary.includes('提取爆款精华中'), true);
});

test('loadPipelineResult reads deliverables result payload from raw output directory', async () => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'openclaw-pipeline-result-'));
  const rawDir = path.join(tempRoot, 'video-jobs', 'vj_demo123', 'raw');
  const deliverablesDir = path.join(tempRoot, 'video-jobs', 'vj_demo123', 'deliverables');
  await mkdir(rawDir, { recursive: true });
  await mkdir(deliverablesDir, { recursive: true });

  const expected = {
    transcript: '/tmp/job/raw/transcript.txt',
    rewritePath: '/tmp/job/deliverables/rewrite.md',
  };
  await writeFile(path.join(deliverablesDir, 'result.json'), JSON.stringify(expected), 'utf8');

  assert.deepEqual(await loadPipelineResult(rawDir), expected);
});

test('mergePipelineResult maps transcript objects and preserves variant shapes', () => {
  const item = { input: 'demo', mode: 'hq', rawLines: [] };

  mergePipelineResult(item, {
    transcript: {
      rawTextPath: '/tmp/job/raw/transcript.txt',
    },
    polishedTranscriptPath: '/tmp/job/deliverables/polished.md',
    rewritePath: '/tmp/job/deliverables/rewrite.md',
    selectedPolishedModelId: 'opus',
    selectedRewriteModelId: 'opus',
    failureReasons: {
      polished: { sonnet: 'rate_limit' },
      rewrite: { sonnet: 'empty_output' },
    },
    polishedVariants: [
      {
        modelId: 'opus',
        path: '/tmp/job/deliverables/polished-opus.md',
        status: 'success',
        errorKind: null,
        reason: null,
        ignoredExtraField: true,
      },
    ],
    rewriteVariants: [
      {
        modelId: 'opus',
        path: '/tmp/job/deliverables/rewrite-opus.md',
        status: 'success',
        errorKind: null,
        reason: null,
        notes: 'keep full payload shape on required fields',
      },
      {
        modelId: 'sonnet',
        path: null,
        status: 'failed',
        errorKind: 'empty_output',
        reason: 'model returned empty content',
      },
    ],
  });

  assert.equal(item.transcriptPath, '/tmp/job/raw/transcript.txt');
  assert.equal(item.polishedTranscriptPath, '/tmp/job/deliverables/polished.md');
  assert.equal(item.rewritePath, '/tmp/job/deliverables/rewrite.md');
  assert.equal(item.selectedPolishedModelId, 'opus');
  assert.equal(item.selectedRewriteModelId, 'opus');
  assert.deepEqual(item.failureReasons, {
    polished: { sonnet: 'rate_limit' },
    rewrite: { sonnet: 'empty_output' },
  });
  assert.deepEqual(item.polishedVariants, [
    {
      modelId: 'opus',
      path: '/tmp/job/deliverables/polished-opus.md',
      status: 'success',
      errorKind: null,
      reason: null,
    },
  ]);
  assert.deepEqual(item.rewriteVariants, [
    {
      modelId: 'opus',
      path: '/tmp/job/deliverables/rewrite-opus.md',
      status: 'success',
      errorKind: null,
      reason: null,
    },
    {
      modelId: 'sonnet',
      path: null,
      status: 'failed',
      errorKind: 'empty_output',
      reason: 'model returned empty content',
    },
  ]);
});

test('mergePipelineResult stays backward compatible with string transcript payloads and old stdout fields', () => {
  const item = {
    input: 'demo',
    mode: 'fast',
    rawLines: [],
    transcriptPath: '/tmp/job/raw/from-stdout.txt',
  };

  mergePipelineResult(item, {
    transcript: '/tmp/job/raw/from-result.txt',
    polishedVariants: null,
    rewriteVariants: undefined,
  });

  assert.equal(item.transcriptPath, '/tmp/job/raw/from-result.txt');
  assert.deepEqual(item.polishedVariants, []);
  assert.deepEqual(item.rewriteVariants, []);

  const fallbackItem = { input: 'demo', mode: 'fast', rawLines: [] };
  consumePipelineLine(fallbackItem, '✅ 转写: /tmp/job/raw/fallback.txt', 'fast');
  assert.equal(fallbackItem.transcriptPath, '/tmp/job/raw/fallback.txt');
});

test('buildPipelineArgs targets the provided job root for pipeline output', () => {
  assert.deepEqual(
    buildPipelineArgs('https://example.com/video', '/tmp/job'),
    [
      '/Users/ncds/.openclaw/workspaces/xiaozhua/skills/video-pipeline/scripts/video_pipeline.py',
      '--output',
      '/tmp/job',
      'https://example.com/video',
    ],
  );
});

test('resolvePythonBin prefers OPENCLAW_PYTHON when configured', () => {
  assert.equal(
    resolvePythonBin(
      { OPENCLAW_PYTHON: '/custom/python3' },
      { exists: () => false },
    ),
    '/custom/python3',
  );
});

test('resolvePythonBin prefers Homebrew python before system python when env is unset', () => {
  assert.equal(
    resolvePythonBin(
      {},
      {
        exists: (candidate) => candidate === '/opt/homebrew/bin/python3' || candidate === '/usr/bin/python3',
      },
    ),
    '/opt/homebrew/bin/python3',
  );
});

test('resolvePythonBin prefers resolved python3 path before fixed candidates', () => {
  assert.equal(
    resolvePythonBin(
      { PATH: '/custom/bin:/usr/bin' },
      {
        which: () => '/custom/bin/python3',
        exists: (candidate) => candidate === '/custom/bin/python3' || candidate === '/usr/bin/python3',
      },
    ),
    '/custom/bin/python3',
  );
});

test('resolvePythonBin falls back to python3 when no absolute candidate exists', () => {
  assert.equal(
    resolvePythonBin(
      {},
      { exists: () => false },
    ),
    'python3',
  );
});

test('buildLocalDeliverableSummaryMarkdown surfaces transcripts, highlight doc, and sub-task id', () => {
  const summary = buildLocalDeliverableSummaryMarkdown([
    {
      input: 'https://douyin.com/video/1',
      title: '视频一',
      platform: 'douyin',
      subTaskId: 'ASR_demo_1',
      transcriptPath: '/tmp/job/raw/demo.txt',
      polishedTranscriptPath: '/tmp/job/deliverables/demo.polished.txt',
      doc: { url: 'https://feishu.example/docx/transcript123' },
      highlightDoc: { url: 'https://feishu.example/docx/highlight1' },
    },
  ], {
    state: 'completed',
    completedAt: '2026-03-19T12:00:00.000Z',
    artifactFolderUrl: 'https://feishu.example/folder/abc',
    artifactUploadError: null,
  }, { jobId: 'ASR_demo' });

  assert.equal(summary.includes('原始转写: /tmp/job/raw/demo.txt'), true);
  assert.equal(summary.includes('清洗稿: /tmp/job/deliverables/demo.polished.txt'), true);
  assert.equal(summary.includes('飞书文档: https://feishu.example/docx/transcript123'), true);
  assert.equal(summary.includes('爆款精华文档: https://feishu.example/docx/highlight1'), true);
  assert.equal(summary.includes('子任务ID: ASR_demo_1'), true);
});

test('buildLocalDeliverableSummaryMarkdown includes final artifact metadata after job completion', () => {
  const summary = buildLocalDeliverableSummaryMarkdown([
    {
      input: 'https://example.com/video',
      title: '示例视频',
    },
  ], {
    state: 'completed_with_errors',
    completedAt: '2026-03-19T13:00:00.000Z',
    artifactFolderUrl: 'https://feishu.example/folder/final-artifacts',
    artifactUploadError: 'zip upload failed once',
  }, { jobId: 'vj_demo' });

  assert.equal(summary.includes('产物目录: https://feishu.example/folder/final-artifacts'), true);
  assert.equal(summary.includes('产物上传异常: zip upload failed once'), true);
});

test('finalizeFailedJobArtifacts writes local summary after final artifact metadata is known', async () => {
  const calls = [];
  const initialJob = {
    state: 'failed',
    taskStatus: 'failed',
    error: 'boom',
    completedAt: '2026-03-19T14:00:00.000Z',
    results: [{ input: 'https://example.com/video', error: 'boom' }],
  };

  const finalJob = await finalizeFailedJobArtifacts(initialJob, {
    uploadArtifacts: async (job) => {
      calls.push({ type: 'upload', job });
      return {
        artifactFolderUrl: 'https://feishu.example/folder/failed-final',
        artifactUploadError: 'zip upload partial failure',
      };
    },
    writeJobPatch: async (patch) => {
      calls.push({ type: 'writeJobPatch', patch });
      return { ...initialJob, ...patch };
    },
    writeLocalSummary: async (results, job) => {
      calls.push({ type: 'writeLocalSummary', results, job });
    },
  });

  assert.equal(finalJob.artifactFolderUrl, 'https://feishu.example/folder/failed-final');
  assert.equal(finalJob.artifactUploadError, 'zip upload partial failure');
  assert.deepEqual(calls.map((entry) => entry.type), ['upload', 'writeJobPatch', 'writeLocalSummary']);
  assert.equal(calls[2].job.artifactFolderUrl, 'https://feishu.example/folder/failed-final');
  assert.equal(calls[2].job.artifactUploadError, 'zip upload partial failure');
});

test('buildCompletionLine prefixes line with sub-task id when provided', () => {
  assert.equal(
    buildCompletionLine({
      subTaskId: 'vj_demo_2',
      input: 'https://example.com/video2',
      transcriptPath: '/tmp/job/transcript.txt',
    }, 1),
    '2. [vj_demo_2] 完成 | https://example.com/video2 | /tmp/job/transcript.txt',
  );
});

test('buildHighlightPrompt embeds each sub-task transcript with its sub-task id', () => {
  const prompt = buildHighlightPrompt({
    jobId: 'ASR_demo',
    items: [
      {
        subTaskId: 'ASR_demo_1',
        input: 'https://douyin.com/video/1',
        title: '视频一',
        platform: 'douyin',
        polishedTranscriptText: '清洗稿一',
      },
      {
        subTaskId: 'ASR_demo_2',
        input: 'https://douyin.com/video/2',
        title: '视频二',
        platform: 'douyin',
        transcriptText: '原始转写二',
      },
    ],
  });

  assert.equal(prompt.includes('子任务 ASR_demo_1'), true);
  assert.equal(prompt.includes('子任务 ASR_demo_2'), true);
  assert.equal(prompt.includes('清洗稿一'), true);
  assert.equal(prompt.includes('原始转写二'), true);
  assert.equal(prompt.includes('叙事角度'), true);
  assert.equal(prompt.includes('爆款原因'), true);
});

test('runHighlightStage writes markdown and uploads doc via injected helpers', async () => {
  const writes = [];
  const uploads = [];
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'highlight-stage-'));
  const result = await runHighlightStage({
    jobId: 'ASR_demo',
    items: [
      { subTaskId: 'ASR_demo_1', input: 'u1', polishedTranscriptText: 'hello' },
    ],
    runCodexCliImpl: async ({ prompt, model }) => `# Highlight (${model})\n\n${prompt.length} chars input`,
    codexCliPath: '/fake/codex',
    deliverablesRoot: tempRoot,
    writeMarkdown: async (filePath, content) => {
      writes.push({ filePath, content });
    },
    uploadDoc: async (title, markdown) => {
      uploads.push({ title, markdown });
      return { documentId: 'docHighlight', url: 'https://feishu.cn/docx/docHighlight', title };
    },
  });

  assert.equal(writes.length, 1);
  assert.equal(writes[0].filePath.endsWith('highlight.md'), true);
  assert.equal(uploads.length, 1);
  assert.equal(uploads[0].title, '爆款精华-ASR_demo');
  assert.equal(uploads[0].markdown.startsWith('# Highlight (gpt-5.5)'), true);
  assert.equal(result.doc.url, 'https://feishu.cn/docx/docHighlight');
});

test('buildCompletionMessage surfaces highlight doc url and rw hint', () => {
  const message = buildCompletionMessage([
    {
      subTaskId: 'ASR_demo_1',
      input: 'https://douyin.com/video/1',
      transcriptPath: '/tmp/t1.txt',
      highlightDoc: { url: 'https://feishu.cn/docx/highlight1' },
    },
    {
      subTaskId: 'ASR_demo_2',
      input: 'https://douyin.com/video/2',
      transcriptPath: '/tmp/t2.txt',
      highlightDoc: { url: 'https://feishu.cn/docx/highlight1' },
    },
  ], {}, { jobId: 'ASR_demo' });

  assert.equal(message.includes('爆款精华文档: https://feishu.cn/docx/highlight1'), true);
  assert.equal(message.includes('/rw https://feishu.cn/docx/highlight1'), true);
  assert.equal(message.includes('[ASR_demo_1]'), true);
});
