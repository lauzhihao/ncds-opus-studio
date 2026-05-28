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
import { ProcStatusRow } from './RwResultPanel';

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
  const sceneGroups = useMemo(() => groupItemsByScene(items), [items]);
  const status = nodeState.status;

  const [episode, setEpisode] = useState<Episode | null>(null);
  const [epErr, setEpErr] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [advanceBusy, setAdvanceBusy] = useState(false);
  const [regenBusy, setRegenBusy] = useState<Record<string, boolean>>({});
  const [pendingRerun, setPendingRerun] = useState(false);
  const [playingScene, setPlayingScene] = useState<string | null>(null);
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

  // scene 级重生（015）：重合成整个 scene，与 UI 渲染粒度一致
  async function doRegenScene(sceneId: string) {
    setRegenBusy((m) => ({ ...m, [sceneId]: true }));
    try {
      await flushEpisode();
      await api.regenTtsScene(jobId, sceneId);
    } catch (e) {
      alert(`重生失败: ${(e as Error).message}`);
    } finally {
      setRegenBusy((m) => ({ ...m, [sceneId]: false }));
    }
  }

  // 试听整段 scene 音频（audio_relpath 是 scene 整段 mp3）
  function togglePlayScene(sceneKey: string, audioRelpath: string | null) {
    if (!audioRelpath) return;
    if (playingScene === sceneKey && audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
      setPlayingScene(null);
      return;
    }
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    const el = new Audio(`/jobs/${jobId}/files/${audioRelpath}`);
    const clear = () => {
      setPlayingScene((cur) => (cur === sceneKey ? null : cur));
      if (audioRef.current === el) audioRef.current = null;
    };
    el.onended = clear;
    el.onerror = clear;
    audioRef.current = el;
    setPlayingScene(sceneKey);
    void el.play().catch(clear);
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

  // 提示 banner（标题上方，统一风格）
  let hint: { tone: 'info' | 'error'; text: string } | null = null;
  if (status === 'failed' && nodeState.error) {
    hint = { tone: 'error', text: `失败：${nodeState.error}` };
  } else if (epErr) {
    hint = { tone: 'error', text: `episode 加载失败：${epErr}` };
  } else if (status === 'idle') {
    hint = { tone: 'info', text: '点击下方「开始配音」启动，按 scene 整段合成。' };
  } else if (items.length === 0 && status === 'done') {
    hint = { tone: 'info', text: '暂无字幕；先在 BEATS 抽屉里编辑。' };
  }

  return (
    <div className="rw-panel-root">
      {hint && <div className={`panel-hint panel-hint-${hint.tone}`}>{hint.text}</div>}

      {/* 配音状态行：跑过就常驻（done 后不消失，与 BEATS/RW 一致） */}
      {status !== 'idle' && (
        <div className="proc-rows" style={{ marginBottom: 'var(--s-3)' }}>
          <ProcStatusRow
            row={{
              id: 'tts',
              label: '分段式高情感度语音合成',
              status: status === 'done' ? 'done' : status === 'failed' ? 'failed' : 'running',
            }}
            runningText="合成中"
          />
        </div>
      )}

      <div className="rw-panel-header">
        <div
          className={`section-h${status === 'running' || status === 'queued' ? ' loading' : ''}`}
          style={{ margin: 0, flex: 1 }}
        >
          TTS / {sceneGroups.length} 段 / {items.length} 句{statusBadge}
          {hasPending && (
            <span className="dim-mono" style={{ marginLeft: 6, fontSize: 'var(--text-2xs)' }}>
              · 保存中…
            </span>
          )}
        </div>
        {renderActionBtn()}
      </div>

      {items.length === 0 ? null : (
        <>
          <div className="tts-list">
            {sceneGroups.map((g) => {
              const playing = playingScene === g.key;
              const canPlay = !!g.audioRelpath;
              const busy = !!regenBusy[g.scene];
              return (
                <section key={g.key} className="tts-scene">
                  <header className="tts-scene-head">
                    <span className="lines-scene-tag">{g.scene || `#${g.items[0].index}`}</span>
                    <span style={{ flex: 1 }} />
                    <button
                      type="button"
                      className="btn sm icon-only ghost"
                      title={canPlay ? (playing ? '暂停' : '试听整段') : '无音频'}
                      disabled={!canPlay || actionBusy || advanceBusy}
                      onClick={() => togglePlayScene(g.key, g.audioRelpath)}
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
                      title={busy ? '重生中…' : '按当前文本重生此段音频'}
                      disabled={!g.scene || status !== 'done' || busy || actionBusy || advanceBusy}
                      onClick={() => doRegenScene(g.scene)}
                    >
                      <RefreshCw size={12} strokeWidth={1.7} />
                    </button>
                  </header>
                  {g.items.map((it) => {
                    const zh = episode?.beats?.[it.index - 1]?.zh ?? it.zh;
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
                      </div>
                    );
                  })}
                </section>
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

// 按 scene 把 beats 分组（连续相同 scene 合一组）。一组共享一个整段 scene mp3。
// items 无 scene 时退化为每 beat 一组（key 用 index）。
interface TtsSceneGroup {
  key: string;
  scene: string;
  audioRelpath: string | null;
  items: TtsItem[];
}
// 按 scene 分组（连续相同 scene 合一组），一组共享一段整段 scene mp3。
function groupItemsByScene(items: TtsItem[]): TtsSceneGroup[] {
  const groups: TtsSceneGroup[] = [];
  for (const it of items) {
    const scene = it.scene;
    const last = groups[groups.length - 1];
    if (last && last.key === scene) {
      last.items.push(it);
    } else {
      groups.push({ key: scene, scene, audioRelpath: it.audio_relpath, items: [it] });
    }
  }
  return groups;
}
