// 鬼谷子（选题）面板 —— 两步流·评论驱动多模型。
//
// 沈括(asr) 完成后，用户在沈括面板点选 1-5 条高赞评论作选题参考（存 localStorage）。本面板：
//   A. 已选评论 +「分析爆款原因」按钮 → 第一步 analyze（多模型并行反推爆款原因，不固定赛道）。
//   B. 多栏展示各模型的结构化分析，用户**可编辑** + 每栏「用这份分析出选题」→ 第二步 generate。
//   C. 多栏展示选题，hover 选定一个（N 选 1）→ 放行柳永(rw) 出稿。
// 评论变化时支持增量「更新选题」/ 全量「重新选题」；改分析则回 A 点「重新分析」。
// 不可用的模型（如 opus 订阅过期）自动隐藏，不展示也不显示错误。

import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type ComponentType,
} from 'react';
import { FileText, Heart, Languages, Lightbulb, MessageCircle, PenLine, Play, Search, Sparkles } from 'lucide-react';

import { api } from '../../api/client';
import type {
  GuiguziAnalysis,
  GuiguziAnalysisColumn,
  GuiguziCandidate,
  GuiguziItem,
  GuiguziResult,
  GuiguziTopic,
  FilmCueKind,
  FilmCommentaryCue,
  FilmTargetLanguage,
  JobState,
} from '../../api/types';
import { guiguziChosenStorageKey, guiguziItemsStorageKey } from '../../config/agents';
import { GlobalLoading } from '../GlobalLoading';
import { QuickTip } from '../QuickTip';
import { useToast } from '../Toast';

interface Props {
  jobId: string;
  job: JobState;
  onConfirmed?: () => void;
  onGotoShenkuo?: () => void;
}

// 模型元信息：name → label
const MODEL_META: Record<string, string> = {
  opus: 'Opus 4.8',
  deepseek: 'DeepSeek',
  agy: 'AGY',
};
const ALL_MODELS = Object.keys(MODEL_META);

function diggCount(n: number): string {
  if (n >= 1e8) return `${(n / 1e8).toFixed(1)}亿`;
  if (n >= 1e4) return `${(n / 1e4).toFixed(1)}w`;
  return String(n);
}

