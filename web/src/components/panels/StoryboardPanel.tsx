// 吴道子「视觉方案」入口：先完成视觉准备，再展示 / 微调 director agent 产出的视觉层。
//
// 数据源：02_rw/episode.json 的 scenes{}（director 产出，含每个子场景的容器图 prompt
//         与 sketches[]）+ beats[].scene（子场景归属）。本面板按 scene.group 分段展示，
//         允许就地编辑容器 prompt / 简笔画 prompt / pos / at.match，防抖整份回写 episode。
// 顶部右：生成视觉方案 / 停止 / 重新执行；底部右：确认视觉方案 → runNode('tts') 交给伯牙。
//
// 风格对齐 BEATS/TTS：panel-hint banner + proc-rows 状态行 + section-h loading。

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Clapperboard, Play, RefreshCw, Square } from 'lucide-react';

import { api } from '../../api/client';
import type { Episode, NodeState, PipelineNodeDef, Scene, Sketch } from '../../api/types';
import { ConfirmDialog } from '../ConfirmDialog';
import { useToast } from '../Toast';
import { ProcStatusRow, type ProcStatus } from './ProcStatusRow';

interface Props {
  jobId: string;
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
  rwNodeState: NodeState;
  preflightNodeState: NodeState;
  onRequestDraftSelection?: () => void;
  onAdvanced?: () => void;
}

const NEXT_NODE = 'tts';
const PREFLIGHT_NODE = 'lines';

function isActive(status: NodeState['status']): boolean {
  return status === 'queued' || status === 'running';
}

function hasSelectedRwDraft(node: NodeState): boolean {
  const selected = node.outputs?.selected_model_id;
  return typeof selected === 'string' && selected.trim().length > 0;
}

function isMissingDraftError(error: string | null | undefined): boolean {
  return /draft\.md missing|选定稿模型|选模型/.test(error ?? '');
}

function friendlyPreflightError(error: string | null | undefined): string {
  const msg = (error ?? '').trim();
  if (!msg) return '未知错误';
  if (isMissingDraftError(msg)) return '请先在柳永选择一版定稿。';
  const friendly = '视觉方案准备暂时失败';
  const idx = msg.indexOf(friendly);
  if (idx >= 0) return msg.slice(idx);
  if (/台词结构化暂时失败|launcher exited|Traceback|RuntimeError: engine step lines|raw launcher stack/.test(msg)) {
    return '视觉方案准备暂时失败：备用通道都没有成功，请稍后重试。';
  }
  const cleaned = msg.replace(/^(?:RuntimeError|ValueError|Exception):\s*/g, '');
  if (/beats|Opus|opus|AGY|DeepSeek|SCodex|模型|结构化/.test(cleaned)) {
    return '视觉方案准备暂时失败：请稍后重试。';
  }
  return cleaned;
}

function friendlyPreflightProgress(progress: string | null | undefined): string | null {
  const msg = (progress ?? '').trim();
  if (!msg) return null;
  if (/结构化失败|尝试下一个模型|切换备用通道/.test(msg)) {
    return '当前通道未成功，正在切换备用通道...';
  }
  if (/结构化完成|条 beats|scenes 待分镜|视觉方案准备完成/.test(msg)) {
    return '视觉方案准备完成';
  }
  if (/台词结构|结构化为 beats|beats|Opus|opus|AGY|DeepSeek|SCodex|模型/.test(msg)) {
    return '正在准备视觉方案...';
  }
  return msg;
}

function hasLineOutputs(node: NodeState): boolean {
  if (node.status !== 'done') return false;
  const beats = node.outputs?.beats_count;
  return (typeof beats === 'number' && beats > 0) || typeof node.outputs?.lines_relpath === 'string';
}

function isLineStale(lines: NodeState, rw: NodeState): boolean {
  if (lines.status !== 'done' || rw.status !== 'done') return false;
  if (!hasLineOutputs(lines)) return true;
  // 当前 job state 没有源版本号；第一版用 finished_at 判断柳永定稿是否晚于视觉准备。
  return !!(rw.finished_at && lines.finished_at && lines.finished_at < rw.finished_at);
}

interface SceneGroup {
  group: string;
  scenes: Array<{ id: string; scene: Scene }>;
}

