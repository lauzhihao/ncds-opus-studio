// rw 节点核心微调编辑器：完整 episode.json 表单。
// 5 个 tab：作品 / 字幕 / 字幕样式 / 画面 / 文字插槽。

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlignLeft,
  ArrowDown,
  ArrowUp,
  Brush,
  Check,
  Eye,
  EyeOff,
  Film,
  Image as ImageIcon,
  Languages,
  Plus,
  Save,
  Sparkles,
  SquarePen,
  Trash2,
  Type,
  Wand2,
  X,
} from 'lucide-react';

import { api } from '../../api/client';
import type { Beat, Episode, NodeState, Overlay, Scene } from '../../api/types';

interface Props {
  jobId: string;
  nodeState: NodeState;
}

const PALETTES = [
  { value: 'paper', label: '暖纸', swatch: '#b8362a' },
  { value: 'sage', label: '鼠尾草', swatch: '#2f6b50' },
  { value: 'dusty', label: '灰蓝', swatch: '#234c8a' },
  { value: 'bw', label: '黑白报刊', swatch: '#cc1f1f' },
];
const BAND_STYLES = [
  { value: 'paper', label: '纸面（透明）' },
  { value: 'dark', label: '深色带' },
];
const FONTS = ['Inter', 'Noto Sans SC', 'Noto Serif SC', 'XY Kaiti', 'ZS Fangsong'];
const SCENE_MOTIONS = [
  'fade', 'flip-h', 'glitch', 'ink-bleed', 'iris-in', 'mask-grid',
  'push-left', 'push-right', 'slide-down', 'slide-up',
  'wipe-circle', 'wipe-left', 'wipe-right', 'zoom-in', 'zoom-out',
];
const OVERLAY_MOTIONS = [
  'elastic-pop', 'fly-in', 'handwrite', 'ink-bleed', 'shake-attention', 'stamp', 'zoom-pop',
];
const CAP_ENTERS = [
  '', 'cap-enter-fly-up', 'cap-enter-fade-blur', 'cap-enter-zoom-soft',
  'cap-enter-mask-l-r', 'cap-enter-letter-spread', 'cap-enter-rise-glow',
];

type TabId = 'meta' | 'beats' | 'subtitles' | 'scenes' | 'overlays';
const TAB_META: { id: TabId; label: string; icon: typeof Type }[] = [
  { id: 'meta', label: '作品', icon: SquarePen },
  { id: 'beats', label: '字幕', icon: Languages },
  { id: 'subtitles', label: '字幕样式', icon: Type },
  { id: 'scenes', label: '画面', icon: Film },
  { id: 'overlays', label: '文字插槽', icon: AlignLeft },
];