function AutoTextarea({
  value,
  fitKey,
  ...rest
}: ComponentProps<'textarea'> & { value: string; fitKey?: unknown }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight + (fitKey === undefined ? 0 : 6)}px`;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, fitKey === undefined ? [value] : [fitKey]);
  return <textarea ref={ref} value={value} {...rest} />;
}

function readItems(jobId: string): GuiguziItem[] {
  try {
    const raw = localStorage.getItem(guiguziItemsStorageKey(jobId));
    return raw ? (JSON.parse(raw) as GuiguziItem[]) : [];
  } catch {
    return [];
  }
}

const TARGET_LANGUAGES: { code: FilmTargetLanguage; label: string }[] = [
  { code: 'en', label: 'English' },
  { code: 'ja', label: '日本語' },
  { code: 'ko', label: '한국어' },
  { code: 'es', label: 'Español' },
  { code: 'fr', label: 'Français' },
  { code: 'de', label: 'Deutsch' },
];

const FILM_TIMELINE_WINDOW_MS = 20_000;
const FILM_TIMELINE_TICK_OFFSETS = [0, 5_000, 10_000, 15_000, 20_000];
const FILM_TIMELINE_MERGE_GAP_MS = 300;
const FILM_TIMELINE_MAX_RUN_MS = 8_000;
const FILM_TIMELINE_PRIMARY_ROLES: FilmCueKind[] = [
  'narration',
  'dialogue',
  'noise',
];
const FILM_ROLE_META = {
  narration: { label: '解说旁白', shortLabel: '解说' },
  dialogue: { label: '影视对白', shortLabel: '对白' },
  noise: { label: '噪声文字', shortLabel: '噪声' },
} as const;

interface FilmTimelineRun {
  key: string;
  role: FilmCueKind;
  language: string;
  startMs: number;
  endMs: number;
  sourceText: string;
  segments: FilmCommentaryCue[];
}

interface FilmTimelineFragment {
  key: string;
  run: FilmTimelineRun;
  clipStartMs: number;
  clipEndMs: number;
  leftPercent: number;
  widthPercent: number;
}

interface FilmTimelineTrack {
  role: FilmCueKind;
  lanes: FilmTimelineFragment[][];
}

interface FilmTimelineWindow {
  index: number;
  startMs: number;
  endMs: number;
  tracks: FilmTimelineTrack[];
}

interface FilmTimelineGroup {
  sourceWorkId: string;
  windows: FilmTimelineWindow[];
}

interface FilmRoleStat {
  count: number;
  durationMs: number;
}

function packFilmTimelineLanes(
  fragments: FilmTimelineFragment[],
): FilmTimelineFragment[][] {
  const sorted = [...fragments].sort((a, b) => (
    a.clipStartMs - b.clipStartMs
    || a.clipEndMs - b.clipEndMs
  ));
  const lanes: FilmTimelineFragment[][] = [];
  const laneEnds: number[] = [];

  sorted.forEach((fragment) => {
    let laneIndex = laneEnds.findIndex((endMs) => fragment.clipStartMs >= endMs);
    if (laneIndex < 0) {
      laneIndex = lanes.length;
      lanes.push([]);
      laneEnds.push(0);
    }
    lanes[laneIndex].push(fragment);
    laneEnds[laneIndex] = fragment.clipEndMs;
  });

  return lanes;
}

function isValidFilmSegment(segment: FilmCommentaryCue): boolean {
  return (
    Number.isFinite(segment.start_ms)
    && Number.isFinite(segment.end_ms)
    && segment.end_ms > Math.max(0, segment.start_ms)
    && segment.text.trim().length > 0
  );
}

function hasSentenceEnding(text: string): boolean {
  return /[。！？!?；;….]["'”’）)\]】》]*\s*$/.test(text);
}

function joinFilmRunText(language: string, texts: string[]): string {
  return texts.map((text) => text.trim()).filter(Boolean).join(
    language === 'zh' ? '' : ' ',
  );
}

function buildFilmTimelineRuns(segments: FilmCommentaryCue[]): FilmTimelineRun[] {
  const streams = new Map<string, Array<{
    segment: FilmCommentaryCue;
    startMs: number;
    endMs: number;
  }>>();
  segments.filter(isValidFilmSegment).forEach((segment) => {
    const language = 'zh';
    const key = `${segment.kind}:${language}`;
    const stream = streams.get(key) ?? [];
    stream.push({
      segment,
      startMs: Math.max(0, segment.start_ms),
      endMs: segment.end_ms,
    });
    streams.set(key, stream);
  });
  const runs: FilmTimelineRun[] = [];

  streams.forEach((stream) => {
    const streamRuns: FilmTimelineRun[] = [];
    stream
      .sort((a, b) => a.startMs - b.startMs || a.endMs - b.endMs)
      .forEach(({ segment, startMs, endMs }) => {
        const previous = streamRuns[streamRuns.length - 1];
        const previousTail = previous?.segments[previous.segments.length - 1];
        const canMerge = Boolean(
          previous
          && startMs >= previous.endMs
          && startMs - previous.endMs <= FILM_TIMELINE_MERGE_GAP_MS
          && endMs - previous.startMs <= FILM_TIMELINE_MAX_RUN_MS
          && !hasSentenceEnding(previousTail?.text ?? ''),
        );

        if (canMerge && previous) {
          previous.endMs = endMs;
          previous.segments.push(segment);
          previous.sourceText = joinFilmRunText(
            previous.language,
            previous.segments.map((item) => item.text),
          );
          return;
        }

        streamRuns.push({
          key: `${segment.cue_id}:${startMs}`,
          role: segment.kind,
          language: 'zh',
          startMs,
          endMs,
          sourceText: segment.text.trim(),
          segments: [segment],
        });
      });
    runs.push(...streamRuns);
  });

  return runs.sort((a, b) => (
    a.startMs - b.startMs
    || a.endMs - b.endMs
    || a.role.localeCompare(b.role)
  ));
}

function buildFilmTimelineGroups(
  segments: FilmCommentaryCue[],
): FilmTimelineGroup[] {
  const groupedSegments = new Map<string, FilmCommentaryCue[]>();
  segments.forEach((segment) => {
    const sourceWorkId = segment.source_work_id || 'source';
    const group = groupedSegments.get(sourceWorkId) ?? [];
    group.push(segment);
    groupedSegments.set(sourceWorkId, group);
  });

  return Array.from(groupedSegments.entries()).flatMap(([sourceWorkId, sourceSegments]) => {
    const runs = buildFilmTimelineRuns(sourceSegments);
    if (runs.length === 0) return [];
    const maxEndMs = runs.reduce(
      (maxEnd, run) => Math.max(maxEnd, run.endMs),
      0,
    );
    const windowCount = Math.max(1, Math.ceil(maxEndMs / FILM_TIMELINE_WINDOW_MS));
    const fragmentsByWindow = Array.from(
      { length: windowCount },
      () => [] as FilmTimelineFragment[],
    );

    runs.forEach((run) => {
      const startMs = run.startMs;
      const endMs = run.endMs;
      const firstWindow = Math.floor(startMs / FILM_TIMELINE_WINDOW_MS);
      const lastWindow = Math.max(
        firstWindow,
        Math.ceil(endMs / FILM_TIMELINE_WINDOW_MS) - 1,
      );

      for (let windowIndex = firstWindow; windowIndex <= lastWindow; windowIndex += 1) {
        const windowStartMs = windowIndex * FILM_TIMELINE_WINDOW_MS;
        const windowEndMs = windowStartMs + FILM_TIMELINE_WINDOW_MS;
        const clipStartMs = Math.max(startMs, windowStartMs);
        const clipEndMs = Math.min(endMs, windowEndMs);
        if (clipEndMs <= clipStartMs) continue;

        fragmentsByWindow[windowIndex].push({
          key: `${run.key}:${windowIndex}:${clipStartMs}`,
          run,
          clipStartMs,
          clipEndMs,
          leftPercent: (
            (clipStartMs - windowStartMs) / FILM_TIMELINE_WINDOW_MS
          ) * 100,
          widthPercent: (
            (clipEndMs - clipStartMs) / FILM_TIMELINE_WINDOW_MS
          ) * 100,
        });
      }
    });

    return [{
      sourceWorkId,
      windows: fragmentsByWindow.map((fragments, index) => ({
        index,
        startMs: index * FILM_TIMELINE_WINDOW_MS,
        endMs: (index + 1) * FILM_TIMELINE_WINDOW_MS,
        tracks: [
          ...FILM_TIMELINE_PRIMARY_ROLES,
        ].map((role) => ({
          role,
          lanes: packFilmTimelineLanes(
            fragments.filter((fragment) => fragment.run.role === role),
          ),
        })),
      })),
    }];
  });
}

function buildFilmRoleStats(
  segments: FilmCommentaryCue[],
): Record<FilmCueKind, FilmRoleStat> {
  const roles: FilmCueKind[] = [
    'narration',
    'dialogue',
    'noise',
  ];
  return Object.fromEntries(roles.map((role) => {
    const roleSegments = segments.filter(
      (segment) => segment.kind === role && isValidFilmSegment(segment),
    );
    const intervalsBySource = new Map<string, Array<[number, number]>>();
    roleSegments.forEach((segment) => {
      const sourceWorkId = segment.source_work_id || 'source';
      const intervals = intervalsBySource.get(sourceWorkId) ?? [];
      intervals.push([Math.max(0, segment.start_ms), segment.end_ms]);
      intervalsBySource.set(sourceWorkId, intervals);
    });

    let durationMs = 0;
    intervalsBySource.forEach((intervals) => {
      const sorted = intervals.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
      let currentStart = -1;
      let currentEnd = -1;
      sorted.forEach(([startMs, endMs]) => {
        if (currentStart < 0) {
          currentStart = startMs;
          currentEnd = endMs;
        } else if (startMs <= currentEnd) {
          currentEnd = Math.max(currentEnd, endMs);
        } else {
          durationMs += currentEnd - currentStart;
          currentStart = startMs;
          currentEnd = endMs;
        }
      });
      if (currentStart >= 0) durationMs += currentEnd - currentStart;
    });

    return [role, { count: roleSegments.length, durationMs }];
  })) as Record<FilmCueKind, FilmRoleStat>;
}

function formatFilmTimelineTick(milliseconds: number): string {
  const totalSeconds = Math.floor(Math.max(0, milliseconds) / 1000);
  const seconds = totalSeconds % 60;
  const totalMinutes = Math.floor(totalSeconds / 60);
  const minutes = totalMinutes % 60;
  const hours = Math.floor(totalMinutes / 60);
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  }
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

function formatFilmDuration(milliseconds: number): string {
  const totalSeconds = Math.round(Math.max(0, milliseconds) / 1000);
  const seconds = totalSeconds % 60;
  const totalMinutes = Math.floor(totalSeconds / 60);
  const minutes = totalMinutes % 60;
  const hours = Math.floor(totalMinutes / 60);
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
    : `${String(totalMinutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

const GUIguziPanelByDomain: Record<string, ComponentType<Props>> = {
  film: FilmGuiguziPanel,
  film_commentary: FilmGuiguziPanel,
};

export function GuiguziPanel(props: Props) {
  const domain = String(props.job.inputs.domain ?? '').trim().toLowerCase();
  const mode = String(props.job.inputs.mode ?? '').trim().toLowerCase();
  const Panel = GUIguziPanelByDomain[domain]
    ?? GUIguziPanelByDomain[mode]
    ?? TopicGuiguziPanel;
  return <Panel {...props} />;
}

function FilmGuiguziPanel({ jobId, job, onConfirmed, onGotoShenkuo }: Props) {
  const { showToast } = useToast();
  const [result, setResult] = useState<GuiguziResult | null>(null);
  const [targetLanguage, setTargetLanguage] = useState<FilmTargetLanguage>('en');
  const [busy, setBusy] = useState(false);
  const asrDone = job.nodes.asr?.status === 'done';
  const running = result?.status === 'running';
  const segments = result?.cues ?? [];
  const roleStats = useMemo(() => buildFilmRoleStats(segments), [segments]);
  const commentaryReady = (
    result?.mode === 'film_commentary'
    && result.status === 'done'
    && segments.length > 0
  );

  useEffect(() => {
    api.getGuiguzi(jobId).then(setResult).catch(() => setResult(null));
  }, [jobId]);

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      api.getGuiguzi(jobId).then(setResult).catch(() => {});
    }, 1500);
    return () => window.clearInterval(timer);
  }, [jobId, running]);

  async function cleanCommentary() {
    if (!asrDone) {
      showToast('请先让沈括完成采集');
      return;
    }
    setBusy(true);
    try {
      setResult(await api.analyzeGuiguzi(jobId, []));
    } catch (error) {
      showToast('解说稿清洗启动失败');
      console.error('[FilmGuiguziPanel] commentary failed', error);
    } finally {
      setBusy(false);
    }
  }

  async function confirmLocalization() {
    if (result?.mode !== 'film_commentary' || result.status !== 'done') return;
    setBusy(true);
    try {
      await api.runNode(
        jobId,
        'rw',
        { target_language: targetLanguage },
        true,
      );
      onConfirmed?.();
    } catch (error) {
      showToast('启动翻译失败，请稍后再试');
      console.error('[FilmGuiguziPanel] localization failed', error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel guiguzi-panel film-script-panel">
      <div className="panel-section">
        <div className="panel-section-title film-panel-title">
          <Languages size={14} strokeWidth={1.8} />
          影视解说字幕
          <button
            className={`btn sm${segments.length === 0 ? ' primary' : ''}`}
            disabled={!asrDone || busy || running}
            onClick={cleanCommentary}
          >
            {running ? '清洗中…' : segments.length > 0 ? '重新清洗' : '开始清洗'}
          </button>
        </div>
        {!asrDone && (
          <div className="empty-state">
            先让 <button className="link-btn" onClick={onGotoShenkuo}>沈括</button> 完成原片字幕 OCR。
          </div>
        )}
        {running && (
          <div className="gg-loading">
            <GlobalLoading size={44} coreColor="var(--bg-surface)" />
            <div className="dim-mono">{result?.progress || '正在对齐 OCR/ASR 并清洗解说稿…'}</div>
          </div>
        )}
        {result?.status === 'failed' && (
          <div className="panel-hint panel-hint-error">
            {result.error || '解说稿清洗失败'}
          </div>
        )}
      </div>

      {segments.length > 0 && (
        <>
          <div className="panel-section film-summary">
            <span>
              <b>原始 OCR</b>
              <small>{result?.qa?.raw_cues ?? segments.length} 条</small>
            </span>
            <span className="kind-narration">
              <b>解说</b>
              <small>
                {result?.qa?.narration_cues ?? roleStats.narration.count} 条 ·{' '}
                {formatFilmDuration(roleStats.narration.durationMs)}
              </small>
            </span>
            <span className="kind-dialogue">
              <b>对白</b>
              <small>
                {result?.qa?.dialogue_filtered ?? roleStats.dialogue.count} 条 ·{' '}
                {formatFilmDuration(roleStats.dialogue.durationMs)}
              </small>
            </span>
            <span className="kind-noise">
              <b>噪声</b>
              <small>{result?.qa?.noise_filtered ?? roleStats.noise.count} 条</small>
            </span>
            <span>
              <b>待复核</b>
              <small>{result?.qa?.needs_review ?? 0} 条</small>
            </span>
          </div>
          {commentaryReady && (
            <div className="panel-hint panel-hint-info">
              解说稿已清洗 · {result.entity_glossary?.length ?? 0} 个统一术语 ·{' '}
              {result.script?.txt}
            </div>
          )}
          <FilmTimeline segments={segments} />
          <div className="panel-section film-cue-comparison">
            {segments.map((cue) => (
              <div className={`film-cue-row kind-${cue.kind}`} key={cue.cue_id}>
                <span className="dim-mono">
                  {formatFilmTimelineTick(cue.start_ms)} · {FILM_ROLE_META[cue.kind].shortLabel}
                </span>
                <span>{cue.ocr_text}</span>
                <span aria-label="清洗后文本">→ {cue.text}</span>
              </div>
            ))}
          </div>
          <div className="panel-section film-localization-action">
            <label>
              目标语言
              <select
                value={targetLanguage}
                onChange={(event) => setTargetLanguage(event.target.value as FilmTargetLanguage)}
              >
                {TARGET_LANGUAGES.map((language) => (
                  <option key={language.code} value={language.code}>
                    {language.label}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="btn primary sm"
              disabled={busy || !commentaryReady}
              onClick={confirmLocalization}
            >
              <Play size={12} strokeWidth={2} />
              确认并交给柳永
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function FilmTimeline({ segments }: { segments: FilmCommentaryCue[] }) {
  const groups = useMemo(() => buildFilmTimelineGroups(segments), [segments]);
  const showGroupHeading = groups.length > 1;

  return (
    <div className="panel-section film-timeline">
      {groups.map((group, groupIndex) => (
        <section className="film-timeline-source" key={group.sourceWorkId}>
          {showGroupHeading && (
            <div className="film-timeline-source-title">
              素材 {groupIndex + 1}
              <span>{group.sourceWorkId}</span>
            </div>
          )}
          {group.windows.map((window) => (
            <FilmTimelineWindowRow key={window.index} window={window} />
          ))}
        </section>
      ))}
    </div>
  );
}

function FilmTimelineWindowRow({ window }: { window: FilmTimelineWindow }) {
  return (
    <div
      className="film-timeline-window"
      aria-label={`${formatFilmTimelineTick(window.startMs)} 至 ${formatFilmTimelineTick(window.endMs)}`}
      data-window-index={window.index}
      data-window-start-ms={window.startMs}
      data-window-end-ms={window.endMs}
    >
      <div className="film-timeline-ruler">
        <div aria-hidden="true" />
        <div className="film-timeline-axis" aria-hidden="true">
          {FILM_TIMELINE_TICK_OFFSETS.map((offsetMs, tickIndex) => (
            <span
              className="film-timeline-tick"
              data-edge={
                tickIndex === 0
                  ? 'start'
                  : tickIndex === FILM_TIMELINE_TICK_OFFSETS.length - 1
                    ? 'end'
                    : undefined
              }
              key={offsetMs}
              style={{ left: `${(offsetMs / FILM_TIMELINE_WINDOW_MS) * 100}%` }}
            >
              {formatFilmTimelineTick(window.startMs + offsetMs)}
            </span>
          ))}
        </div>
      </div>
      <div className="film-timeline-tracks">
        {window.tracks.map((track) => {
          const roleMeta = FILM_ROLE_META[track.role];
          const lanes = track.lanes.length > 0 ? track.lanes : [[]];
          return (
            <div
              className={`film-timeline-track kind-${track.role}`}
              data-track-role={track.role}
              key={track.role}
            >
              <div className="film-timeline-track-label">{roleMeta.shortLabel}</div>
              <div className="film-timeline-track-canvas">
                {lanes.map((lane, laneIndex) => (
                  <div
                    className={`film-timeline-lane${lane.length === 0 ? ' is-empty' : ''}`}
                    key={laneIndex}
                  >
                    {lane.map((fragment) => {
                      const numberedSegments = fragment.run.segments.map(
                        (segment, index) => `${index + 1}. ${segment.text.trim()}`,
                      );
                      const segmentMarkers = fragment.run.segments.filter((segment) => (
                        segment.start_ms > fragment.clipStartMs
                        && segment.start_ms < fragment.clipEndMs
                      ));
                      return (
                        <QuickTip
                          aria-label={`${roleMeta.label}：${numberedSegments.join('；')}`}
                          className={`film-timeline-clip kind-${fragment.run.role}`}
                          data-clip-start-ms={fragment.clipStartMs}
                          data-clip-end-ms={fragment.clipEndMs}
                          data-run-start-ms={fragment.run.startMs}
                          data-run-end-ms={fragment.run.endMs}
                          data-segment-count={fragment.run.segments.length}
                          key={fragment.key}
                          style={{
                            left: `${fragment.leftPercent}%`,
                            width: `${fragment.widthPercent}%`,
                          }}
                          tip={(
                            <ol className="film-timeline-tip-list">
                              {fragment.run.segments.map((segment) => (
                                <li key={segment.cue_id}>{segment.text.trim()}</li>
                              ))}
                            </ol>
                          )}
                        >
                          {segmentMarkers.map((segment) => (
                            <i
                              aria-hidden="true"
                              className="film-timeline-segment-mark"
                              key={segment.cue_id}
                              style={{
                                left: `${
                                  ((segment.start_ms - fragment.clipStartMs)
                                    / (fragment.clipEndMs - fragment.clipStartMs)) * 100
                                }%`,
                              }}
                            />
                          ))}
                          <span className="film-timeline-clip-text">
                            {fragment.run.sourceText}
                          </span>
                        </QuickTip>
                      );
                    })}
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TopicGuiguziPanel({ jobId, job, onConfirmed, onGotoShenkuo }: Props) {
  const { showToast } = useToast();
  const [items, setItems] = useState<GuiguziItem[]>(() => readItems(jobId));
  const [result, setResult] = useState<GuiguziResult | null>(null);
  const [busyAction, setBusyAction] = useState(false);
  const [chosenTitle, setChosenTitle] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, GuiguziAnalysis>>({});
  const [activeAnalysisTab, setActiveAnalysisTab] = useState<string>('');

  useLayoutEffect(() => {
    document.querySelectorAll<HTMLTextAreaElement>('.guiguzi-cols .gg-field-input').forEach(el => {
      el.style.height = 'auto';
      el.style.height = `${el.scrollHeight}px`;
    });
  }, [activeAnalysisTab]);

  const [promptEdit, setPromptEdit] = useState<string>('');
  const [promptSyncedAt, setPromptSyncedAt] = useState<number | undefined>(undefined);

  const asrDone = job.nodes.asr?.status === 'done';
  const stage = result?.stage;
  const running = result?.status === 'running';
  const analyzed = result?.status === 'analyzed';
  const done = result?.status === 'done';
  const busy = busyAction || running;

  const analysisMap = result?.analysis as Record<string, GuiguziAnalysisColumn | undefined> | undefined;
  const candidatesMap = result?.candidates as Record<string, GuiguziCandidate | undefined> | undefined;

  // 可用模型列表（无 error 的）
  const availAnalysis = analyzed
    ? ALL_MODELS.filter((m) => analysisMap?.[m] && !analysisMap[m]?.error)
    : [];
  const availTopics = done
    ? ALL_MODELS.filter((m) => candidatesMap?.[m] && !candidatesMap[m]?.error)
    : [];

  // 默认选中第一个可用模型
  if (analyzed && availAnalysis.length > 0 && !availAnalysis.includes(activeAnalysisTab)) {
    // 在 render 期同步更新（非 useEffect），避免 tab 切到不可用模型
    if (activeAnalysisTab !== availAnalysis[0]) {
      // eslint-disable-next-line react-compiler/react-internal/no-unused-state
      setActiveAnalysisTab(availAnalysis[0]);
    }
  }

  useEffect(() => {
    setItems(readItems(jobId));
    try {
      const raw = localStorage.getItem(guiguziChosenStorageKey(jobId));
      setChosenTitle(raw ? (JSON.parse(raw) as GuiguziTopic).title ?? null : null);
    } catch {
      setChosenTitle(null);
    }
    api.getGuiguzi(jobId).then(setResult).catch(() => setResult(null));
  }, [jobId]);

  // running 期间轮询（每 2s），兜底 SSE 断连/丢事件场景
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (!running) {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }
    const tick = () => {
      api.getGuiguzi(jobId).then((d) => {
        if (d.status !== 'running' && pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
        setResult(d);
      }).catch(() => {});
    };
    tick();
    pollRef.current = setInterval(tick, 2000);
    return () => {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    };
  }, [running, jobId]);

  useEffect(() => {
    if (analyzed && result?.analysis) {
      const next: Record<string, GuiguziAnalysis> = {};
      for (const m of ALL_MODELS) {
        const a = analysisMap?.[m]?.analysis;
        if (a) next[m] = { ...a };
      }
      setEdits(next);
    }
  }, [analyzed, result?.updated_at]); // eslint-disable-line react-hooks/exhaustive-deps

  if (done && result?.prompt != null && result.updated_at !== promptSyncedAt) {
    setPromptSyncedAt(result.updated_at);
    setPromptEdit(result.prompt);
  }

  async function analyze() {
    if (!asrDone) {
      showToast('请先让沈括完成采集');
      return;
    }
    setBusyAction(true);
    try {
      setResult(await api.analyzeGuiguzi(jobId, items));
    } catch (e) {
      showToast('分析失败，请稍后再试');
      console.error('[GuiguziPanel] analyzeGuiguzi 失败', e);
    } finally {
      setBusyAction(false);
    }
  }

  async function generate(analysis: GuiguziAnalysis, opts: { force?: boolean; prompt?: string } = {}) {
    setBusyAction(true);
    setResult((prev) =>
      prev ? { ...prev, status: 'running', stage: 'generating', candidates: null, topics: null, progress: '出题中…' } : prev,
    );
    try {
      setResult(await api.generateGuiguzi(jobId, items, analysis, opts));
    } catch (e) {
      showToast('出选题失败，请稍后再试');
      console.error('[GuiguziPanel] generateGuiguzi 失败', e);
    } finally {
      setBusyAction(false);
    }
  }

  async function chooseTopic(t: GuiguziTopic) {
    if (!asrDone) {
      showToast('请先完成沈括的采集与转写');
      return;
    }
    setChosenTitle(t.title);
    try {
      localStorage.setItem(guiguziChosenStorageKey(jobId), JSON.stringify(t));
    } catch { /* ignore */ }
    onConfirmed?.();
    try {
      await api.runNode(jobId, 'rw', undefined, true);
    } catch (e) {
      showToast('启动出稿失败，请稍后再试');
      console.error('[GuiguziPanel] runNode(rw) 失败', e);
    }
  }

  const hasAnalysis = !!(
    result?.analysis &&
    ALL_MODELS.some((m) => analysisMap?.[m]?.analysis)
  );
  const hasTopics =
    done &&
    ALL_MODELS.some((m) => (candidatesMap?.[m]?.topics?.length ?? 0) > 0);

  const coveredComments = new Set<string>();
  if (done) {
    for (const m of availTopics) {
      for (const t of candidatesMap?.[m]?.topics ?? []) {
        if (t.anchor_comment) coveredComments.add(t.anchor_comment);
      }
    }
  }
  const currentComments = items.map((it) => it.comment);
  const newCount = currentComments.filter((c) => !coveredComments.has(c)).length;
  const removedCount = [...coveredComments].filter((c) => !currentComments.includes(c)).length;
  const changed = items.length > 0 && hasTopics && (newCount > 0 || removedCount > 0);

  const analyzeButton = (
    <button
      className="btn primary sm"
      disabled={!asrDone || busy}
      onClick={analyze}
      title="从原文(+评论)反推爆款原因（不固定赛道）"
    >
      <Search size={13} strokeWidth={1.8} />
      {stage === 'analyzing'
        ? '分析中…'
        : hasAnalysis
          ? '重新分析'
          : items.length > 0
            ? '分析爆款原因'
            : '拆解爆款原因'}
    </button>
  );

  return (
    <div className="panel guiguzi-panel">
      <div className="panel-section">
        <div className="panel-section-title" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <MessageCircle size={14} strokeWidth={1.8} />
          <span style={{ flex: 1 }}>选题参考 · 已选 {items.length}/5 条评论</span>
          {items.length > 0 && analyzeButton}
        </div>
        {!asrDone ? (
          <div className="empty-state" style={{ padding: 'var(--s-4)' }}>
            还没有可选的评论。先让{' '}
            <button type="button" className="link-btn" onClick={onGotoShenkuo}>沈括</button>
            {' '}去采集些基础数据（文案 / 高赞评论），再回来找我选题。
          </div>
        ) : items.length === 0 ? (
          <div className="empty-state" style={{ padding: 'var(--s-4)' }}>
            <div>
              建议从{' '}
              <button type="button" className="link-btn" onClick={onGotoShenkuo}>沈括</button>
              {' '}处选择高赞评论，再进行选题。
            </div>
            <div style={{ marginTop: 'var(--s-3)', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span>或者您也可以</span>
              {analyzeButton}
            </div>
          </div>
        ) : (
          <ul className="guiguzi-seed-list">
            {items.map((it, i) => (
              <li key={i} className="guiguzi-seed">
                <span className="guiguzi-seed-idx">{i + 1}</span>
                <span className="guiguzi-seed-text">{it.comment}</span>
                {it.digg != null && (
                  <span className="guiguzi-seed-digg">
                    <Heart size={10} strokeWidth={2} /> {diggCount(it.digg)}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {running && (
        <div className="panel-section">
          <div className="gg-loading">
            <GlobalLoading size={48} coreColor="var(--bg-surface)" />
            <div className="dim-mono">
              {result?.progress ||
                (stage === 'analyzing' ? '多模型并行分析爆款原因中…' : '多模型并行出题中…')}
            </div>
          </div>
        </div>
      )}

      {analyzed && availAnalysis.length > 0 && (
        <div className="panel-section">
          <div className="panel-section-title">
            <Lightbulb size={14} strokeWidth={1.8} /> 爆款原因 · 改一改、选一个出选题
          </div>
          <div className="gg-tabs">
            {availAnalysis.map((m) => (
              <button
                key={m}
                className={`gg-tab${activeAnalysisTab === m ? ' active' : ''}`}
                onClick={() => setActiveAnalysisTab(m)}
              >
                {MODEL_META[m] || m}
              </button>
            ))}
          </div>
          <div className={`guiguzi-cols cols-${availAnalysis.length} tab-active-${activeAnalysisTab}`}>
            {availAnalysis.map((m) => (
              <AnalysisColumn
                key={m}
                name={m}
                label={MODEL_META[m] || m}
                value={edits[m] ?? {}}
                onChange={(a) => setEdits((p) => ({ ...p, [m]: a }))}
                onUse={() => generate(edits[m] ?? {}, { force: true })}
                disabled={busy}
              />
            ))}
          </div>
        </div>
      )}

      {analyzed && availAnalysis.length === 0 && (
        <div className="panel-section">
          <div className="empty-state" style={{ padding: 'var(--s-4)', color: 'var(--danger, #e5484d)' }}>
            所有模型均不可用，无法分析。
          </div>
        </div>
      )}

      {done && availTopics.length > 0 && (
        <div className="panel-section">
          <div className="panel-section-title" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <PenLine size={14} strokeWidth={1.8} />
            <span style={{ flex: 1 }}>选题产出 · 点选一个交柳永</span>
            {hasTopics && (
              <button
                className="btn ghost sm"
                disabled={busy}
                onClick={() => generate(result?.chosen_analysis ?? {}, { force: true, prompt: promptEdit })}
                title="用下方提示词，全部评论重出（含已出过题的）"
              >
                重新选题
              </button>
            )}
          </div>
          {result?.prompt != null && (
            <details className="gg-prompt" open>
              <summary>
                <FileText size={12} strokeWidth={1.8} /> 选题提示词（$source = 原文占位，可改）
              </summary>
              <AutoTextarea
                className="gg-prompt-input"
                value={promptEdit}
                fitKey={result?.updated_at}
                onChange={(e) => setPromptEdit(e.target.value)}
                spellCheck={false}
              />
            </details>
          )}
          {changed && (
            <div className="panel-hint panel-hint-info" style={{ marginBottom: 'var(--s-3)' }}>
              评论选择已变化
              {newCount > 0 && `，新增 ${newCount} 条`}
              {removedCount > 0 && `，移除 ${removedCount} 条`}
              。
              <button
                className="link-btn"
                disabled={busy}
                onClick={() => generate(result?.chosen_analysis ?? {}, {})}
              >
                更新选题{newCount > 0 ? ` +${newCount}` : ''}
              </button>
              （只为新评论补题，已出题的保留）；或上方「重新选题」全部重出。
            </div>
          )}
          <div className={`guiguzi-cols cols-${availTopics.length}`}>
            {availTopics.map((m) => (
              <ModelColumn
                key={m}
                name={m}
                label={MODEL_META[m] || m}
                cand={candidatesMap?.[m]}
                chosenTitle={chosenTitle}
                onChoose={chooseTopic}
              />
            ))}
          </div>
        </div>
      )}

      {done && availTopics.length === 0 && (
        <div className="panel-section">
          <div className="empty-state" style={{ padding: 'var(--s-4)', color: 'var(--danger, #e5484d)' }}>
            所有模型均不可用，无法出选题。
          </div>
        </div>
      )}

      {result?.status === 'failed' && (
        <div className="panel-section">
          <div className="empty-state" style={{ padding: 'var(--s-4)', color: 'var(--danger, #e5484d)' }}>
            失败：{result.error || '未知错误'}
          </div>
        </div>
      )}
    </div>
  );
}

// —— 第一步：单模型分析栏（结构化字段，可编辑）+「用这份分析出选题」——
function AnalysisColumn({
  name,
  label,
  value,
  onChange,
  onUse,
  disabled,
}: {
  name: string;
  label: string;
  value: GuiguziAnalysis;
  onChange: (a: GuiguziAnalysis) => void;
  onUse: () => void;
  disabled: boolean;
}) {
  const set = (patch: Partial<GuiguziAnalysis>) => onChange({ ...value, ...patch });
  return (
    <div className={`guiguzi-col model-${name}`}>
      <div className="guiguzi-col-head">{label}</div>
      <label className="gg-field">
        <span className="gg-field-tag tag-title">爆款原因</span>
        <AutoTextarea
          className="gg-field-input"
          value={value.hook_reason ?? ''}
          onChange={(e) => set({ hook_reason: e.target.value })}
        />
      </label>
      <label className="gg-field">
        <span className="gg-field-tag tag-why">目标受众</span>
        <AutoTextarea
          className="gg-field-input"
          value={value.audience ?? ''}
          onChange={(e) => set({ audience: e.target.value })}
        />
      </label>
      <label className="gg-field">
        <span className="gg-field-tag tag-angle">可复制钩子</span>
        <AutoTextarea
          className="gg-field-input"
          value={(value.hooks ?? []).join('\n')}
          onChange={(e) => set({ hooks: e.target.value.split('\n').map((s) => s.trim()).filter(Boolean) })}
        />
      </label>
      <label className="gg-field">
        <span className="gg-field-tag tag-dir">建议方向</span>
        <AutoTextarea
          className="gg-field-input"
          value={value.direction ?? ''}
          onChange={(e) => set({ direction: e.target.value })}
        />
      </label>
      <button className="btn primary sm gg-use-btn" disabled={disabled} onClick={onUse}>
        <Sparkles size={13} strokeWidth={1.8} /> 用这份分析出选题
      </button>
    </div>
  );
}

// —— 第二步：单模型选题栏；每个选题 hover 选定（N 选 1）→ 交柳永。
function ModelColumn({
  name,
  label,
  cand,
  chosenTitle,
  onChoose,
}: {
  name: string;
  label: string;
  cand?: GuiguziCandidate;
  chosenTitle: string | null;
  onChoose: (t: GuiguziTopic) => void;
}) {
  const topics = [...(cand?.topics ?? [])].sort((a, b) => (b.potential ?? 0) - (a.potential ?? 0));
  return (
    <div className={`guiguzi-col model-${name}`}>
      <div className="guiguzi-col-head">{label}</div>
      {topics.length === 0 ? (
        <div className="dim-mono" style={{ padding: 'var(--s-2)' }}>无产出</div>
      ) : (
        topics.map((t, i) => {
          const chosen = chosenTitle != null && t.title === chosenTitle;
          return (
            <div
              key={i}
              className={`guiguzi-topic${chosen ? ' chosen' : ''}`}
              role="button"
              tabIndex={0}
              onClick={() => onChoose(t)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onChoose(t);
                }
              }}
            >
              <div className="guiguzi-topic-bar">
                <span className="guiguzi-topic-no">备选 {i + 1}</span>
                {t.potential != null && <span className="guiguzi-topic-pot">潜力 {t.potential} 分</span>}
              </div>
              {t.anchor_comment && <blockquote className="guiguzi-topic-quote">{t.anchor_comment}</blockquote>}
              <div className="gg-line">
                <span className="gg-tag tag-title">标题</span>
                <span className="gg-line-text strong">{t.title}</span>
              </div>
              {t.why && (
                <div className="gg-line">
                  <span className="gg-tag tag-why">理由</span>
                  <span className="gg-line-text">{t.why}</span>
                </div>
              )}
              {t.angle && (
                <div className="gg-line">
                  <span className="gg-tag tag-angle">角度</span>
                  <span className="gg-line-text dim">{t.angle}</span>
                </div>
              )}
              <div className="guiguzi-topic-overlay">
                <PenLine size={14} strokeWidth={2} />
                {chosen ? '重新提交' : '选这个'}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}