// 按 scene.group 把 scenes 分段（连续相同 group 合一段；缺省用 scene id 自成一段）
function groupScenes(scenes: Record<string, Scene>): SceneGroup[] {
  const out: SceneGroup[] = [];
  for (const [id, scene] of Object.entries(scenes)) {
    const g = scene.group || id;
    const last = out[out.length - 1];
    if (last && last.group === g) last.scenes.push({ id, scene });
    else out.push({ group: g, scenes: [{ id, scene }] });
  }
  return out;
}

export function StoryboardPanel({
  jobId,
  nodeDef,
  nodeState,
  rwNodeState,
  preflightNodeState,
  onRequestDraftSelection,
  onAdvanced,
}: Props) {
  const { showToast } = useToast();
  const status = nodeState.status;
  const scenesCount = (nodeState.outputs?.scenes_count as number | undefined) ?? 0;
  const sketchesCount = (nodeState.outputs?.sketches_count as number | undefined) ?? 0;

  const [episode, setEpisode] = useState<Episode | null>(null);
  const [epErr, setEpErr] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [advanceBusy, setAdvanceBusy] = useState(false);
  const [pendingRerun, setPendingRerun] = useState(false);
  const [preflightRunErr, setPreflightRunErr] = useState<string | null>(null);
  const [preflightBusy, setPreflightBusy] = useState(false);
  const autoPreflightKeys = useRef<Set<string>>(new Set());

  const rwState = rwNodeState;
  const lineState = preflightNodeState;
  const linesStale = isLineStale(lineState, rwState);
  const rwSelected = hasSelectedRwDraft(rwState);
  const missingSelectedDraft = rwState.status === 'done' && !rwSelected;
  const missingDraftFailed = lineState.status === 'failed' && isMissingDraftError(lineState.error);
  const preflightReady = lineState.status === 'done' && !linesStale && hasLineOutputs(lineState);
  const preflightActive = isActive(lineState.status);
  const rwReady = rwState.status === 'done';
  const canPreparePreflight = rwReady && rwSelected;

  const runPreflight = useCallback(async () => {
    if (!canPreparePreflight) return;
    setPreflightBusy(true);
    setPreflightRunErr(null);
    try {
      await api.runNode(jobId, PREFLIGHT_NODE);
    } catch (e) {
      const msg = (e as Error).message;
      setPreflightRunErr(msg);
      showToast('准备视觉方案失败，请稍后重试');
      console.error('[StoryboardPanel] preflight failed', e);
    } finally {
      setPreflightBusy(false);
    }
  }, [canPreparePreflight, jobId, showToast]);

  useEffect(() => {
    if (!canPreparePreflight || preflightBusy) return;
    const missingOrIdle = lineState.status === 'idle';
    const staleDone = lineState.status === 'done' && linesStale;
    const recoverableFailed = missingDraftFailed;
    if (!missingOrIdle && !staleDone && !recoverableFailed) return;

    const key = [
      jobId,
      rwState.finished_at ?? 'rw',
      rwState.outputs?.selected_model_id ?? 'unselected',
      lineState.status,
      lineState.finished_at ?? 'none',
      staleDone ? 'stale' : 'fresh',
    ].join(':');
    if (autoPreflightKeys.current.has(key)) return;
    autoPreflightKeys.current.add(key);
    void runPreflight();
  }, [
    canPreparePreflight,
    jobId,
    lineState,
    linesStale,
    missingDraftFailed,
    preflightBusy,
    runPreflight,
    rwState.finished_at,
    rwState.outputs,
  ]);

  // 只在 done 时拉 episode（running/idle 时 scenes 还没产出）
  useEffect(() => {
    if (status !== 'done') {
      setEpisode(null);
      return;
    }
    api.getEpisode(jobId)
      .then((ep) => { setEpisode(ep); setEpErr(null); })
      .catch((e: Error) => setEpErr(e.message));
  }, [jobId, status, nodeState.finished_at]);

  // 防抖落盘整份 episode（沿用 ImageResultPanel 模式）
  const debounceTimer = useRef<number | null>(null);
  const pendingEpRef = useRef<Episode | null>(null);
  const [saveTick, setSaveTick] = useState(0);
  void saveTick;

  const flushEpisode = useCallback(async (): Promise<void> => {
    if (debounceTimer.current != null) {
      window.clearTimeout(debounceTimer.current);
      debounceTimer.current = null;
    }
    const ep = pendingEpRef.current;
    if (!ep) return;
    pendingEpRef.current = null;
    setSaveTick((x) => x + 1);
    try {
      await api.putEpisode(jobId, ep);
    } catch (e) {
      pendingEpRef.current = ep;
      console.error('[storyboard] save episode failed', e);
    }
    setSaveTick((x) => x + 1);
  }, [jobId]);

  const patchScene = useCallback(
    (sceneId: string, mutate: (sc: Scene) => void) => {
      setEpisode((prev) => {
        if (!prev) return prev;
        const next: Episode = JSON.parse(JSON.stringify(prev));
        const sc = next.scenes[sceneId];
        if (sc) mutate(sc);
        pendingEpRef.current = next;
        return next;
      });
      setSaveTick((x) => x + 1);
      if (debounceTimer.current != null) window.clearTimeout(debounceTimer.current);
      debounceTimer.current = window.setTimeout(() => { void flushEpisode(); }, 600);
    },
    [flushEpisode],
  );

  async function doRun() {
    setActionBusy(true);
    try {
      await api.runNode(jobId, nodeDef.name);
    } catch (e) {
      showToast('启动失败，请稍后再试');
      console.error('[StoryboardPanel] 启动失败', e);
    } finally {
      setActionBusy(false);
    }
  }

  async function doCancel() {
    setActionBusy(true);
    try {
      await api.cancelNode(jobId, nodeDef.name);
    } catch (e) {
      showToast('停止失败，请稍后再试');
      console.error('[StoryboardPanel] 停止失败', e);
    } finally {
      setActionBusy(false);
    }
  }

  async function doAdvance() {
    setAdvanceBusy(true);
    try {
      await flushEpisode();
      await api.runNode(jobId, NEXT_NODE);
      onAdvanced?.();
    } catch (e) {
      showToast('交给伯牙失败，请稍后再试');
      console.error('[StoryboardPanel] advance to boya failed', e);
    } finally {
      setAdvanceBusy(false);
    }
  }

  function renderActionBtn() {
    if (status === 'running' || status === 'queued') {
      return (
        <button className="btn primary sm" disabled={actionBusy} onClick={doCancel}>
          <Square size={11} strokeWidth={2.2} fill="currentColor" /> 停止
        </button>
      );
    }
    if (status === 'done') {
      return (
        <button
          className="btn primary sm"
          title="重做视觉方案（会覆盖 scenes 与下游状态）"
          disabled={actionBusy}
          onClick={() => setPendingRerun(true)}
        >
          <RefreshCw size={12} strokeWidth={1.9} /> 重新执行
        </button>
      );
    }
    return (
      <button className="btn primary sm" disabled={actionBusy} onClick={doRun}>
        <Clapperboard size={12} strokeWidth={2} /> 生成视觉方案
      </button>
    );
  }

  const statusBadge =
    status === 'running' ? ' · RUNNING'
      : status === 'queued' ? ' · QUEUED'
      : status === 'failed' ? ' · FAILED'
      : '';
  const hasPending = pendingEpRef.current != null;

  const groups = useMemo(
    () => (episode?.scenes ? groupScenes(episode.scenes) : []),
    [episode],
  );

  let hint: { tone: 'info' | 'error'; text: string } | null = null;
  if (epErr) {
    hint = { tone: 'error', text: `episode 加载失败：${epErr}` };
  } else if (status === 'idle') {
    hint = { tone: 'info', text: '吴道子会把成稿转成视觉方案：子场景、容器图提示词与简笔画设计。' };
  }

  if (!preflightReady) {
    const lineStatus = lineState.status;
    const rawPreflightError = lineState.error || preflightRunErr;
    const preflightError = rawPreflightError ? friendlyPreflightError(rawPreflightError) : '';
    const preflightProgress = friendlyPreflightProgress(lineState.progress);
    const waitingForRw = !rwReady;
    const waitingForDraftSelection = !waitingForRw && missingSelectedDraft;
    const rowStatus: ProcStatus =
      waitingForDraftSelection ? 'pending'
        : preflightRunErr ? 'failed'
        : lineStatus === 'failed' ? 'failed'
          : preflightActive || preflightBusy ? 'running'
            : 'pending';
    const failedText = preflightError.startsWith('视觉方案准备')
      ? preflightError
      : `视觉方案准备失败：${preflightError || '未知错误'}`;
    const launchFailedText = preflightError.startsWith('视觉方案准备')
      ? preflightError
      : `视觉方案准备启动失败：${preflightError || '未知错误'}`;
    const readyText =
      waitingForRw ? '先完成柳永成稿，再进入吴道子。'
        : waitingForDraftSelection ? '柳永已经产出候选稿；请先选择一版定稿，再交给吴道子。'
          : preflightRunErr ? launchFailedText
            : lineStatus === 'failed' ? failedText
              : linesStale ? '柳永成稿已更新，正在重新准备视觉方案。'
                : '正在准备视觉方案，完成后进入工作台。';
    const canRetry = canPreparePreflight && !preflightActive && !preflightBusy;

    return (
      <div className="rw-panel-root">
        <div className={`panel-hint ${!waitingForDraftSelection && (lineStatus === 'failed' || preflightRunErr) ? 'panel-hint-error' : 'panel-hint-info'}`}>
          {readyText}
        </div>

        <div className="proc-rows" style={{ marginBottom: 'var(--s-3)' }}>
          <ProcStatusRow
            row={{
              id: 'wudaozi-preflight',
              label: '视觉方案准备',
              status: rowStatus,
              detail: preflightError || preflightProgress || undefined,
            }}
            runningText="准备中"
          />
        </div>

        {(preflightActive || preflightBusy) && preflightProgress && (
          <div className="dim-mono">{preflightProgress}</div>
        )}

        <div className="rw-panel-header">
          <div
            className={`section-h${preflightActive || preflightBusy ? ' loading' : ''}`}
            style={{ margin: 0, flex: 1 }}
          >
            视觉工作台准备
          </div>
          {waitingForDraftSelection && onRequestDraftSelection ? (
            <button className="btn primary sm" onClick={onRequestDraftSelection}>
              <Play size={12} strokeWidth={2} fill="currentColor" /> 回柳永定稿
            </button>
          ) : null}
          {canRetry ? (
            <button className="btn primary sm" disabled={preflightBusy} onClick={() => { void runPreflight(); }}>
              {lineStatus === 'failed' ? (
                <RefreshCw size={12} strokeWidth={1.9} />
              ) : (
                <Play size={12} strokeWidth={2} fill="currentColor" />
              )}
              {lineStatus === 'failed' ? '重试准备' : '重新准备'}
            </button>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div className="rw-panel-root">
      {hint && <div className={`panel-hint panel-hint-${hint.tone}`}>{hint.text}</div>}

      <div className="proc-rows" style={{ marginBottom: 'var(--s-3)' }}>
        <ProcStatusRow
          row={{
            id: 'wudaozi-preflight',
            label: `视觉方案准备完成 · ${(lineState.outputs?.beats_count as number | undefined) ?? 0} 句`,
            status: 'done',
          }}
        />
      </div>

      {status !== 'idle' && (
        <div className="proc-rows" style={{ marginBottom: 'var(--s-3)' }}>
          <ProcStatusRow
            row={{
              id: 'storyboard',
              label: '视觉方案（子场景 + 提示词 + 简笔画设计）',
              status: status === 'done' ? 'done' : status === 'failed' ? 'failed' : 'running',
            }}
            runningText="设计中"
          />
        </div>
      )}

      <div className="rw-panel-header">
        <div
          className={`section-h${status === 'running' || status === 'queued' ? ' loading' : ''}`}
          style={{ margin: 0, flex: 1 }}
        >
          视觉方案 · {scenesCount} 子场景 · {sketchesCount} 简笔画{statusBadge}
          {hasPending && (
            <span className="dim-mono" style={{ marginLeft: 6, fontSize: 'var(--text-2xs)' }}>
              · 保存中…
            </span>
          )}
        </div>
        {renderActionBtn()}
      </div>

      {(status === 'running' || status === 'queued') && nodeState.progress && (
        <div className="dim-mono">{nodeState.progress}</div>
      )}

      {status !== 'done' ? null : (
        <>
          <div className="sb-list">
            {groups.map((grp) => (
              <section key={grp.group} className="sb-group">
                <header className="sb-group-head">
                  <span className="lines-scene-tag">{grp.group}</span>
                  <span className="dim-mono">{grp.scenes.length} 子场景</span>
                </header>
                {grp.scenes.map(({ id, scene }) => (
                  <article key={id} className="sb-scene">
                    <div className="sb-scene-head">
                      <span className="image-card-id mono">{id}</span>
                      <span className="dim-mono">{(scene.sketches?.length ?? 0)} 简笔画</span>
                    </div>
                    <textarea
                      className="field sb-container-prompt"
                      value={scene.prompt}
                      placeholder="容器图 prompt（暖纸底 + 稀疏背景）"
                      rows={5}
                      spellCheck={false}
                      onChange={(e) => patchScene(id, (sc) => { sc.prompt = e.target.value; })}
                    />
                    {(scene.sketches ?? []).map((sk, n) => (
                      <SketchRow
                        key={n}
                        n={n}
                        sketch={sk}
                        onPatch={(mut) => patchScene(id, (sc) => {
                          if (sc.sketches && sc.sketches[n]) mut(sc.sketches[n]);
                        })}
                      />
                    ))}
                  </article>
                ))}
              </section>
            ))}
            {groups.length === 0 && (
              <div className="dim-mono">（暂无视觉方案；可重新生成，或检查柳永成稿是否为空）</div>
            )}
          </div>
          <div className="image-footer">
            <button
              type="button"
              className="btn primary sm"
              title="确认视觉方案 · 交给伯牙配音"
              disabled={advanceBusy || actionBusy || status !== 'done'}
              onClick={doAdvance}
            >
              <Play size={12} strokeWidth={2} fill="currentColor" /> 交给伯牙
            </button>
          </div>
        </>
      )}

      <ConfirmDialog
        open={pendingRerun}
        title="重做视觉方案？"
        message={<>会重新切子场景并覆盖 scenes{'{}'}、画面提示词与简笔画设计，同时重置下游声音和画面资产。</>}
        confirmLabel="重新执行"
        danger
        onConfirm={async () => {
          await doRun();
          setPendingRerun(false);
        }}
        onCancel={() => setPendingRerun(false)}
      />
    </div>
  );
}

function SketchRow({
  n,
  sketch,
  onPatch,
}: {
  n: number;
  sketch: Sketch;
  onPatch: (mutate: (sk: Sketch) => void) => void;
}) {
  return (
    <div className="sb-sketch">
      <span className="sb-sketch-idx mono">sk{n + 1}</span>
      <div className="sb-sketch-body">
        <textarea
          className="field sb-sketch-prompt"
          value={sketch.prompt}
          placeholder="简笔画单格内容（english，圣经自动前置）"
          rows={5}
          spellCheck={false}
          onChange={(e) => onPatch((sk) => { sk.prompt = e.target.value; })}
        />
        <div className="sb-sketch-meta">
          <label className="sb-num">x
            <input
              type="number" min={0} max={100}
              value={Math.round(sketch.pos?.x ?? 50)}
              onChange={(e) => onPatch((sk) => { sk.pos = { ...sk.pos, x: Number(e.target.value) }; })}
            />
          </label>
          <label className="sb-num">y
            <input
              type="number" min={0} max={100}
              value={Math.round(sketch.pos?.y ?? 50)}
              onChange={(e) => onPatch((sk) => { sk.pos = { ...sk.pos, y: Number(e.target.value) }; })}
            />
          </label>
          <label className="sb-num">w
            <input
              type="number" min={5} max={100}
              value={Math.round(sketch.size ?? 30)}
              onChange={(e) => onPatch((sk) => { sk.size = Number(e.target.value); })}
            />
          </label>
          <label className="sb-at">跟词
            <input
              type="text"
              value={sketch.at?.match ?? ''}
              placeholder="台词关键词"
              onChange={(e) => onPatch((sk) => {
                const m = e.target.value.trim();
                if (m) sk.at = { ...(sk.at ?? {}), match: m };
                else delete sk.at;
              })}
            />
          </label>
        </div>
      </div>
    </div>
  );
}
