// image 节点抽屉：按 scenes 卡片栅格 + 每张卡可就地编辑 prompt + 单图重生 +
// 顶部右整体 开始/停止/重新执行，底部右"用此组图 · 下一步"启动 preview。
//
// 数据来源
//   - episode.json scenes 字典 → prompt 的 source of truth（可编辑回写）
//   - 节点 outputs.items → 每个 scene 的 image_relpath（null = 未生成 / mock）
// prompt 编辑走 EpisodeEditorPanel 同款模式：本地 patch + 防抖 putEpisode。

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ImageOff, Play, RefreshCw, Square } from 'lucide-react';

import { api } from '../../api/client';
import type { Episode, ImageItem, NodeState, PipelineNodeDef } from '../../api/types';
import { ConfirmDialog } from '../ConfirmDialog';
import { ProcStatusRow } from './RwResultPanel';

interface Props {
  jobId: string;
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
  onAdvanced?: () => void;
}

const NEXT_NODE = 'preview';

export function ImageResultPanel({ jobId, nodeDef, nodeState, onAdvanced }: Props) {
  const items = useMemo<ImageItem[]>(
    () => (nodeState.outputs?.items as ImageItem[] | undefined) ?? [],
    [nodeState.outputs],
  );
  const status = nodeState.status;

  const [episode, setEpisode] = useState<Episode | null>(null);
  const [epErr, setEpErr] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [advanceBusy, setAdvanceBusy] = useState(false);
  const [regenBusy, setRegenBusy] = useState<Record<string, boolean>>({});
  const [pendingRerun, setPendingRerun] = useState(false);

  // 加载 episode（拿 scenes 的 prompt 作为可编辑文本的 source of truth）；
  // 节点 finished_at 变化时重拉（重跑 image 后 prompt 可能没改但 episode mtime 变了）
  useEffect(() => {
    api.getEpisode(jobId)
      .then((ep) => { setEpisode(ep); setEpErr(null); })
      .catch((e: Error) => setEpErr(e.message));
  }, [jobId, nodeState.finished_at]);

  // 防抖落盘整份 episode（沿用 EpisodeEditorPanel 模式）
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
      console.error('[image] save episode failed', e);
    }
    setSaveTick((x) => x + 1);
  }, [jobId]);

  const patchPrompt = useCallback(
    (sceneId: string, prompt: string) => {
      setEpisode((prev) => {
        if (!prev) return prev;
        const next: Episode = JSON.parse(JSON.stringify(prev));
        if (next.scenes[sceneId]) {
          next.scenes[sceneId].prompt = prompt;
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

  async function doRegen(sceneId: string) {
    setRegenBusy((m) => ({ ...m, [sceneId]: true }));
    try {
      // 改了 prompt 后立刻重生：先 flush 草稿落盘 episode，再调 regen
      await flushEpisode();
      await api.regenImageScene(jobId, sceneId);
    } catch (e) {
      alert(`重生失败: ${(e as Error).message}`);
    } finally {
      setRegenBusy((m) => ({ ...m, [sceneId]: false }));
    }
  }

  async function doAdvance() {
    setAdvanceBusy(true);
    try {
      await flushEpisode();
      await api.runNode(jobId, NEXT_NODE);
      onAdvanced?.();
    } catch (e) {
      alert(`启动 PREVIEW 失败: ${(e as Error).message}`);
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
          title="清空 image 及下游产物后整体重新跑"
          disabled={actionBusy}
          onClick={() => setPendingRerun(true)}
        >
          <RefreshCw size={12} strokeWidth={1.9} /> 重新执行
        </button>
      );
    }
    return (
      <button className="btn primary sm" disabled={actionBusy} onClick={doRun}>
        <Play size={12} strokeWidth={2} /> 开始生图
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

  // 提示 banner（标题上方，统一风格）：idle 引导，failed 报错，episode 加载失败
  let hint: { tone: 'info' | 'error'; text: string } | null = null;
  if (status === 'failed' && nodeState.error) {
    hint = { tone: 'error', text: `失败：${nodeState.error}` };
  } else if (epErr) {
    hint = { tone: 'error', text: `episode 加载失败：${epErr}` };
  } else if (status === 'idle') {
    hint = { tone: 'info', text: '点击右上「开始生图」启动，按 scene 批量图生图。' };
  } else if (items.length === 0 && status === 'done') {
    hint = { tone: 'info', text: '暂无场景；先在 PREVIEW 抽屉里添加 scene。' };
  }

  return (
    <div className="rw-panel-root">
      {hint && <div className={`panel-hint panel-hint-${hint.tone}`}>{hint.text}</div>}

      {/* 生图状态行：跑过就常驻（done 后不消失，与 BEATS/TTS 一致） */}
      {status !== 'idle' && (
        <div className="proc-rows" style={{ marginBottom: 'var(--s-3)' }}>
          <ProcStatusRow
            row={{
              id: 'image',
              label: '批量图生图',
              status: status === 'done' ? 'done' : status === 'failed' ? 'failed' : 'running',
            }}
            runningText="生成中"
          />
        </div>
      )}

      <div className="rw-panel-header">
        <div
          className={`section-h${status === 'running' || status === 'queued' ? ' loading' : ''}`}
          style={{ margin: 0, flex: 1 }}
        >
          IMAGE 生成 · {items.length} 个场景{statusBadge}
          {hasPending && (
            <span className="dim-mono" style={{ marginLeft: 6, fontSize: 'var(--text-2xs)' }}>
              · 保存中…
            </span>
          )}
        </div>
        {renderActionBtn()}
      </div>

      {/* 批量生图较慢（每 scene 一次 gpt-image-2），running 时保留逐条进度明细 */}
      {(status === 'running' || status === 'queued') && nodeState.progress && (
        <div className="dim-mono">{nodeState.progress}</div>
      )}

      {items.length === 0 ? null : (
        <>
          <div className="image-grid">
            {items.map((it) => {
              const prompt = episode?.scenes?.[it.scene_id]?.prompt ?? it.prompt;
              return (
                <ImageCard
                  key={it.scene_id}
                  jobId={jobId}
                  item={it}
                  prompt={prompt}
                  busy={!!regenBusy[it.scene_id]}
                  disabled={actionBusy || advanceBusy || status !== 'done'}
                  onPromptChange={(v) => patchPrompt(it.scene_id, v)}
                  onRegen={() => doRegen(it.scene_id)}
                />
              );
            })}
          </div>
          <div className="image-footer">
            <button
              type="button"
              className="btn primary sm"
              title="用此组图 · 下一步（启动 PREVIEW）"
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
        title="重新执行 IMAGE？"
        message={<>会清空所有图片产物 + 所有下游节点的状态与产物，然后整体重新生图。</>}
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

function ImageCard({
  jobId,
  item,
  prompt,
  busy,
  disabled,
  onPromptChange,
  onRegen,
}: {
  jobId: string;
  item: ImageItem;
  prompt: string;
  busy: boolean;
  disabled: boolean;
  onPromptChange: (v: string) => void;
  onRegen: () => void;
}) {
  const hasImage = !!item.image_relpath;
  const sketches = item.sketches ?? [];
  const [skBusy, setSkBusy] = useState<Record<number, boolean>>({});

  async function doRegenSketch(n: number) {
    setSkBusy((m) => ({ ...m, [n]: true }));
    try {
      await api.regenImageSketch(jobId, item.scene_id, n);
    } catch (e) {
      alert(`简笔画重生失败: ${(e as Error).message}`);
    } finally {
      setSkBusy((m) => ({ ...m, [n]: false }));
    }
  }

  return (
    <article className="image-card">
      <header className="image-card-head">
        <span className="image-card-id mono">{item.scene_id}</span>
        {sketches.length > 0 && (
          <span className="dim-mono" style={{ fontSize: 'var(--text-2xs)' }}>
            {sketches.length} 简笔画
          </span>
        )}
      </header>
      <div className="image-card-preview">
        {hasImage ? (
          <img
            src={`/jobs/${jobId}/files/${item.image_relpath}`}
            alt={item.scene_id}
            loading="lazy"
            draggable={false}
          />
        ) : (
          <div className="image-card-placeholder">
            <ImageOff size={20} strokeWidth={1.5} />
            <span>未生成</span>
          </div>
        )}
        {busy && <div className="image-card-busy">生成中…</div>}
      </div>

      {sketches.length > 0 && (
        <div className="image-sketches">
          {sketches.map((sk) => (
            <div key={sk.index} className="image-sketch" title={sk.prompt}>
              {sk.image_relpath ? (
                <img
                  src={`/jobs/${jobId}/files/${sk.image_relpath}`}
                  alt={`sk${sk.index}`}
                  loading="lazy"
                  draggable={false}
                />
              ) : (
                <div className="image-sketch-ph">
                  <ImageOff size={13} strokeWidth={1.5} />
                </div>
              )}
              <button
                type="button"
                className="image-sketch-regen"
                title={skBusy[sk.index] ? '生成中…' : `按当前 prompt 重生简笔画 sk${sk.index}`}
                disabled={disabled || skBusy[sk.index]}
                onClick={() => doRegenSketch(sk.index)}
              >
                <RefreshCw size={10} strokeWidth={1.9} />
              </button>
              {skBusy[sk.index] && <div className="image-sketch-busy" />}
            </div>
          ))}
        </div>
      )}

      <textarea
        className="field image-card-prompt"
        value={prompt}
        onChange={(e) => onPromptChange(e.target.value)}
        placeholder="容器图提示词…"
        rows={3}
        spellCheck={false}
      />
      <div className="image-card-footer">
        <button
          type="button"
          className="btn sm icon-only ghost"
          title={busy ? '生成中…' : '按当前 prompt 重生容器图'}
          disabled={disabled || busy}
          onClick={onRegen}
        >
          <RefreshCw size={12} strokeWidth={1.7} />
        </button>
      </div>
    </article>
  );
}
