#!/usr/bin/env node
import path from 'node:path';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import {
  runRewriteAnalysis,
  getDefaultModelConfigPath,
  runRewriteForItem,
} from './video_rewrite_runner.mjs';
import {
  buildFallbackAnalysisRecord,
  DEFAULT_ARTICLE_REWRITE_PROFILE_ID,
  getRewriteProfile,
  normalizeRewriteProfileId,
} from './rewrite_profiles.mjs';

function buildNormalizedText(text) {
  return String(text ?? '')
    .replace(/\r\n/g, '\n')
    .replace(/\u00a0/g, ' ')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function normalizeStringArray(value, fallback = []) {
  const next = Array.isArray(value)
    ? value
      .filter((item) => typeof item === 'string' && item.trim())
      .map((item) => item.trim())
    : [];
  return next.length > 0 ? next : fallback;
}

function normalizeStringValue(value, fallback = '') {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function coerceAnalysisRecord({ profileId, rawRecord = null, normalizedText, errorMessage = null }) {
  const profile = getRewriteProfile(profileId);
  const allowRawTargetProfileFields = profile.analysisDefinesTargetProfile !== false;
  const fallbackSummary = profile.requiresAnalysis
    ? (errorMessage
      ? `analysis 执行失败，使用默认【${profile.defaultAnalysisRecord.formatName}】格式进入大纲提取。`
      : profile.defaultAnalysisRecord.summary)
    : profile.defaultAnalysisRecord.summary;
  const fallback = buildFallbackAnalysisRecord(profileId, {
    normalizedText,
    summary: fallbackSummary,
  });

  const record = {
    ...fallback,
    normalizedText: normalizeStringValue(rawRecord?.normalizedText, '')
      ? buildNormalizedText(rawRecord.normalizedText)
      : fallback.normalizedText,
    summary: normalizeStringValue(rawRecord?.summary, fallback.summary),
    audience: allowRawTargetProfileFields
      ? normalizeStringValue(rawRecord?.audience, fallback.audience)
      : fallback.audience,
    platformTone: allowRawTargetProfileFields
      ? normalizeStringValue(rawRecord?.platformTone, fallback.platformTone)
      : fallback.platformTone,
    risks: normalizeStringArray(rawRecord?.risks, fallback.risks),
    formatName: allowRawTargetProfileFields
      ? normalizeStringValue(rawRecord?.formatName, fallback.formatName)
      : fallback.formatName,
    formatFeatures: allowRawTargetProfileFields
      ? normalizeStringArray(rawRecord?.formatFeatures, fallback.formatFeatures)
      : fallback.formatFeatures,
    writingLogic: allowRawTargetProfileFields
      ? normalizeStringArray(rawRecord?.writingLogic, fallback.writingLogic)
      : fallback.writingLogic,
    expressionStyle: allowRawTargetProfileFields
      ? normalizeStringArray(rawRecord?.expressionStyle, fallback.expressionStyle)
      : fallback.expressionStyle,
    articleType: allowRawTargetProfileFields
      ? normalizeStringValue(rawRecord?.articleType, fallback.articleType)
      : fallback.articleType,
    sourceStyle: normalizeStringArray(
      rawRecord?.sourceStyle,
      normalizeStringArray(rawRecord?.expressionStyle, fallback.sourceStyle ?? []),
    ),
    sourceArticleType: normalizeStringValue(
      rawRecord?.sourceArticleType,
      normalizeStringValue(rawRecord?.articleType, fallback.sourceArticleType ?? ''),
    ),
    keyFacts: normalizeStringArray(rawRecord?.keyFacts, fallback.keyFacts ?? []),
    subjectiveClaims: normalizeStringArray(rawRecord?.subjectiveClaims, fallback.subjectiveClaims ?? []),
    mustKeep: normalizeStringArray(rawRecord?.mustKeep, fallback.mustKeep ?? []),
    mustAvoid: normalizeStringArray(rawRecord?.mustAvoid, fallback.mustAvoid ?? []),
    rewriteAngles: normalizeStringArray(rawRecord?.rewriteAngles, fallback.rewriteAngles ?? []),
    coreHighlights: normalizeStringArray(rawRecord?.coreHighlights, fallback.coreHighlights ?? []),
    conflictPoints: normalizeStringArray(rawRecord?.conflictPoints, fallback.conflictPoints ?? []),
    emotionPoints: normalizeStringArray(rawRecord?.emotionPoints, fallback.emotionPoints ?? []),
    stancePoints: normalizeStringArray(rawRecord?.stancePoints, fallback.stancePoints ?? []),
    audienceEmotions: normalizeStringArray(rawRecord?.audienceEmotions, fallback.audienceEmotions ?? []),
    propagationHooks: normalizeStringArray(rawRecord?.propagationHooks, fallback.propagationHooks ?? []),
    platformContext: normalizeStringValue(rawRecord?.platformContext, fallback.platformContext ?? ''),
    headlineType: normalizeStringValue(rawRecord?.headlineType, fallback.headlineType ?? ''),
    headlineApproachReason: normalizeStringValue(rawRecord?.headlineApproachReason, fallback.headlineApproachReason ?? ''),
    headlineFormula: normalizeStringArray(rawRecord?.headlineFormula, fallback.headlineFormula ?? []),
    headlineCandidates: normalizeStringArray(rawRecord?.headlineCandidates, fallback.headlineCandidates ?? []).slice(0, 5),
    bestHeadline: normalizeStringValue(rawRecord?.bestHeadline, fallback.bestHeadline ?? ''),
    bestHeadlineReason: normalizeStringValue(rawRecord?.bestHeadlineReason, fallback.bestHeadlineReason ?? ''),
    learnedKnowledge: normalizeStringArray(rawRecord?.learnedKnowledge, fallback.learnedKnowledge ?? []),
    generatedBy: normalizeStringValue(rawRecord?.generatedBy, fallback.generatedBy),
    analysisStatus: rawRecord
      ? 'success'
      : profile.requiresAnalysis
        ? 'fallback'
        : 'skipped',
  };
  if (typeof rawRecord?.generatedAt === 'string' && rawRecord.generatedAt.trim()) {
    record.generatedAt = rawRecord.generatedAt.trim();
  }
  if (errorMessage) {
    record.analysisError = errorMessage;
  }
  return record;
}

async function normalizeSourceText({
  sourceText,
  deliverablesDir,
  targetProfile,
  modelConfigPath,
  fetchImpl,
  runAnalysisImpl,
}) {
  const normalizedTargetProfile = normalizeRewriteProfileId(targetProfile, DEFAULT_ARTICLE_REWRITE_PROFILE_ID);
  const profile = getRewriteProfile(normalizedTargetProfile);
  const initialNormalizedText = buildNormalizedText(sourceText);
  const normalizedTextPath = path.join(deliverablesDir, 'normalized-source.txt');
  const analysisPath = path.join(deliverablesDir, 'source-analysis.json');
  await writeFile(normalizedTextPath, initialNormalizedText, 'utf8');

  let metadata = coerceAnalysisRecord({
    profileId: normalizedTargetProfile,
    normalizedText: initialNormalizedText,
  });

  if (profile.requiresAnalysis) {
    try {
      const rawAnalysisRecord = await runAnalysisImpl({
        transcriptPath: normalizedTextPath,
        deliverablesDir,
        modelConfigPath,
        fetchImpl,
        targetProfile: normalizedTargetProfile,
      });
      metadata = coerceAnalysisRecord({
        profileId: normalizedTargetProfile,
        rawRecord: rawAnalysisRecord,
        normalizedText: initialNormalizedText,
      });
    } catch (error) {
      metadata = coerceAnalysisRecord({
        profileId: normalizedTargetProfile,
        normalizedText: initialNormalizedText,
        errorMessage: error instanceof Error ? error.message : String(error),
      });
    }
  }

  await writeFile(normalizedTextPath, metadata.normalizedText, 'utf8');
  await writeFile(analysisPath, JSON.stringify(metadata, null, 2), 'utf8');
  return {
    normalizedTextPath,
    analysisPath,
    ...metadata,
  };
}

function selectTopDrafts(drafts, count = 4) {
  return (Array.isArray(drafts) ? drafts : [])
    .filter((draft) => draft?.status === 'success' && draft?.path)
    .slice(0, count);
}

export async function runContentRewrite({
  sourceText,
  deliverablesDir,
  modelConfigPath = getDefaultModelConfigPath(),
  targetProfile = DEFAULT_ARTICLE_REWRITE_PROFILE_ID,
  candidateCount = 4,
  userRequirements = '',
  fetchImpl = fetch,
  runRewriteImpl = runRewriteForItem,
  runAnalysisImpl = runRewriteAnalysis,
} = {}) {
  if (typeof sourceText !== 'string' || !sourceText.trim()) {
    throw new Error('sourceText is required');
  }
  if (typeof deliverablesDir !== 'string' || !deliverablesDir.trim()) {
    throw new Error('deliverablesDir is required');
  }

  await mkdir(deliverablesDir, { recursive: true });
  const normalizedTargetProfile = normalizeRewriteProfileId(targetProfile, DEFAULT_ARTICLE_REWRITE_PROFILE_ID);
  const normalized = await normalizeSourceText({
    sourceText,
    deliverablesDir,
    targetProfile: normalizedTargetProfile,
    modelConfigPath,
    fetchImpl,
    runAnalysisImpl,
  });
  const rewriteResult = await runRewriteImpl({
    transcriptPath: normalized.normalizedTextPath,
    polishedTranscriptPath: null,
    deliverablesDir,
    modelConfigPath,
    fetchImpl,
    targetProfile: normalizedTargetProfile,
    analysisRecord: normalized,
    userRequirements,
  });

  return {
    status: rewriteResult.status,
    targetProfile: normalizedTargetProfile,
    normalizedTextPath: normalized.normalizedTextPath,
    analysisPath: normalized.analysisPath,
    normalizedSummary: normalized.summary,
    targetAudience: normalized.audience,
    platformTone: normalized.platformTone,
    risks: normalized.risks,
    outlinePath: rewriteResult.outlinePath,
    indexPath: rewriteResult.indexPath,
    feedbackPath: rewriteResult.feedbackPath,
    drafts: rewriteResult.drafts,
    candidateDrafts: selectTopDrafts(rewriteResult.drafts, candidateCount),
    errors: rewriteResult.errors,
    generatedBy: normalized.generatedBy,
  };
}

async function main() {
  const rawPayload = process.argv[2];
  if (!rawPayload) {
    throw new Error('Expected JSON payload');
  }
  const payload = JSON.parse(rawPayload);
  const result = await runContentRewrite({
    sourceText: payload.sourceText,
    deliverablesDir: payload.deliverablesDir,
    modelConfigPath: payload.modelConfigPath ?? getDefaultModelConfigPath(),
    targetProfile: payload.targetProfile ?? DEFAULT_ARTICLE_REWRITE_PROFILE_ID,
    candidateCount: payload.candidateCount ?? 4,
    userRequirements: payload.userRequirements ?? '',
  });
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(error.message ?? error);
    process.exit(1);
  });
}