export function EpisodeEditorPanel({ jobId, nodeState }: Props) {
  const [episode, setEpisode] = useState<Episode | null>(null);
  const [tab, setTab] = useState<TabId>('meta');
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [dirty, setDirty] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  // iframe reload trigger：保存后自增，让 iframe 重新 fetch episode
  const [previewKey, setPreviewKey] = useState(0);

  useEffect(() => {
    api.getEpisode(jobId)
      .then((ep) => { setEpisode(ep); setLoadErr(null); })
      .catch((e: Error) => setLoadErr(e.message));
  }, [jobId, nodeState.finished_at]);

  const patch = useCallback((mut: (draft: Episode) => void) => {
    setEpisode((prev) => {
      if (!prev) return prev;
      const next: Episode = JSON.parse(JSON.stringify(prev));
      mut(next);
      return next;
    });
    setDirty(true);
  }, []);

  async function save() {
    if (!episode) return;
    setSaving(true);
    try {
      await api.putEpisode(jobId, episode);
      setDirty(false);
      setSavedAt(Date.now());
      setPreviewKey((k) => k + 1);  // reload iframe
    } catch (e: unknown) {
      alert(`保存失败：${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  }

  function reloadPreview() {
    setPreviewKey((k) => k + 1);
  }

  if (loadErr) {
    return (
      <div className="empty-state" style={{ textAlign: 'left', padding: 20 }}>
        <div style={{ color: 'var(--ink-2)', marginBottom: 4 }}>无法加载 episode.json</div>
        <div className="dim-mono">{loadErr}</div>
        <div className="dim" style={{ marginTop: 8, fontSize: 'var(--text-sm)' }}>
          请先运行 rw 节点产出 episode 文件。
        </div>
      </div>
    );
  }
  if (!episode) {
    return <div className="dim" style={{ padding: 12 }}>加载 episode.json…</div>;
  }

  const sceneEntries = Object.entries(episode.scenes);
  const overlayCount = sceneEntries.reduce((n, [, s]) => n + (s.overlays?.length ?? 0), 0);
  const tabCount: Record<TabId, number | undefined> = {
    meta: undefined,
    beats: episode.beats.length,
    subtitles: undefined,
    scenes: sceneEntries.length,
    overlays: overlayCount,
  };

  return (
    <div>
      {/* —— iframe HTML 预览 —— */}
      <div className="preview-stage">
        <iframe
          key={previewKey}
          src={`/preview/${jobId}/011-reading-confidence.html`}
          title="011 预览"
          loading="lazy"
        />
        <div className="preview-toolbar">
          <span className="dim-mono">1920 × 1080 · HTML 实时预览</span>
          <div style={{ flex: 1 }} />
          <button className="btn sm ghost" onClick={reloadPreview} title="刷新预览">
            刷新
          </button>
        </div>
      </div>

      {/* —— 顶部 tabs + save —— */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <div className="tabs">
          {TAB_META.map((t) => {
            const Icon = t.icon;
            return (
              <button key={t.id} className={tab === t.id ? 'active' : ''} onClick={() => setTab(t.id)}>
                <Icon size={12} strokeWidth={1.7} />
                {t.label}
                {tabCount[t.id] != null && <span className="count">{tabCount[t.id]}</span>}
              </button>
            );
          })}
        </div>
        <div style={{ flex: 1 }} />
        {savedAt && !dirty && (
          <span className="dim-mono" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: 'var(--status-done)' }}>
            <Check size={11} strokeWidth={2} /> 已保存
          </span>
        )}
        <button className="btn primary sm" disabled={!dirty || saving} onClick={save}>
          <Save size={12} strokeWidth={1.8} />
          {saving ? '保存中…' : dirty ? '保存修改' : '已保存'}
        </button>
      </div>

      {tab === 'meta' && <MetaTab episode={episode} patch={patch} />}
      {tab === 'beats' && <BeatsTab episode={episode} patch={patch} />}
      {tab === 'subtitles' && <SubtitlesTab episode={episode} patch={patch} />}
      {tab === 'scenes' && <ScenesTab episode={episode} patch={patch} jobId={jobId} />}
      {tab === 'overlays' && <OverlaysTab episode={episode} patch={patch} />}
    </div>
  );
}

// ============================================================
// Meta
// ============================================================
function MetaTab({ episode, patch }: { episode: Episode; patch: (m: (d: Episode) => void) => void }) {
  return (
    <div>
      <div className="section-h">
        <SquarePen size={12} strokeWidth={1.7} /> 标题与品牌
      </div>
      <div className="form-row">
        <label>作品主标题</label>
        <input className="field" value={episode.meta.title}
          onChange={(e) => patch((d) => { d.meta.title = e.target.value; })} />
      </div>
      <div className="form-row">
        <label>顶部品牌名</label>
        <input className="field" value={episode.meta.brandTitle}
          onChange={(e) => patch((d) => { d.meta.brandTitle = e.target.value; })} />
      </div>
      <div className="form-row">
        <label>右上免责声明</label>
        <input className="field" value={episode.meta.disclaimer}
          onChange={(e) => patch((d) => { d.meta.disclaimer = e.target.value; })} />
      </div>

      <div className="section-h">
        <Brush size={12} strokeWidth={1.7} /> 视觉
      </div>
      <div className="form-row">
        <label>主题配色</label>
        <PaletteSwatches
          value={episode.visual.palette}
          onChange={(v) => patch((d) => { d.visual.palette = v; })}
        />
      </div>
      <div className="form-row">
        <label>字幕带样式</label>
        <Segmented
          value={episode.visual.bandStyle}
          options={BAND_STYLES}
          onChange={(v) => patch((d) => { d.visual.bandStyle = v; })}
        />
      </div>
      <div className="form-row">
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <input type="checkbox" checked={episode.visual.kenBurns}
            onChange={(e) => patch((d) => { d.visual.kenBurns = e.target.checked; })} />
          <span>启用 Ken Burns 镜头（图片缓慢推拉）</span>
        </label>
      </div>
      <SliderRow
        label="播放节奏（playback.rate）"
        value={episode.playback.rate}
        min={0.6}
        max={1.6}
        step={0.05}
        unit="x"
        onChange={(v) => patch((d) => { d.playback.rate = v; })}
      />
    </div>
  );
}

// ============================================================
// Beats
// ============================================================
function BeatsTab({ episode, patch }: { episode: Episode; patch: (m: (d: Episode) => void) => void }) {
  const sceneIds = useMemo(() => Object.keys(episode.scenes), [episode.scenes]);

  function setBeat(idx: number, field: keyof Beat, value: string | boolean) {
    patch((d) => {
      const b = d.beats[idx] as unknown as Record<string, unknown>;
      b[field as string] = value;
    });
  }
  function addBeat(after: number) {
    patch((d) => {
      const tpl: Beat = { zh: '', en: '', scene: sceneIds[0] ?? 'intro' };
      d.beats.splice(after + 1, 0, tpl);
    });
  }
  function delBeat(idx: number) { patch((d) => { d.beats.splice(idx, 1); }); }
  function move(idx: number, dir: -1 | 1) {
    patch((d) => {
      const t = idx + dir;
      if (t < 0 || t >= d.beats.length) return;
      [d.beats[idx], d.beats[t]] = [d.beats[t], d.beats[idx]];
    });
  }

  return (
    <div>
      <div className="dim" style={{ fontSize: 'var(--text-sm)', marginBottom: 12 }}>
        每条 beat = 一句字幕 + 所属 scene + 进场动效。
      </div>
      <table className="beats-table">
        <thead>
          <tr>
            <th className="num">#</th>
            <th>中文</th>
            <th>英文</th>
            <th style={{ width: 100 }}>Scene</th>
            <th style={{ width: 140 }}>进场动效</th>
            <th style={{ width: 110 }}>操作</th>
          </tr>
        </thead>
        <tbody>
          {episode.beats.map((b, i) => (
            <tr key={i}>
              <td className="num mono">{(i + 1).toString().padStart(2, '0')}</td>
              <td>
                <textarea className="field field-compact" rows={1} value={b.zh}
                  onChange={(e) => setBeat(i, 'zh', e.target.value)} />
              </td>
              <td>
                <textarea className="field field-compact" rows={1} value={b.en}
                  onChange={(e) => setBeat(i, 'en', e.target.value)} />
              </td>
              <td>
                <select className="field field-compact" value={b.scene}
                  onChange={(e) => setBeat(i, 'scene', e.target.value)}>
                  {sceneIds.map((id) => <option key={id} value={id}>{id}</option>)}
                </select>
              </td>
              <td>
                <select className="field field-compact" value={b.capEnter ?? ''}
                  onChange={(e) => setBeat(i, 'capEnter', e.target.value)}>
                  {CAP_ENTERS.map((m) => <option key={m} value={m}>{m || '默认随机'}</option>)}
                </select>
              </td>
              <td>
                <div style={{ display: 'inline-flex', gap: 2 }}>
                  <button className="btn sm icon-only ghost" title="上移" onClick={() => move(i, -1)}>
                    <ArrowUp size={12} strokeWidth={1.6} />
                  </button>
                  <button className="btn sm icon-only ghost" title="下移" onClick={() => move(i, 1)}>
                    <ArrowDown size={12} strokeWidth={1.6} />
                  </button>
                  <button className="btn sm icon-only ghost" title="插入" onClick={() => addBeat(i)}>
                    <Plus size={12} strokeWidth={1.8} />
                  </button>
                  <button className="btn sm icon-only ghost danger" title="删除" onClick={() => delBeat(i)}>
                    <Trash2 size={12} strokeWidth={1.6} />
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {episode.beats.length === 0 && (
        <button className="btn sm primary" style={{ marginTop: 12 }} onClick={() => addBeat(-1)}>
          <Plus size={12} strokeWidth={1.8} /> 添加第一条字幕
        </button>
      )}
    </div>
  );
}

// ============================================================
// Subtitles 全局样式
// ============================================================
function SubtitlesTab({ episode, patch }: { episode: Episode; patch: (m: (d: Episode) => void) => void }) {
  return (
    <div>
      <div className="section-h">
        <Type size={12} strokeWidth={1.7} /> 字号
      </div>
      <SliderRow label="中文字号" unit="px" min={48} max={110} step={2}
        value={episode.visual.capZhSize}
        onChange={(v) => patch((d) => { d.visual.capZhSize = v; })} />
      <SliderRow label="英文字号" unit="px" min={20} max={56} step={1}
        value={episode.visual.capEnSize}
        onChange={(v) => patch((d) => { d.visual.capEnSize = v; })} />

      <div className="section-h">
        <Type size={12} strokeWidth={1.7} /> 字体
      </div>
      <div className="form-row">
        <label>中文字体</label>
        <FontSelect
          value={episode.visual.capZhFont ?? 'Noto Sans SC'}
          onChange={(v) => patch((d) => { d.visual.capZhFont = v; })}
        />
      </div>
      <div className="form-row">
        <label>英文字体</label>
        <FontSelect
          value={episode.visual.capEnFont ?? 'Inter'}
          onChange={(v) => patch((d) => { d.visual.capEnFont = v; })}
        />
      </div>

      <div className="section-h">
        <Eye size={12} strokeWidth={1.7} /> 可见性
      </div>
      <div className="form-row">
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <input type="checkbox" checked={episode.visual.showSubtitleEn}
            onChange={(e) => patch((d) => { d.visual.showSubtitleEn = e.target.checked; })} />
          <span>
            <Languages size={12} strokeWidth={1.7} style={{ verticalAlign: '-2px', marginRight: 4 }} />
            显示英文字幕
          </span>
        </label>
      </div>
    </div>
  );
}

// ============================================================
// Scenes
// ============================================================
function ScenesTab({ episode, patch, jobId }: { episode: Episode; patch: (m: (d: Episode) => void) => void; jobId: string }) {
  function addScene() {
    const newId = window.prompt('新场景 id（英文小写，例：body2）');
    if (!newId) return;
    if (episode.scenes[newId]) { alert('id 已存在'); return; }
    patch((d) => {
      d.scenes[newId] = { prompt: '', label: '', motion: { enter: 'fade', duration: 700 }, overlays: [] };
    });
  }
  function delScene(id: string) {
    if (!window.confirm(`删除场景 ${id}？引用它的 beats 不会自动迁移。`)) return;
    patch((d) => { delete d.scenes[id]; });
  }
  function regenWst(id: string, sc: Scene) {
    alert(`即将调用 wst 重生（从 prompt）：${id}\n\n${sc.prompt.slice(0, 100)}…\n\nmock 模式不真生图。`);
  }
  function regenTst(id: string, sc: Scene) {
    alert(`即将调用 tst 微调（以原图为参考）：${id}\n\n${sc.prompt.slice(0, 100)}…\n\nmock 模式不真生图。`);
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
        <span className="dim" style={{ fontSize: 'var(--text-sm)' }}>共 {Object.keys(episode.scenes).length} 个场景</span>
        <div style={{ flex: 1 }} />
        <button className="btn sm" onClick={addScene}>
          <Plus size={12} strokeWidth={1.8} /> 新场景
        </button>
      </div>

      {Object.entries(episode.scenes).map(([id, sc]) => {
        const usedBy = episode.beats.filter((b) => b.scene === id).length;
        return (
          <div key={id} className="scene-card">
            <div className="scene-head">
              <span className="id mono">{id}</span>
              <span className="badge">{usedBy} 句</span>
              <span className="badge">{sc.overlays?.length ?? 0} 插槽</span>
              <div style={{ flex: 1 }} />
              <button className="btn sm" onClick={() => regenWst(id, sc)} title="按 prompt 全新生图">
                <Wand2 size={12} strokeWidth={1.7} /> wst
              </button>
              <button className="btn sm" onClick={() => regenTst(id, sc)} title="以原图为参考微调">
                <Sparkles size={12} strokeWidth={1.7} /> tst
              </button>
              <button className="btn sm icon-only ghost danger" onClick={() => delScene(id)} title="删除场景">
                <Trash2 size={12} strokeWidth={1.6} />
              </button>
            </div>
            <div className="scene-body">
              <div className="form-row">
                <label>
                  <ImageIcon size={11} strokeWidth={1.7} style={{ verticalAlign: '-1px', marginRight: 4 }} />
                  图片 Prompt
                </label>
                <textarea className="field" value={sc.prompt}
                  onChange={(e) => patch((d) => { d.scenes[id].prompt = e.target.value; })}
                  style={{ minHeight: 72 }} />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div className="form-row">
                  <label>进场动效</label>
                  <select className="field" value={sc.motion?.enter ?? 'fade'}
                    onChange={(e) => patch((d) => {
                      d.scenes[id].motion = { ...(d.scenes[id].motion ?? { duration: 700 }), enter: e.target.value };
                    })}>
                    {SCENE_MOTIONS.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </div>
                <div className="form-row">
                  <label>动效时长（ms）</label>
                  <input className="field" type="number" value={sc.motion?.duration ?? 700}
                    onChange={(e) => patch((d) => {
                      d.scenes[id].motion = { enter: d.scenes[id].motion?.enter ?? 'fade', duration: Number(e.target.value) };
                    })} />
                </div>
              </div>
              <div className="dim-mono" style={{ marginTop: 6 }}>
                产物落 video-jobs/{jobId.slice(0, 6)}…/03_wst/
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ============================================================
// Overlays
// ============================================================
function OverlaysTab({ episode, patch }: { episode: Episode; patch: (m: (d: Episode) => void) => void }) {
  const all: Array<[string, number, Overlay]> = [];
  for (const [sid, sc] of Object.entries(episode.scenes)) {
    (sc.overlays ?? []).forEach((o, i) => all.push([sid, i, o]));
  }

  function setOv(sid: string, idx: number, mut: (o: Overlay) => void) {
    patch((d) => { const ov = d.scenes[sid].overlays?.[idx]; if (ov) mut(ov); });
  }
  function addOv(sid: string) {
    patch((d) => {
      const sc = d.scenes[sid];
      if (!sc.overlays) sc.overlays = [];
      sc.overlays.push({
        text: '新插槽',
        pos: { x: 50, y: 50 },
        style: { font: 'XY Kaiti', size: 64, weight: 400, color: 'var(--accent)' },
        motion: { enter: 'ink-bleed', duration: 900, delay: 200 },
      });
    });
  }
  function delOv(sid: string, idx: number) {
    patch((d) => { d.scenes[sid].overlays?.splice(idx, 1); });
  }

  const sceneIds = Object.keys(episode.scenes);

  if (all.length === 0) {
    return (
      <div>
        <div className="empty-state" style={{ textAlign: 'left', padding: 16 }}>
          <div style={{ color: 'var(--ink-2)' }}>当前没有文字插槽。</div>
          <div className="dim" style={{ fontSize: 'var(--text-sm)', marginTop: 4 }}>
            文字插槽叠加在画面上，可独立调字体、颜色、位置和动效。
          </div>
        </div>
        <div style={{ marginTop: 12, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {sceneIds.map((sid) => (
            <button key={sid} className="btn sm" onClick={() => addOv(sid)}>
              <Plus size={11} strokeWidth={1.8} /> 在 <span className="mono" style={{ marginLeft: 3 }}>{sid}</span> 添加
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="dim" style={{ fontSize: 'var(--text-sm)', marginBottom: 14 }}>
        共 {all.length} 个插槽。x/y 是百分比（0-100），未来支持在预览里拖动。
      </div>

      {all.map(([sid, idx, o]) => (
        <div key={`${sid}-${idx}`} className="slot-card">
          <div className="slot-head">
            <span className="tag mono">{sid}</span>
            <span className="dim-mono">#{idx}</span>
            <div style={{ flex: 1 }} />
            <button className="btn sm icon-only ghost danger" onClick={() => delOv(sid, idx)} title="删除">
              <X size={12} strokeWidth={1.6} />
            </button>
          </div>
          <div className="form-row">
            <label>文案</label>
            <input className="field" value={o.text}
              onChange={(e) => setOv(sid, idx, (ov) => { ov.text = e.target.value; })} />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
            <NumField label="X (%)" value={o.pos.x}
              onChange={(v) => setOv(sid, idx, (ov) => { ov.pos.x = v; })} />
            <NumField label="Y (%)" value={o.pos.y}
              onChange={(v) => setOv(sid, idx, (ov) => { ov.pos.y = v; })} />
            <NumField label="旋转 (°)" value={o.style.rotation ?? 0}
              onChange={(v) => setOv(sid, idx, (ov) => { ov.style.rotation = v; })} />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
            <div className="form-row">
              <label>字体</label>
              <FontSelect value={o.style.font ?? 'XY Kaiti'}
                onChange={(v) => setOv(sid, idx, (ov) => { ov.style.font = v; })} />
            </div>
            <NumField label="字号 (px)" value={o.style.size ?? 60}
              onChange={(v) => setOv(sid, idx, (ov) => { ov.style.size = v; })} />
            <div className="form-row">
              <label>颜色</label>
              <input className="field" value={o.style.color ?? 'var(--accent)'}
                onChange={(e) => setOv(sid, idx, (ov) => { ov.style.color = e.target.value; })} />
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
            <div className="form-row">
              <label>动效</label>
              <select className="field" value={o.motion?.enter ?? 'fly-in'}
                onChange={(e) => setOv(sid, idx, (ov) => {
                  ov.motion = { ...(ov.motion ?? { duration: 900 }), enter: e.target.value };
                })}>
                {OVERLAY_MOTIONS.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
            <NumField label="动效时长 (ms)" value={o.motion?.duration ?? 900}
              onChange={(v) => setOv(sid, idx, (ov) => {
                ov.motion = { enter: ov.motion?.enter ?? 'fly-in', duration: v };
              })} />
            <NumField label="延迟 (ms)" value={o.motion?.delay ?? 0}
              onChange={(v) => setOv(sid, idx, (ov) => {
                ov.motion = { ...(ov.motion ?? { enter: 'fly-in', duration: 900 }), delay: v };
              })} />
          </div>
        </div>
      ))}

      <div style={{ marginTop: 14, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {sceneIds.map((sid) => (
          <button key={sid} className="btn sm" onClick={() => addOv(sid)}>
            <Plus size={11} strokeWidth={1.8} /> 在 <span className="mono" style={{ marginLeft: 3 }}>{sid}</span> 添加
          </button>
        ))}
      </div>
    </div>
  );
}

// ============================================================
// 小工具组件
// ============================================================

function SliderRow({
  label, value, min, max, step, unit, onChange,
}: {
  label: string; value: number; min: number; max: number; step: number;
  unit: string; onChange: (v: number) => void;
}) {
  return (
    <div className="form-row">
      <label style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span>{label}</span>
        <span className="mono dim">{value}{unit}</span>
      </label>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))} />
    </div>
  );
}

function NumField({
  label, value, onChange,
}: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <div className="form-row">
      <label>{label}</label>
      <input className="field" type="number" value={value}
        onChange={(e) => onChange(Number(e.target.value))} />
    </div>
  );
}

function FontSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <select className="field" value={value} onChange={(e) => onChange(e.target.value)}>
      {FONTS.map((f) => (
        <option key={f} value={f} style={{ fontFamily: f }}>{f}</option>
      ))}
    </select>
  );
}

function PaletteSwatches({
  value, onChange,
}: { value: string; onChange: (v: string) => void }) {
  return (
    <div style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap' }}>
      {PALETTES.map((p) => {
        const active = p.value === value;
        return (
          <button
            key={p.value}
            className={`btn sm ${active ? 'primary' : ''}`}
            onClick={() => onChange(p.value)}
            type="button"
            style={{ paddingLeft: 8 }}
          >
            <span style={{
              width: 10, height: 10, borderRadius: '50%', background: p.swatch,
              border: '1px solid rgba(0,0,0,0.12)',
            }} />
            {p.label}
          </button>
        );
      })}
    </div>
  );
}

function Segmented({
  value, options, onChange,
}: { value: string; options: { value: string; label: string }[]; onChange: (v: string) => void }) {
  return (
    <div className="tabs">
      {options.map((o) => (
        <button key={o.value} className={value === o.value ? 'active' : ''} onClick={() => onChange(o.value)} type="button">
          {o.label}
        </button>
      ))}
    </div>
  );
}

// 防止 EyeOff 树摇被丢；预留给将来"显示英文字幕"按钮用
void EyeOff;
