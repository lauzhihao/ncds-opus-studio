// storyboard（分镜）节点抽屉：展示 / 微调 director agent 产出的视觉层。
//
// 数据源：02_rw/episode.json 的 scenes{}（director 产出，含每个子场景的容器图 prompt
//         与 sketches[]）+ beats[].scene（子场景归属）。本面板按 scene.group 分段展示，
//         允许就地编辑容器 prompt / 简笔画 prompt / pos / at.match，防抖整份回写 episode。
// 顶部右：开始分镜 / 停止 / 重新执行；底部右：用此分镜 · 下一步 → runNode('tts')。
//
// 风格对齐 BEATS/TTS：panel-hint banner + proc-rows 状态行 + section-h loading。

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Clapperboard, Play, RefreshCw, Square } from 'lucide-react';

import { api } from '../../api/client';
import type { Episode, NodeState, PipelineNodeDef, Scene, Sketch } from '../../api/types';
import { ConfirmDialog } from '../ConfirmDialog';
import { useToast } from '../Toast';
import { ProcStatusRow } from './RwResultPanel';

interface Props {
  jobId: string;
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
  onAdvanced?: () => void;
}

const NEXT_NODE = 'tts';

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

export function StoryboardPanel({ jobId, nodeDef, nodeState, onAdvanced }: Props) {
  const { showToast } = useToast();
  const status = nodeState.status;
  const scenesCount = (nodeState.outputs?.scenes_count as number | undefined) ?? 0;
  const sketchesCount = (nodeState.outputs?.sketches_count as number | undefined) ?? 0;

  const [episode, setEpisode] = useState<Episode | null>(null);
  const [epErr, setEpErr] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [advanceBusy, setAdvanceBusy] = useState(false);
  const [pendingRerun, setPendingRerun] = useState(false);

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
      showToast('启动 TTS 失败，请稍后再试');
      console.error('[StoryboardPanel] 启动 TTS 失败', e);
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
          title="重跑 director 分镜（会覆盖 scenes 与下游状态）"
          disabled={actionBusy}
          onClick={() => setPendingRerun(true)}
        >
          <RefreshCw size={12} strokeWidth={1.9} /> 重新执行
        </button>
      );
    }
    return (
      <button className="btn primary sm" disabled={actionBusy} onClick={doRun}>
        <Clapperboard size={12} strokeWidth={2} /> 开始分镜
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
  if (status === 'failed' && nodeState.error) {
    hint = { tone: 'error', text: `失败：${nodeState.error}` };
  } else if (epErr) {
    hint = { tone: 'error', text: `episode 加载失败：${epErr}` };
  } else if (status === 'idle') {
    hint = { tone: 'info', text: '点击右上「开始分镜」，导演 agent 会切子场景并设计容器图与简笔画。' };
  }

  return (
    <div className="rw-panel-root">
      {hint && <div className={`panel-hint panel-hint-${hint.tone}`}>{hint.text}</div>}

      {status !== 'idle' && (
        <div className="proc-rows" style={{ marginBottom: 'var(--s-3)' }}>
          <ProcStatusRow
            row={{
              id: 'storyboard',
              label: '导演分镜（子场景 + 简笔画设计）',
              status: status === 'done' ? 'done' : status === 'failed' ? 'failed' : 'running',
            }}
            runningText="分镜中"
          />
        </div>
      )}

      <div className="rw-panel-header">
        <div
          className={`section-h${status === 'running' || status === 'queued' ? ' loading' : ''}`}
          style={{ margin: 0, flex: 1 }}
        >
          分镜 · {scenesCount} 子场景 · {sketchesCount} 简笔画{statusBadge}
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
              <div className="dim-mono">（scenes 为空，重跑分镜或检查 BEATS 是否有内容）</div>
            )}
          </div>
          <div className="image-footer">
            <button
              type="button"
              className="btn primary sm"
              title="用此分镜 · 下一步（启动 TTS）"
              disabled={advanceBusy || actionBusy || status !== 'done'}
              onClick={doAdvance}
            >
              <Play size={12} strokeWidth={2} fill="currentColor" /> 下一步
            </button>
          </div>
        </>
      )}

      <ConfirmDialog
        open={pendingRerun}
        title="重跑分镜？"
        message={<>会让 director agent 重新切子场景并覆盖 scenes{'{}'}，并重置下游（TTS/IMAGE…）的状态与产物。</>}
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
