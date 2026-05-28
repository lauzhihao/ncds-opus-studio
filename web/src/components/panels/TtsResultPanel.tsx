// tts 节点抽屉：按 beats 顺序逐行渲染 [NN] [zh 单行输入] [▶ 试听] [↻ 重生]，
// 顶部右整体 开始/停止/重新执行，底部右"用此配音 · 下一步"启动 image。
//
// 数据来源
//   - episode.json beats 数组 → zh 的 source of truth（可编辑回写）
//   - 节点 outputs.items → 每条的 audio_relpath（null = 未生成 / mock）
// zh 编辑走 EpisodeEditorPanel 同款：本地 patch + 防抖 putEpisode。
// 试听按钮按 HTML5 <audio> 一次播一条；audio_relpath null 时 disabled。

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Pause, Play, RefreshCw, Square, VolumeX } from 'lucide-react';

import { api } from '../../api/client';
import type { Episode, NodeState, PipelineNodeDef, TtsItem } from '../../api/types';
import { ConfirmDialog } from '../ConfirmDialog';

interface Props {
  jobId: string;
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
  onAdvanced?: () => void;
}

const NEXT_NODE = 'image';

export function TtsResultPanel({ jobId, nodeDef, nodeState, onAdvanced }: Props) {
  const items = useMemo<TtsItem[]>(
    () => (nodeState.outputs?.items as TtsItem[] | undefined) ?? [],
    [nodeState.outputs],
  );
  const status = nodeState.status;

  const [episode, setEpisode] = useState<Episode | null>(null);
  const [epErr, setEpErr] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [advanceBusy, setAdvanceBusy] = useState(false);
  const [regenBusy, setRegenBusy] = useState<Record<number, boolean>>({});
  const [pendingRerun, setPendingRerun] = useState(false);
  const [playingIdx, setPlayingIdx] = useState<number | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    api.getEpisode(jobId)
      .then((ep) => { setEpisode(ep); setEpErr(null); })
      .catch((e: Error) => setEpErr(e.message));
  }, [jobId, nodeState.finished_at]);

  // 卸载时停掉音频
  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
    };
  }, []);

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
      console.error('[tts] save episode failed', e);
    }
    setSaveTick((x) => x + 1);
  }, [jobId]);

  const patchZh = useCallback(
    (index: number, zh: string) => {
      setEpisode((prev) => {
        if (!prev) return prev;
        const next: Episode = JSON.parse(JSON.stringify(prev));
        const i = index - 1;
        if (next.beats[i]) {
          next.beats[i].zh = zh;
        }
        pendingEpRef.current = next;
        return next;
      });
      setSaveTick((x) => x + 1);
      if (debounceTimer.current != null) window.clearTimeout(debounceTimer.current);
      debounceTimer.current = window.setTimeout(() => {
        void flushEpisode();
      }, 600);
    },
    [flushEpisode],
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

  async function doRegen(index: number) {
    setRegenBusy((m) => ({ ...m, [index]: true }));
    try {
      await flushEpisode();
      await api.regenTtsBeat(jobId, index);
    } catch (e) {
      alert(`重生失败: ${(e as Error).message}`);
    } finally {
      setRegenBusy((m) => ({ ...m, [index]: false }));
    }
  }

  function togglePlay(item: TtsItem) {
    if (!item.audio_relpath) return;
    // 同行二次点击 → 暂停
    if (playingIdx === item.index && audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
      setPlayingIdx(null);
      return;
    }
    // 切换：停掉旧的，播新的
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    const el = new Audio(`/jobs/${jobId}/files/${item.audio_relpath}`);
    el.onended = () => {
      setPlayingIdx((cur) => (cur === item.index ? null : cur));
      if (audioRef.current === el) audioRef.current = null;
    };
    el.onerror = () => {
      setPlayingIdx((cur) => (cur === item.index ? null : cur));
      if (audioRef.current === el) audioRef.current = null;
    };
    audioRef.current = el;
    setPlayingIdx(item.index);
    void el.play().catch(() => {
      setPlayingIdx(null);
      audioRef.current = null;
    });
  }

  async function doAdvance() {
    setAdvanceBusy(true);
    try {
      await flushEpisode();
      await api.runNode(jobId, NEXT_NODE);
      onAdvanced?.();
    } catch (e) {
      alert(`启动 IMAGE 失败: ${(e as Error).message}`);
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
          title="清空 tts 及下游产物后整体重新跑"
          disabled={actionBusy}
          onClick={() => setPendingRerun(true)}
        >
          <RefreshCw size={12} strokeWidth={1.9} /> 重新执行
        </button>
      );
    }
    return (
      <button className="btn primary sm" disabled={actionBusy} onClick={doRun}>
        <Play size={12} strokeWidth={2} /> 开始配音
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
  const hasPending = pendingEpRef.current != null;

  return (
    <div className="rw-panel-root">
      <div className="rw-panel-header">
        <div className="section-h" style={{ margin: 0, flex: 1 }}>
          TTS 配音 · {items.length} 句{statusBadge}
          {hasPending && (
            <span className="dim-mono" style={{ marginLeft: 6, fontSize: 'var(--text-2xs)' }}>
              · 保存中…
            </span>
          )}
        </div>
        {renderActionBtn()}
      </div>

      {status === 'running' && (
        <div className="dim-mono">{nodeState.progress || '正在配音…'}</div>
      )}
      {status === 'failed' && nodeState.error && (
        <div className="asr-error">失败：{nodeState.error}</div>
      )}
      {epErr && (
        <div className="asr-error">episode 加载失败：{epErr}</div>
      )}

      {items.length === 0 ? (
        <div className="dim-mono">
          {status === 'idle' || status === 'failed'
            ? '尚未配音，点击右上「开始配音」启动。'
            : status === 'running' || status === 'queued'
              ? '配音中…'
              : '暂无字幕；先在 LINES / PREVIEW 抽屉里编辑 beats。'}
        </div>
      ) : (
        <>
          <div className="tts-list">
            {items.map((it) => {
              const zh = episode?.beats?.[it.index - 1]?.zh ?? it.zh;
              const playing = playingIdx === it.index;
              const canPlay = !!it.audio_relpath;
              return (
                <div key={it.index} className="tts-row">
                  <span className="tts-row-num mono">{String(it.index).padStart(2, '0')}</span>
                  <input
                    type="text"
                    className="field tts-row-input"
                    value={zh}
                    onChange={(e) => patchZh(it.index, e.target.value)}
                    placeholder="（空字幕）"
                    spellCheck={false}
                  />
                  <button
                    type="button"
                    className="btn sm icon-only ghost"
                    title={canPlay ? (playing ? '暂停' : '试听') : 'mock 模式无音频'}
                    disabled={!canPlay || actionBusy || advanceBusy}
                    onClick={() => togglePlay(it)}
                  >
                    {!canPlay ? (
                      <VolumeX size={12} strokeWidth={1.7} />
                    ) : playing ? (
                      <Pause size={12} strokeWidth={1.8} fill="currentColor" />
                    ) : (
                      <Play size={12} strokeWidth={1.8} fill="currentColor" />
                    )}
                  </button>
                  <button
                    type="button"
                    className="btn sm icon-only ghost"
                    title={regenBusy[it.index] ? '生成中…' : '按当前文本重生此条音频'}
                    disabled={status !== 'done' || regenBusy[it.index] || actionBusy || advanceBusy}
                    onClick={() => doRegen(it.index)}
                  >
                    <RefreshCw size={12} strokeWidth={1.7} />
                  </button>
                </div>
              );
            })}
          </div>
          <div className="image-footer">
            <button
              type="button"
              className="btn primary sm"
              title="用此配音 · 下一步（启动 IMAGE）"
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
        title="重新执行 TTS？"
        message={<>会清空所有音频产物 + 所有下游节点的状态与产物，然后整体重新配音。</>}
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
