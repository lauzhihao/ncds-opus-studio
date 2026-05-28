// lines 节点抽屉：beats 列表编辑。
//
// 数据源：02_rw/episode.json 里的 beats[]（用户在 RW 抽屉选模型时由 selectRwModel
//        把 02_rw/<model_id>/episode.json 拷过来）。本面板直接 read/write 这份文件。
// 编辑形态：每条 beat 一行，zh / en 独立 input；scene 字段以小标签显示（只读，避免
//          用户改坏命名约定）；chapter 字段保留写回不暴露。
// 防抖保存：beats 数组变化 600ms 后整段写回 episode.json（仅 replace beats[]，保留
//          scenes/audio/fonts 等其他字段）。
// 下一步：「用此台词稿 · 下一步」flush 草稿后 runNode('tts')。

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Play, Plus, RefreshCw, Square, Trash2 } from 'lucide-react';

import { api } from '../../api/client';
import type { NodeState, PipelineNodeDef } from '../../api/types';
import { ConfirmDialog } from '../ConfirmDialog';
import { ProcStatusRow } from './RwResultPanel';

interface Props {
  jobId: string;
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
  onAdvanced?: () => void;
}

const NEXT_NODE = 'tts';
const EPISODE_RELPATH = '02_rw/episode.json';

interface Beat {
  zh: string;
  en?: string;
  scene?: string;
  chapter?: number | null;
}

