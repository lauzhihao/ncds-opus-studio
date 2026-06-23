// 吴道子「画面资产」面板：1 张全片背景 + 按 scene 分组的前景素材。
// 后端 image 节点会在 running 期间通过 /jobs SSE 增量 patch outputs：
//   - background：全片统一背景图候选
//   - items[].sketches：跟随字幕入场的前景素材

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Download, ImageOff, Play, RefreshCw, Square } from 'lucide-react';

import { api } from '../../api/client';
import type {
  Episode,
  ImageBackgroundItem,
  ImageItem,
  ImageSketchItem,
  NodeState,
  PipelineNodeDef,
} from '../../api/types';
import { friendlyProgressText } from '../../utils/progress';
import { ConfirmDialog } from '../ConfirmDialog';
import { useToast } from '../Toast';
import { ProcStatusRow } from './ProcStatusRow';

interface Props {
  jobId: string;
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
  onAdvanced?: () => void;
}

const NEXT_NODE = 'tts';
const BACKGROUND_ID = 'background';

export function ImageResultPanel({ jobId, nodeDef, nodeState, onAdvanced }: Props) {
  const { showToast } = useToast();
  const items = useMemo<ImageItem[]>(
    () => (nodeState.outputs?.items as ImageItem[] | undefined) ?? [],
    [nodeState.outputs],
  );
  const background = nodeState.outputs?.background as ImageBackgroundItem | undefined;
  const status = nodeState.status;
  const foregroundCount = useMemo(
    () => items.reduce((sum, it) => sum + (it.sketches?.length ?? 0), 0),
    [items],
  );

  const [episode, setEpisode] = useState<Episode | null>(null);
  const [epErr, setEpErr] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [advanceBusy, setAdvanceBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [pendingRerun, setPendingRerun] = useState(false);
  const [backgroundBusy, setBackgroundBusy] = useState(false);
  const [backgroundVariantBusy, setBackgroundVariantBusy] = useState<string | null>(null);
  const [skBusy, setSkBusy] = useState<Record<string, boolean>>({});

  useEffect(() => {
    api.getEpisode(jobId)
      .then((ep) => { setEpisode(ep); setEpErr(null); })
      .catch((e: Error) => setEpErr(e.message));
  }, [jobId, nodeState.finished_at]);

  const debounceTimer = useRef<number | null>(null);
  const pendingEpRef = useRef<Episode | null>(null);

  const flushEpisode = useCallback(async (): Promise<void> => {
    if (debounceTimer.current != null) {
      window.clearTimeout(debounceTimer.current);
      debounceTimer.current = null;
    }
    const ep = pendingEpRef.current;
    if (!ep) return;
    pendingEpRef.current = null;
    setSaving(true);
    try {
      await api.putEpisode(jobId, ep);
    } catch (e) {
      pendingEpRef.current = ep;
      console.error('[image] save episode failed', e);
    } finally {
      setSaving(false);
    }
  }, [jobId]);

  const patchBackgroundPrompt = useCallback(
    (prompt: string) => {
      setEpisode((prev) => {
        if (!prev) return prev;
        const next: Episode = JSON.parse(JSON.stringify(prev));
        const image = (next.image ?? {}) as Record<string, unknown>;
        const bg = typeof image.background === 'object' && image.background
          ? { ...(image.background as Record<string, unknown>) }
          : {};
        bg.prompt = prompt;
        bg.imageFile = 'pictures/background.webp';
        image.background = bg;
        next.image = image;
        pendingEpRef.current = next;
        return next;
      });
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
      showToast('启动失败，请稍后再试');
      console.error('[ImageResultPanel] 启动失败', e);
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
      console.error('[ImageResultPanel] 停止失败', e);
    } finally {
      setActionBusy(false);
    }
  }

  async function doRegenBackground() {
    setBackgroundBusy(true);
    try {
      await flushEpisode();
      await api.regenImageScene(jobId, BACKGROUND_ID);
    } catch (e) {
      showToast('背景图重生失败，请稍后再试');
      console.error('[ImageResultPanel] background regen failed', e);
    } finally {
      setBackgroundBusy(false);
    }
  }

  async function doSelectBackgroundVariant(rel: string) {
    if (backgroundVariantBusy) return;
    setBackgroundVariantBusy(rel);
    try {
      await api.selectImageVariant(jobId, BACKGROUND_ID, rel);
    } catch (e) {
      showToast('背景候选切换失败，请稍后再试');
      console.error('[ImageResultPanel] background variant select failed', e);
    } finally {
      setBackgroundVariantBusy(null);
    }
  }

  async function doRegenSketch(sceneId: string, n: number) {
    const key = `${sceneId}:${n}`;
    setSkBusy((m) => ({ ...m, [key]: true }));
    try {
      await api.regenImageSketch(jobId, sceneId, n);
    } catch (e) {
      showToast('前景素材重生失败，请稍后再试');
      console.error('[ImageResultPanel] foreground regen failed', e);
    } finally {
      setSkBusy((m) => ({ ...m, [key]: false }));
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
      console.error('[ImageResultPanel] advance to boya failed', e);
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
        <Play size={12} strokeWidth={2} /> 生成画面资产
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
  const hasPending = pendingEpRef.current != null || saving;
  const backgroundPrompt = String(
    ((episode?.image as Record<string, unknown> | undefined)?.background as { prompt?: unknown } | undefined)?.prompt
      ?? background?.prompt
      ?? '',
  );

  let hint: { tone: 'info' | 'error'; text: string } | null = null;
  if (epErr) {
    hint = { tone: 'error', text: `episode 加载失败：${epErr}` };
  } else if (status === 'idle') {
    hint = { tone: 'info', text: '会先生成一张全片背景，再逐个生成跟随字幕入场的前景素材。' };
  } else if (!background && items.length === 0 && status === 'done') {
    hint = { tone: 'info', text: '暂无画面资产；可重新生成，或检查视觉方案里的前景素材。' };
  }

  return (
    <div className="rw-panel-root">
      {hint && <div className={`panel-hint panel-hint-${hint.tone}`}>{hint.text}</div>}

      {status !== 'idle' && (
        <div className="proc-rows" style={{ marginBottom: 'var(--s-3)' }}>
          <ProcStatusRow
            row={{
              id: 'image',
              label: '画面资产生成',
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
          画面资产 · {background ? 1 : 0} 张背景 · {foregroundCount} 个前景素材{statusBadge}
          {hasPending && (
            <span className="dim-mono" style={{ marginLeft: 6, fontSize: 'var(--text-2xs)' }}>
              · 保存中…
            </span>
          )}
        </div>
        {renderActionBtn()}
      </div>

      {(status === 'running' || status === 'queued') && nodeState.progress && (
        <div className="dim-mono">{friendlyProgressText('image', nodeState.progress)}</div>
      )}

      {(background || status === 'running' || status === 'queued') && (
        <BackgroundCard
          jobId={jobId}
          background={background}
          prompt={backgroundPrompt}
          bust={nodeState.finished_at}
          disabled={actionBusy || advanceBusy || status !== 'done'}
          busy={backgroundBusy}
          variantBusy={backgroundVariantBusy}
          onPromptChange={patchBackgroundPrompt}
          onRegen={doRegenBackground}
          onSelectVariant={doSelectBackgroundVariant}
        />
      )}

      {items.length > 0 && (
        <>
          <div className="image-assets-list">
            {items.map((it) => (
              <ForegroundSceneCard
                key={it.scene_id}
                jobId={jobId}
                item={it}
                bust={nodeState.finished_at}
                disabled={actionBusy || advanceBusy || status !== 'done'}
                busyMap={skBusy}
                onRegenSketch={doRegenSketch}
              />
            ))}
          </div>
          <div className="image-footer">
            <button
              type="button"
              className="btn primary sm"
              title="确认画面资产 · 交给伯牙配音"
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
        title="重新生成画面资产？"
        message={<>会清空背景图、前景素材以及下游声音和成片状态，然后整体重新生成画面资产。</>}
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

function BackgroundCard({
  jobId,
  background,
  prompt,
  bust,
  disabled,
  busy,
  variantBusy,
  onPromptChange,
  onRegen,
  onSelectVariant,
}: {
  jobId: string;
  background?: ImageBackgroundItem;
  prompt: string;
  bust: number | null;
  disabled: boolean;
  busy: boolean;
  variantBusy: string | null;
  onPromptChange: (v: string) => void;
  onRegen: () => void;
  onSelectVariant: (rel: string) => void;
}) {
  const bustQs = bust ? `?v=${bust}` : '';
  const fileUrl = (rel: string) => `/jobs/${jobId}/files/${rel}${bustQs}`;
  const variants = (background?.variants ?? []).filter((v) => !!v.image_relpath);
  const selected = background?.selected_variant_relpath
    || variants.find((v) => v.selected)?.image_relpath
    || variants[0]?.image_relpath
    || background?.image_relpath;
  const running = busy || background?.status === 'queued' || background?.status === 'running';

  return (
    <section className="image-bg-card">
      <div className="image-bg-head">
        <div>
          <div className="section-h" style={{ margin: 0 }}>背景图</div>
          <div className="dim-mono">全片共用，像 PPT 背景一样承载所有字幕和前景素材</div>
        </div>
        {background?.image_relpath && (
          <a
            className="btn sm icon-only ghost"
            href={`/jobs/${jobId}/files/${background.image_relpath}`}
            download
            title="下载背景图"
          >
            <Download size={12} strokeWidth={1.7} />
          </a>
        )}
      </div>

      <div className="image-bg-preview">
        {background?.image_relpath ? (
          <img src={fileUrl(background.image_relpath)} alt="背景图" loading="lazy" draggable={false} />
        ) : (
          <div className="image-card-placeholder">
            <ImageOff size={20} strokeWidth={1.5} />
            <span>{running ? '生成中…' : '未生成'}</span>
          </div>
        )}
        {running && <div className="image-card-busy">生成中…</div>}
      </div>

      {variants.length > 1 && (
        <div className="image-variants" aria-label="背景候选图">
          {variants.map((variant) => {
            const active = variant.image_relpath === selected;
            const picking = variantBusy === variant.image_relpath;
            return (
              <button
                key={variant.image_relpath}
                type="button"
                className={`image-variant${active ? ' active' : ''}`}
                title={active ? `候选 ${variant.index} · 当前背景` : `设候选 ${variant.index} 为背景`}
                disabled={disabled || running || !!variantBusy}
                onClick={() => onSelectVariant(variant.image_relpath)}
              >
                <img src={fileUrl(variant.image_relpath)} alt="" loading="lazy" draggable={false} />
                <span className="image-variant-index">{picking ? '…' : variant.index}</span>
              </button>
            );
          })}
        </div>
      )}

      <textarea
        className="field image-card-prompt"
        value={prompt}
        onChange={(e) => onPromptChange(e.target.value)}
        placeholder="背景图提示词…"
        rows={3}
        spellCheck={false}
      />
      <div className="image-card-footer">
        <button
          type="button"
          className="btn sm ghost"
          disabled={disabled || running}
          onClick={onRegen}
        >
          <RefreshCw size={12} strokeWidth={1.7} /> {running ? '生成中…' : '重生背景'}
        </button>
      </div>
      {background?.error && <div className="panel-hint panel-hint-error">{background.error}</div>}
    </section>
  );
}

function ForegroundSceneCard({
  jobId,
  item,
  bust,
  disabled,
  busyMap,
  onRegenSketch,
}: {
  jobId: string;
  item: ImageItem;
  bust: number | null;
  disabled: boolean;
  busyMap: Record<string, boolean>;
  onRegenSketch: (sceneId: string, n: number) => void;
}) {
  const sketches = item.sketches ?? [];
  return (
    <article className="image-asset-group">
      <header className="image-card-head">
        <span className="image-card-id mono">{item.scene_id}</span>
        <span className="dim-mono" style={{ fontSize: 'var(--text-2xs)' }}>
          {sketches.length} 前景素材
        </span>
      </header>
      {item.prompt && <div className="dim-mono image-asset-intent">{item.prompt}</div>}
      {sketches.length > 0 ? (
        <div className="image-foreground-grid">
          {sketches.map((sk) => (
            <ForegroundAsset
              key={sk.index}
              jobId={jobId}
              sceneId={item.scene_id}
              sketch={sk}
              bust={bust}
              busy={!!busyMap[`${item.scene_id}:${sk.index}`]}
              disabled={disabled}
              onRegen={() => onRegenSketch(item.scene_id, sk.index)}
            />
          ))}
        </div>
      ) : (
        <div className="dim-mono">暂无前景素材</div>
      )}
    </article>
  );
}

function ForegroundAsset({
  jobId,
  sceneId,
  sketch,
  bust,
  busy,
  disabled,
  onRegen,
}: {
  jobId: string;
  sceneId: string;
  sketch: ImageSketchItem;
  bust: number | null;
  busy: boolean;
  disabled: boolean;
  onRegen: () => void;
}) {
  const bustQs = bust ? `?v=${bust}` : '';
  const fileUrl = (rel: string) => `/jobs/${jobId}/files/${rel}${bustQs}`;
  const running = busy || sketch.status === 'queued' || sketch.status === 'running';

  return (
    <div className={`image-foreground${sketch.error ? ' failed' : ''}`} title={sketch.prompt}>
      <div className="image-foreground-preview">
        {sketch.image_relpath ? (
          <img src={fileUrl(sketch.image_relpath)} alt={`${sceneId}-sk${sketch.index}`} loading="lazy" draggable={false} />
        ) : (
          <div className="image-sketch-ph">
            <ImageOff size={14} strokeWidth={1.5} />
          </div>
        )}
        {running && <div className="image-sketch-busy" />}
      </div>
      <div className="image-foreground-meta">
        <span className="mono">sk{sketch.index}</span>
        <span className="dim-mono">{sketch.error ? '失败' : running ? '生成中' : sketch.image_relpath ? '完成' : '等待'}</span>
      </div>
      <div className="image-foreground-prompt">{sketch.prompt || '未填写 prompt'}</div>
      <div className="image-foreground-actions">
        {sketch.image_relpath && (
          <a
            className="btn sm icon-only ghost"
            href={`/jobs/${jobId}/files/${sketch.image_relpath}`}
            download
            title="下载前景素材"
          >
            <Download size={11} strokeWidth={1.7} />
          </a>
        )}
        <button
          type="button"
          className="btn sm icon-only ghost"
          title={running ? '生成中…' : '重生前景素材'}
          disabled={disabled || running}
          onClick={onRegen}
        >
          <RefreshCw size={11} strokeWidth={1.9} />
        </button>
      </div>
      {sketch.error && <div className="image-foreground-error">{sketch.error}</div>}
    </div>
  );
}
