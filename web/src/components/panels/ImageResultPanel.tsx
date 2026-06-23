// 吴道子「画面资产」面板：1 张全片背景 + 按逐字幕 shot 分组的前景素材。
// 后端 image 节点会在 running 期间通过 /jobs SSE 增量 patch outputs：
//   - background：全片统一背景图候选
//   - items[].assets：跟随字幕入场的前景素材

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Database, Download, ImageOff, Play, Plus, RefreshCw, Search, Square, Tags } from 'lucide-react';

import { api } from '../../api/client';
import type {
  Episode,
  ImageAssetItem,
  ImageBackgroundItem,
  ImageItem,
  MaterialItem,
  MaterialScope,
  MaterialSearchResponse,
  NodeState,
  PipelineNodeDef,
} from '../../api/types';
import { ConfirmDialog } from '../ConfirmDialog';
import { Modal } from '../Modal';
import { useToast } from '../Toast';

interface Props {
  jobId: string;
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
  onAdvanced?: () => void;
}

const NEXT_NODE = 'tts';
const BACKGROUND_ID = 'background';
type ImageAssetTab = 'background' | 'foreground';

export function ImageResultPanel({ jobId, nodeDef, nodeState, onAdvanced }: Props) {
  const { showToast } = useToast();
  const items = useMemo<ImageItem[]>(
    () => (nodeState.outputs?.items as ImageItem[] | undefined) ?? [],
    [nodeState.outputs],
  );
  const background = nodeState.outputs?.background as ImageBackgroundItem | undefined;
  const status = nodeState.status;
  const foregroundCount = useMemo(
    () => items.reduce((sum, it) => sum + (it.assets?.length ?? 0), 0),
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
  const [assetBusy, setAssetBusy] = useState<Record<string, boolean>>({});
  const [assetTab, setAssetTab] = useState<ImageAssetTab>(() => (items.length > 0 ? 'foreground' : 'background'));
  const [assetPickerShot, setAssetPickerShot] = useState<ImageItem | null>(null);
  const assetTabTouched = useRef(false);

  const selectAssetTab = useCallback((tab: ImageAssetTab) => {
    assetTabTouched.current = true;
    setAssetTab(tab);
  }, []);

  useEffect(() => {
    api.getEpisode(jobId)
      .then((ep) => { setEpisode(ep); setEpErr(null); })
      .catch((e: Error) => setEpErr(e.message));
  }, [jobId, nodeState.finished_at]);

  useEffect(() => {
    assetTabTouched.current = false;
    setAssetTab(items.length > 0 ? 'foreground' : 'background');
  }, [jobId]);

  useEffect(() => {
    if (!assetTabTouched.current && items.length > 0) {
      setAssetTab('foreground');
    }
  }, [items.length]);

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

  async function doRegenAsset(shotId: string, n: number) {
    const key = `${shotId}:${n}`;
    setAssetBusy((m) => ({ ...m, [key]: true }));
    try {
      await api.regenImageSketch(jobId, shotId, n);
    } catch (e) {
      showToast('前景素材重生失败，请稍后再试');
      console.error('[ImageResultPanel] foreground regen failed', e);
    } finally {
      setAssetBusy((m) => ({ ...m, [key]: false }));
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
    hint = { tone: 'info', text: '会先生成一张全片背景，再并行生成每句字幕的前景素材。' };
  } else if (!background && items.length === 0 && status === 'done') {
    hint = { tone: 'info', text: '暂无画面资产；可重新生成，或检查视觉方案里的前景素材。' };
  }

  return (
    <div className="rw-panel-root">
      {hint && <div className={`panel-hint panel-hint-${hint.tone}`}>{hint.text}</div>}

      <div className="image-panel-toolbar">
        <nav className="asr-tabs image-panel-tabs" role="tablist" aria-label="画面资产分类">
          <button
            type="button"
            role="tab"
            aria-selected={assetTab === 'background'}
            className={`asr-tab${assetTab === 'background' ? ' active' : ''}`}
            onClick={() => selectAssetTab('background')}
          >
            背景图 · {background ? 1 : 0}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={assetTab === 'foreground'}
            className={`asr-tab${assetTab === 'foreground' ? ' active' : ''}`}
            onClick={() => selectAssetTab('foreground')}
          >
            前景素材 · {foregroundCount}
          </button>
        </nav>
        <div className="image-panel-actions">
          {hasPending && (
            <span className="dim-mono" style={{ fontSize: 'var(--text-2xs)' }}>保存中…</span>
          )}
          {renderActionBtn()}
        </div>
      </div>

      <div className="image-tab-panels">
        {assetTab === 'background' ? (
          <div className="image-tab-panel" role="tabpanel" aria-label="背景图">
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
          </div>
        ) : (
          <div className="image-tab-panel" role="tabpanel" aria-label="前景素材">
            {items.length > 0 ? (
              <div className="image-assets-list">
                {items.map((it) => (
                  <ForegroundShotCard
                    key={it.shot_id}
                    jobId={jobId}
                    item={it}
                    bust={nodeState.finished_at}
                    disabled={actionBusy || advanceBusy || status !== 'done'}
                    busyMap={assetBusy}
                    onRegenAsset={doRegenAsset}
                    onOpenAssetPicker={setAssetPickerShot}
                  />
                ))}
              </div>
            ) : (
              <div className="image-assets-empty">
                {status === 'running' || status === 'queued' ? '等待前景素材…' : '暂无前景素材'}
              </div>
            )}
          </div>
        )}
      </div>

      {items.length > 0 && (
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
      {assetPickerShot && (
        <AssetPickerDialog
          jobId={jobId}
          shot={assetPickerShot}
          onClose={() => setAssetPickerShot(null)}
        />
      )}
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

function ForegroundShotCard({
  jobId,
  item,
  bust,
  disabled,
  busyMap,
  onRegenAsset,
  onOpenAssetPicker,
}: {
  jobId: string;
  item: ImageItem;
  bust: number | null;
  disabled: boolean;
  busyMap: Record<string, boolean>;
  onRegenAsset: (shotId: string, n: number) => void;
  onOpenAssetPicker: (shot: ImageItem) => void;
}) {
  const assets = item.assets ?? [];
  return (
    <article className="image-asset-group">
      <header className="image-card-head">
        <div className="image-shot-title">
          <span className="image-card-id mono">{item.shot_id}</span>
          <span className="dim-mono" style={{ fontSize: 'var(--text-2xs)' }}>
            第 {item.beat_index} 句 · {assets.length} 前景素材
          </span>
        </div>
        <button
          type="button"
          className="btn sm icon-only primary image-add-material"
          title="从素材库添加前景素材"
          onClick={() => onOpenAssetPicker(item)}
        >
          <Plus size={12} strokeWidth={2} />
        </button>
      </header>
      {item.intent && <div className="dim-mono image-asset-intent">{item.intent}</div>}
      {assets.length > 0 ? (
        <div className="image-foreground-grid">
          {assets.map((asset) => (
            <ForegroundAsset
              key={asset.index}
              jobId={jobId}
              shotId={item.shot_id}
              asset={asset}
              bust={bust}
              busy={!!busyMap[`${item.shot_id}:${asset.index}`]}
              disabled={disabled}
              onRegen={() => onRegenAsset(item.shot_id, asset.index)}
            />
          ))}
        </div>
      ) : (
        <div className="dim-mono">暂无前景素材</div>
      )}
    </article>
  );
}

const MATERIAL_SCOPE_LABELS: { id: MaterialScope; label: string }[] = [
  { id: 'current_job', label: '当前任务' },
  { id: 'same_author', label: '同作者' },
  { id: 'same_domain', label: '同赛道' },
  { id: 'global', label: '全库' },
];

const MATERIAL_TAG_SUGGESTIONS = ['人物', '手机', '焦虑', '职场', '低头', '金钱', '成长', '关系'];

function AssetPickerDialog({ jobId, shot, onClose }: { jobId: string; shot: ImageItem; onClose: () => void }) {
  const { showToast } = useToast();
  const [scope, setScope] = useState<MaterialScope>('current_job');
  const [query, setQuery] = useState('');
  const [resp, setResp] = useState<MaterialSearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<MaterialItem | null>(null);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setLoading(true);
      setErr(null);
      api.listMaterials({ jobId, scope, q: query, limit: 60 })
        .then((next) => {
          if (cancelled) return;
          setResp(next);
          setSelected(null);
        })
        .catch((e: Error) => {
          if (cancelled) return;
          setErr(e.message);
          setResp(null);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [jobId, scope, query]);

  async function attachSelected() {
    if (!selected) return;
    try {
      await api.attachImageMaterial(jobId, { shot_id: shot.shot_id, material_id: selected.id });
      showToast('素材已加入当前句');
      onClose();
    } catch (e) {
      showToast('素材引用接口待接入');
      console.error('[ImageResultPanel] attach material placeholder', e);
    }
  }

  const scopes = resp?.scopes?.length ? resp.scopes : MATERIAL_SCOPE_LABELS;
  const items = resp?.items ?? [];
  const todo = resp?.todo || '素材索引尚未接入。';

  return (
    <Modal title={`添加前景素材 · ${shot.shot_id}`} onClose={onClose}>
      <div className="material-picker">
        <div className="material-picker-top">
          <div className="material-search">
            <Search size={14} strokeWidth={1.8} />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="标签 / 关键词"
              spellCheck={false}
            />
          </div>
          <div className="material-shot-context">
            <span className="mono">第 {shot.beat_index} 句</span>
            <span>{shot.intent || '未填写画面意图'}</span>
          </div>
        </div>

        <div className="material-scope-tabs" role="tablist" aria-label="素材来源">
          {scopes.map((it) => (
            <button
              key={it.id}
              type="button"
              className={`material-scope-tab${scope === it.id ? ' active' : ''}`}
              onClick={() => setScope(it.id)}
            >
              {it.label}
            </button>
          ))}
        </div>

        <div className="material-tags">
          <Tags size={13} strokeWidth={1.8} />
          {MATERIAL_TAG_SUGGESTIONS.map((tag) => (
            <button
              key={tag}
              type="button"
              className="material-tag"
              onClick={() => setQuery((prev) => (prev.trim() ? `${prev.trim()} ${tag}` : tag))}
            >
              {tag}
            </button>
          ))}
        </div>

        <div className="material-results">
          {loading && <div className="material-empty">加载素材索引…</div>}
          {!loading && err && <div className="panel-hint panel-hint-error">{err}</div>}
          {!loading && !err && items.length > 0 && (
            <div className="material-grid">
              {items.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`material-card${selected?.id === item.id ? ' active' : ''}`}
                  onClick={() => setSelected(item)}
                >
                  <img src={item.preview_url} alt="" loading="lazy" draggable={false} />
                  <span>{item.title || item.source_label || item.id}</span>
                </button>
              ))}
            </div>
          )}
          {!loading && !err && items.length === 0 && (
            <div className="material-empty">
              <Database size={22} strokeWidth={1.5} />
              <span>{todo}</span>
            </div>
          )}
        </div>

        <div className="material-picker-actions">
          <button type="button" className="btn sm ghost" onClick={onClose}>取消</button>
          <button type="button" className="btn sm primary" disabled={!selected} onClick={attachSelected}>
            引用素材
          </button>
        </div>
      </div>
    </Modal>
  );
}

function ForegroundAsset({
  jobId,
  shotId,
  asset,
  bust,
  busy,
  disabled,
  onRegen,
}: {
  jobId: string;
  shotId: string;
  asset: ImageAssetItem;
  bust: number | null;
  busy: boolean;
  disabled: boolean;
  onRegen: () => void;
}) {
  const bustQs = bust ? `?v=${bust}` : '';
  const fileUrl = (rel: string) => `/jobs/${jobId}/files/${rel}${bustQs}`;
  const running = busy || asset.status === 'queued' || asset.status === 'running';

  return (
    <div className={`image-foreground${asset.error ? ' failed' : ''}`} title={asset.prompt}>
      <div className="image-foreground-preview">
        {asset.image_relpath ? (
          <img src={fileUrl(asset.image_relpath)} alt={`${shotId}-${asset.asset_id ?? asset.index}`} loading="lazy" draggable={false} />
        ) : (
          <div className="image-sketch-ph">
            <ImageOff size={14} strokeWidth={1.5} />
          </div>
        )}
        {running && <div className="image-sketch-busy" />}
      </div>
      <div className="image-foreground-meta">
        <span className="mono">{asset.asset_id ?? `a${asset.index}`}</span>
        <span className="dim-mono">{asset.error ? '失败' : running ? '生成中' : asset.image_relpath ? '完成' : '等待'}</span>
      </div>
      <div className="image-foreground-prompt">{asset.prompt || '未填写 prompt'}</div>
      <div className="image-foreground-actions">
        {asset.image_relpath && (
          <a
            className="btn sm icon-only ghost"
            href={`/jobs/${jobId}/files/${asset.image_relpath}`}
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
      {asset.error && <div className="image-foreground-error">{asset.error}</div>}
    </div>
  );
}