export function LinesPanel({ jobId, nodeDef, nodeState, onAdvanced }: Props) {
  const status = nodeState.status;
  const beatsCount = (nodeState.outputs?.beats_count as number | undefined) ?? 0;

  // 完整 episode 缓存：写回时整份替换 beats[]，其他字段保留
  const episodeRef = useRef<Record<string, unknown> | null>(null);
  const [beats, setBeats] = useState<Beat[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [advanceBusy, setAdvanceBusy] = useState(false);
  const [pendingRerun, setPendingRerun] = useState(false);

  // 防抖落盘
  const pendingBeatsRef = useRef<Beat[] | null>(null);
  const debounceTimer = useRef<number | null>(null);
  const [saveTick, setSaveTick] = useState(0);

  // 加载 episode.json；node finished_at 变化时重拉（重跑后刷新）
  useEffect(() => {
    // 只在 done 时拉 episode.json；idle/queued/running/failed 时它还不存在（running 是
    // opus 正在结构化），fetch 会 404。
    if (status !== 'done') {
      setLoaded(false);
      setBeats([]);
      return;
    }
    setLoaded(false);
    setLoadErr(null);
    fetch(`/jobs/${jobId}/files/${EPISODE_RELPATH}`)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((text) => {
        let parsed: Record<string, unknown>;
        try {
          parsed = JSON.parse(text);
        } catch (e) {
          throw new Error(`episode.json 解析失败: ${(e as Error).message}`);
        }
        const raw = parsed?.beats;
        if (!Array.isArray(raw)) throw new Error('episode.beats[] 不存在或不是数组');
        episodeRef.current = parsed;
        setBeats(raw.map(normalizeBeat));
        setLoaded(true);
      })
      .catch((e: Error) => setLoadErr(e.message));
  }, [jobId, status, nodeState.finished_at]);

  const flush = useCallback(async (): Promise<void> => {
    if (debounceTimer.current != null) {
      window.clearTimeout(debounceTimer.current);
      debounceTimer.current = null;
    }
    const pending = pendingBeatsRef.current;
    if (pending == null || episodeRef.current == null) return;
    pendingBeatsRef.current = null;
    setSaveTick((x) => x + 1);
    const nextEpisode = { ...episodeRef.current, beats: pending };
    try {
      await api.writeFile(jobId, EPISODE_RELPATH, JSON.stringify(nextEpisode, null, 2));
      episodeRef.current = nextEpisode;
    } catch (e) {
      pendingBeatsRef.current = pending;
      console.error('[lines] save episode failed', e);
    }
    setSaveTick((x) => x + 1);
  }, [jobId]);

  const schedule = useCallback(
    (next: Beat[]) => {
      setBeats(next);
      pendingBeatsRef.current = next;
      setSaveTick((x) => x + 1);
      if (debounceTimer.current != null) window.clearTimeout(debounceTimer.current);
      debounceTimer.current = window.setTimeout(() => {
        void flush();
      }, 600);
    },
    [flush],
  );

  const updateBeat = useCallback(
    (idx: number, patch: Partial<Beat>) => {
      schedule(beats.map((b, i) => (i === idx ? { ...b, ...patch } : b)));
    },
    [beats, schedule],
  );

  const removeBeat = useCallback(
    (idx: number) => {
      schedule(beats.filter((_, i) => i !== idx));
    },
    [beats, schedule],
  );

  const addBeatAfter = useCallback(
    (idx: number) => {
      const prev = beats[idx];
      const newBeat: Beat = {
        zh: '',
        en: '',
        scene: prev?.scene ?? '',
        chapter: prev?.chapter ?? null,
      };
      const next = [...beats.slice(0, idx + 1), newBeat, ...beats.slice(idx + 1)];
      schedule(next);
    },
    [beats, schedule],
  );

  async function doRun() {
    setActionBusy(true);
    try {
      await api.runNode(jobId, nodeDef.name);
    } catch (e) {
      alert(`启动失败: ${(e as Error).message}`);
    } finally {
      setActionBusy(false);
    }
  }

  async function doCancel() {
    setActionBusy(true);
    try {
      await api.cancelNode(jobId, nodeDef.name);
    } catch (e) {
      alert(`停止失败: ${(e as Error).message}`);
    } finally {
      setActionBusy(false);
    }
  }

  async function doAdvance() {
    setAdvanceBusy(true);
    try {
      await flush();
      await api.runNode(jobId, NEXT_NODE);
      onAdvanced?.();
    } catch (e) {
      alert(`启动 TTS 失败: ${(e as Error).message}`);
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
          title="重新校验（不影响 beats 编辑内容）"
          disabled={actionBusy}
          onClick={() => setPendingRerun(true)}
        >
          <RefreshCw size={12} strokeWidth={1.9} /> 重新执行
        </button>
      );
    }
    return (
      <button className="btn primary sm" disabled={actionBusy} onClick={doRun}>
        <Play size={12} strokeWidth={2} /> 加载台词
      </button>
    );
  }

  const statusBadge =
    status === 'running'
      ? ' · RUNNING'
      : status === 'queued'
        ? ' · QUEUED'
        : status === 'failed'
          ? ' · FAILED'
          : '';
  const hasPending = pendingBeatsRef.current != null;
  void saveTick;

  const groups = useMemo(() => groupByScene(beats), [beats]);

  // 提示 banner（标题上方，统一风格）：idle 引导，failed 报错，加载失败提示
  let hint: { tone: 'info' | 'error'; text: string } | null = null;
  if (status === 'failed' && nodeState.error) {
    hint = { tone: 'error', text: `失败：${nodeState.error}` };
  } else if (loadErr) {
    hint = { tone: 'error', text: `加载失败：${loadErr}` };
  } else if (status === 'idle') {
    hint = { tone: 'info', text: '点击下方「加载台词」启动，AI 自动切分字幕。' };
  }

  return (
    <div className="rw-panel-root">
      {hint && <div className={`panel-hint panel-hint-${hint.tone}`}>{hint.text}</div>}

      {/* 结构化状态行：跑过就常驻（done 后不消失，参考 RW），与 RW/ASR 统一风格 */}
      {status !== 'idle' && (
        <div className="proc-rows" style={{ marginBottom: 'var(--s-3)' }}>
          <ProcStatusRow
            row={{
              id: 'lines',
              label: 'AI 切分字幕',
              status: status === 'done' ? 'done' : status === 'failed' ? 'failed' : 'running',
            }}
            runningText="结构化中"
          />
        </div>
      )}

      <div className="rw-panel-header">
        <div
          className={`section-h${status === 'running' || status === 'queued' ? ' loading' : ''}`}
          style={{ margin: 0, flex: 1 }}
        >
          台词稿 / {groups.length} 段 / {beats.length || beatsCount} 句{statusBadge}
          {hasPending && (
            <span className="dim-mono" style={{ marginLeft: 6, fontSize: 'var(--text-2xs)' }}>
              · 保存中…
            </span>
          )}
        </div>
        {renderActionBtn()}
      </div>

      {!loaded ? null : (
        <>
          <nav className="asr-tabs">
            <span className="asr-tab active" style={{ cursor: 'default' }}>
              逐句编辑
            </span>
            <span style={{ flex: 1 }} />
            <button
              type="button"
              className="btn sm icon-only primary"
              title="用此台词稿 · 下一步（启动 TTS）"
              disabled={advanceBusy || actionBusy || status !== 'done' || beats.length === 0}
              onClick={doAdvance}
            >
              <Play size={12} strokeWidth={2} fill="currentColor" />
            </button>
          </nav>

          <div className="lines-list">
            {groups.map((grp) => (
              <section key={grp.startIdx} className="lines-group">
                <header className="lines-group-head">
                  <span className="lines-scene-tag">{grp.scene || '(no scene)'}</span>
                  {grp.chapter != null && (
                    <span className="dim-mono">第 {grp.chapter} 章</span>
                  )}
                </header>
                {grp.beats.map((b, j) => {
                  const idx = grp.startIdx + j;
                  return (
                    <article key={idx} className="lines-row">
                      <span className="lines-row-num mono">{String(idx + 1).padStart(3, '0')}</span>
                      <div className="lines-row-body">
                        <input
                          type="text"
                          className="lines-zh"
                          value={b.zh}
                          placeholder="中文字幕"
                          onChange={(e) => updateBeat(idx, { zh: e.target.value })}
                        />
                        <input
                          type="text"
                          className="lines-en"
                          value={b.en ?? ''}
                          placeholder="英文译文（可选）"
                          onChange={(e) => updateBeat(idx, { en: e.target.value })}
                        />
                      </div>
                      <button
                        type="button"
                        className="btn sm icon-only ghost"
                        title="在此句后插入新句"
                        onClick={() => addBeatAfter(idx)}
                      >
                        <Plus size={12} strokeWidth={1.7} />
                      </button>
                      <button
                        type="button"
                        className="btn sm icon-only ghost danger"
                        title="删除此句"
                        onClick={() => removeBeat(idx)}
                      >
                        <Trash2 size={12} strokeWidth={1.7} />
                      </button>
                    </article>
                  );
                })}
              </section>
            ))}
            {beats.length === 0 && (
              <div className="dim-mono">（beats 列表为空，请在 RW 选个有内容的模型）</div>
            )}
          </div>
        </>
      )}

      <ConfirmDialog
        open={pendingRerun}
        title="重新加载台词稿？"
        message={<>会重新校验 02_rw/episode.json 并重置下游节点的状态。当前 beats 编辑内容不会丢失（写回的是同一份文件）。</>}
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

function normalizeBeat(raw: unknown): Beat {
  const b = (raw ?? {}) as Record<string, unknown>;
  return {
    zh: typeof b.zh === 'string' ? b.zh : '',
    en: typeof b.en === 'string' ? b.en : '',
    scene: typeof b.scene === 'string' ? b.scene : '',
    chapter: typeof b.chapter === 'number' ? b.chapter : null,
  };
}

// 按连续相同 scene 分组（章节标题只在 chapter 切换时显示）
function groupByScene(beats: Beat[]): Array<{
  startIdx: number;
  scene: string;
  chapter: number | null;
  beats: Beat[];
}> {
  const groups: Array<{ startIdx: number; scene: string; chapter: number | null; beats: Beat[] }> = [];
  beats.forEach((b, i) => {
    const last = groups[groups.length - 1];
    if (last && last.scene === (b.scene ?? '')) {
      last.beats.push(b);
    } else {
      groups.push({
        startIdx: i,
        scene: b.scene ?? '',
        chapter: b.chapter ?? null,
        beats: [b],
      });
    }
  });
  return groups;
}
