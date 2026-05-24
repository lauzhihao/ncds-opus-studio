// rw 节点核心微调编辑器：完整的 episode.json 表单。
// 5 个区块：meta / beats / 字幕全局 / scenes / overlays。
// 数据流：mount 时 GET /jobs/{id}/episode；编辑只改本地状态；点保存 PUT 回后端。

import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../../api/client';
import type { Beat, Episode, NodeState, Overlay, Scene } from '../../api/types';

interface Props {
  jobId: string;
  nodeState: NodeState;
}

const PALETTES = [
  { value: 'paper', label: '暖纸' },
  { value: 'sage', label: '鼠尾草' },
  { value: 'dusty', label: '灰蓝' },
  { value: 'bw', label: '黑白报刊' },
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

export function RwEditorPanel({ jobId, nodeState }: Props) {
  const [episode, setEpisode] = useState<Episode | null>(null);
  const [tab, setTab] = useState<'meta' | 'beats' | 'subtitles' | 'scenes' | 'overlays'>('meta');
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  useEffect(() => {
    api.getEpisode(jobId)
      .then((ep) => { setEpisode(ep); setLoadErr(null); })
      .catch((e: Error) => setLoadErr(e.message));
  }, [jobId, nodeState.finished_at]); // rw 跑完后重新拉

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
    } catch (e: unknown) {
      alert(`保存失败：${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  }

  if (loadErr) {
    return (
      <div style={{ color: 'var(--ink-soft)' }}>
        <p>无法加载 episode.json：{loadErr}</p>
        <p>需要先运行 rw 节点产出 episode 文件。</p>
      </div>
    );
  }
  if (!episode) return <div>加载 episode.json…</div>;

  const sceneEntries = Object.entries(episode.scenes);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12 }}>
        {(['meta', 'beats', 'subtitles', 'scenes', 'overlays'] as const).map((t) => (
          <button
            key={t}
            className={tab === t ? 'btn primary sm' : 'btn sm'}
            onClick={() => setTab(t)}
          >
            {TAB_LABEL[t]}
            {t === 'beats' ? ` (${episode.beats.length})` : null}
            {t === 'scenes' ? ` (${sceneEntries.length})` : null}
            {t === 'overlays'
              ? ` (${sceneEntries.reduce((n, [, s]) => n + (s.overlays?.length ?? 0), 0)})`
              : null}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <button className="btn primary sm" disabled={!dirty || saving} onClick={save}>
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

const TAB_LABEL: Record<'meta' | 'beats' | 'subtitles' | 'scenes' | 'overlays', string> = {
  meta: '作品信息',
  beats: '字幕列表',
  subtitles: '字幕样式',
  scenes: '画面',
  overlays: '文字插槽',
};

// ============================================================
// Meta
// ============================================================
function MetaTab({ episode, patch }: { episode: Episode; patch: (m: (d: Episode) => void) => void }) {
  return (
    <div>
      <div className="section-h">标题与品牌</div>
      <div className="form-row">
        <label>作品标题（主标题）</label>
        <input value={episode.meta.title} onChange={(e) => patch((d) => { d.meta.title = e.target.value; })} />
      </div>
      <div className="form-row">
        <label>顶部品牌名</label>
        <input value={episode.meta.brandTitle} onChange={(e) => patch((d) => { d.meta.brandTitle = e.target.value; })} />
      </div>
      <div className="form-row">
        <label>右上免责声明</label>
        <input value={episode.meta.disclaimer} onChange={(e) => patch((d) => { d.meta.disclaimer = e.target.value; })} />
      </div>

      <div className="section-h">视觉</div>
      <div className="form-row">
        <label>主题配色</label>
        <select value={episode.visual.palette} onChange={(e) => patch((d) => { d.visual.palette = e.target.value; })}>
          {PALETTES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
        </select>
      </div>
      <div className="form-row">
        <label>字幕带样式</label>
        <select value={episode.visual.bandStyle} onChange={(e) => patch((d) => { d.visual.bandStyle = e.target.value; })}>
          {BAND_STYLES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
        </select>
      </div>
      <div className="form-row">
        <label><input type="checkbox" checked={episode.visual.kenBurns}
          onChange={(e) => patch((d) => { d.visual.kenBurns = e.target.checked; })} /> 启用 Ken Burns 镜头</label>
      </div>
      <div className="form-row">
        <label>节奏（playback.rate）</label>
        <input type="number" min={0.6} max={1.6} step={0.05} value={episode.playback.rate}
          onChange={(e) => patch((d) => { d.playback.rate = Number(e.target.value); })} />
      </div>
    </div>
  );
}

// ============================================================
// Beats（字幕表格）
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
  function delBeat(idx: number) {
    patch((d) => { d.beats.splice(idx, 1); });
  }
  function move(idx: number, dir: -1 | 1) {
    patch((d) => {
      const t = idx + dir;
      if (t < 0 || t >= d.beats.length) return;
      [d.beats[idx], d.beats[t]] = [d.beats[t], d.beats[idx]];
    });
  }

  return (
    <div>
      <div style={{ color: 'var(--ink-soft)', fontSize: 12, marginBottom: 8 }}>
        每条 beat = 一句字幕 + 对应 scene + 进场动效。共 {episode.beats.length} 条。
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border)', textAlign: 'left', color: 'var(--ink-soft)' }}>
            <th style={{ width: 30, padding: '6px 4px' }}>#</th>
            <th style={{ padding: '6px 4px' }}>中文</th>
            <th style={{ padding: '6px 4px' }}>英文</th>
            <th style={{ width: 100, padding: '6px 4px' }}>Scene</th>
            <th style={{ width: 130, padding: '6px 4px' }}>进场动效</th>
            <th style={{ width: 90, padding: '6px 4px' }}>操作</th>
          </tr>
        </thead>
        <tbody>
          {episode.beats.map((b, i) => (
            <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
              <td style={{ padding: '4px', color: 'var(--ink-soft)' }}>{i + 1}</td>
              <td style={{ padding: '4px' }}>
                <textarea rows={1} value={b.zh}
                  onChange={(e) => setBeat(i, 'zh', e.target.value)}
                  style={{ width: '100%', minHeight: 24, padding: '2px 4px', border: '1px solid var(--border)', borderRadius: 4 }} />
              </td>
              <td style={{ padding: '4px' }}>
                <textarea rows={1} value={b.en}
                  onChange={(e) => setBeat(i, 'en', e.target.value)}
                  style={{ width: '100%', minHeight: 24, padding: '2px 4px', border: '1px solid var(--border)', borderRadius: 4 }} />
              </td>
              <td style={{ padding: '4px' }}>
                <select value={b.scene} onChange={(e) => setBeat(i, 'scene', e.target.value)} style={{ width: '100%' }}>
                  {sceneIds.map((id) => <option key={id} value={id}>{id}</option>)}
                </select>
              </td>
              <td style={{ padding: '4px' }}>
                <select value={b.capEnter ?? ''} onChange={(e) => setBeat(i, 'capEnter', e.target.value)} style={{ width: '100%' }}>
                  {CAP_ENTERS.map((m) => <option key={m} value={m}>{m || '(默认随机)'}</option>)}
                </select>
              </td>
              <td style={{ padding: '4px', whiteSpace: 'nowrap' }}>
                <button className="btn sm ghost" title="上移" onClick={() => move(i, -1)}>↑</button>
                <button className="btn sm ghost" title="下移" onClick={() => move(i, 1)}>↓</button>
                <button className="btn sm ghost" title="插入" onClick={() => addBeat(i)}>+</button>
                <button className="btn sm ghost danger" title="删除" onClick={() => delBeat(i)}>×</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {episode.beats.length === 0 && (
        <button className="btn sm primary" style={{ marginTop: 12 }} onClick={() => addBeat(-1)}>+ 添加第一条字幕</button>
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
      <div className="section-h">字号</div>
      <div className="form-row">
        <label>中文字号（px）{episode.visual.capZhSize}</label>
        <input type="range" min={48} max={110} step={2} value={episode.visual.capZhSize}
          onChange={(e) => patch((d) => { d.visual.capZhSize = Number(e.target.value); })} />
      </div>
      <div className="form-row">
        <label>英文字号（px）{episode.visual.capEnSize}</label>
        <input type="range" min={20} max={56} step={1} value={episode.visual.capEnSize}
          onChange={(e) => patch((d) => { d.visual.capEnSize = Number(e.target.value); })} />
      </div>

      <div className="section-h">字体</div>
      <div className="form-row">
        <label>中文字体</label>
        <select value={episode.visual.capZhFont ?? 'Noto Sans SC'}
          onChange={(e) => patch((d) => { d.visual.capZhFont = e.target.value; })}>
          {FONTS.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
      </div>
      <div className="form-row">
        <label>英文字体</label>
        <select value={episode.visual.capEnFont ?? 'Inter'}
          onChange={(e) => patch((d) => { d.visual.capEnFont = e.target.value; })}>
          {FONTS.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
      </div>

      <div className="section-h">可见性</div>
      <div className="form-row">
        <label>
          <input type="checkbox" checked={episode.visual.showSubtitleEn}
            onChange={(e) => patch((d) => { d.visual.showSubtitleEn = e.target.checked; })} />
          显示英文字幕
        </label>
      </div>
    </div>
  );
}

// ============================================================
// Scenes 卡片墙
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
  async function regenWst(id: string, sc: Scene) {
    alert(`即将调用 wst 重生：${id}\nprompt: ${sc.prompt.slice(0, 60)}…\n（mock 模式不真生图，联调阶段再接通）`);
    // 真实接通后：const r = await fetch('/tasks/wst', { method: 'POST', body: JSON.stringify({ params: { prompt: sc.prompt }})}); ...
  }
  async function regenTst(id: string, sc: Scene) {
    alert(`即将调用 tst 微调（以原图为参考）：${id}\nprompt: ${sc.prompt.slice(0, 60)}…\n（mock 模式不真生图）`);
  }

  return (
    <div>
      <div style={{ marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ color: 'var(--ink-soft)', fontSize: 12 }}>共 {Object.keys(episode.scenes).length} 个场景</span>
        <button className="btn sm" onClick={addScene}>+ 新场景</button>
      </div>
      <div style={{ display: 'grid', gap: 12 }}>
        {Object.entries(episode.scenes).map(([id, sc]) => (
          <div key={id} style={{
            border: '1px solid var(--border)',
            borderRadius: 10,
            padding: 12,
            background: '#faf8f3',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <span style={{ fontWeight: 700, fontSize: 13 }}>{id}</span>
              <span style={{ color: 'var(--ink-soft)', fontSize: 11 }}>
                {episode.beats.filter((b) => b.scene === id).length} 句字幕 · {sc.overlays?.length ?? 0} 插槽
              </span>
              <div style={{ flex: 1 }} />
              <button className="btn sm" onClick={() => regenWst(id, sc)}>从 prompt 重生 (wst)</button>
              <button className="btn sm" onClick={() => regenTst(id, sc)}>以原图微调 (tst)</button>
              <button className="btn sm ghost danger" onClick={() => delScene(id)}>×</button>
            </div>
            <div className="form-row">
              <label>图片 Prompt</label>
              <textarea value={sc.prompt}
                onChange={(e) => patch((d) => { d.scenes[id].prompt = e.target.value; })}
                style={{ minHeight: 70 }} />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              <div className="form-row">
                <label>进场动效</label>
                <select value={sc.motion?.enter ?? 'fade'}
                  onChange={(e) => patch((d) => {
                    d.scenes[id].motion = { ...(d.scenes[id].motion ?? { duration: 700 }), enter: e.target.value };
                  })}>
                  {SCENE_MOTIONS.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </div>
              <div className="form-row">
                <label>动效时长（ms）</label>
                <input type="number" value={sc.motion?.duration ?? 700}
                  onChange={(e) => patch((d) => {
                    d.scenes[id].motion = { enter: d.scenes[id].motion?.enter ?? 'fade', duration: Number(e.target.value) };
                  })} />
              </div>
            </div>
            <div style={{ fontSize: 11, color: 'var(--ink-soft)', marginTop: 4 }}>
              job_id: {jobId.slice(0, 8)} · 图片产物落 video-jobs/{jobId.slice(0, 6)}…/03_wst/
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ============================================================
// Overlays 文字插槽
// ============================================================
function OverlaysTab({ episode, patch }: { episode: Episode; patch: (m: (d: Episode) => void) => void }) {
  const all: Array<[string, number, Overlay]> = [];
  for (const [sid, sc] of Object.entries(episode.scenes)) {
    (sc.overlays ?? []).forEach((o, i) => all.push([sid, i, o]));
  }

  function setOv(sid: string, idx: number, mut: (o: Overlay) => void) {
    patch((d) => {
      const ov = d.scenes[sid].overlays?.[idx];
      if (ov) mut(ov);
    });
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

  if (all.length === 0) {
    return (
      <div>
        <p style={{ color: 'var(--ink-soft)' }}>当前没有文字插槽。选择一个场景添加：</p>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {Object.keys(episode.scenes).map((sid) => (
            <button key={sid} className="btn sm" onClick={() => addOv(sid)}>+ 在 {sid} 添加</button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div>
      <div style={{ marginBottom: 10, color: 'var(--ink-soft)', fontSize: 12 }}>
        共 {all.length} 个插槽。位置 x/y 单位是百分比（0-100），未来支持在预览 iframe 里直接拖。
      </div>
      <div style={{ display: 'grid', gap: 10 }}>
        {all.map(([sid, idx, o]) => (
          <div key={`${sid}-${idx}`} style={{
            border: '1px solid var(--border)',
            borderRadius: 8,
            padding: 10,
            background: '#faf8f3',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <span style={{ fontWeight: 600, fontSize: 12 }}>{sid} #{idx}</span>
              <div style={{ flex: 1 }} />
              <button className="btn sm ghost danger" onClick={() => delOv(sid, idx)}>×</button>
            </div>
            <div className="form-row">
              <label>文案</label>
              <input value={o.text} onChange={(e) => setOv(sid, idx, (ov) => { ov.text = e.target.value; })} />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
              <div className="form-row">
                <label>X %</label>
                <input type="number" value={o.pos.x}
                  onChange={(e) => setOv(sid, idx, (ov) => { ov.pos.x = Number(e.target.value); })} />
              </div>
              <div className="form-row">
                <label>Y %</label>
                <input type="number" value={o.pos.y}
                  onChange={(e) => setOv(sid, idx, (ov) => { ov.pos.y = Number(e.target.value); })} />
              </div>
              <div className="form-row">
                <label>旋转 °</label>
                <input type="number" value={o.style.rotation ?? 0}
                  onChange={(e) => setOv(sid, idx, (ov) => { ov.style.rotation = Number(e.target.value); })} />
              </div>
              <div className="form-row">
                <label>字体</label>
                <select value={o.style.font ?? 'XY Kaiti'}
                  onChange={(e) => setOv(sid, idx, (ov) => { ov.style.font = e.target.value; })}>
                  {FONTS.map((f) => <option key={f} value={f}>{f}</option>)}
                </select>
              </div>
              <div className="form-row">
                <label>字号 px</label>
                <input type="number" value={o.style.size ?? 60}
                  onChange={(e) => setOv(sid, idx, (ov) => { ov.style.size = Number(e.target.value); })} />
              </div>
              <div className="form-row">
                <label>颜色</label>
                <input value={o.style.color ?? 'var(--accent)'}
                  onChange={(e) => setOv(sid, idx, (ov) => { ov.style.color = e.target.value; })} />
              </div>
              <div className="form-row">
                <label>动效</label>
                <select value={o.motion?.enter ?? 'fly-in'}
                  onChange={(e) => setOv(sid, idx, (ov) => {
                    ov.motion = { ...(ov.motion ?? { duration: 900 }), enter: e.target.value };
                  })}>
                  {OVERLAY_MOTIONS.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </div>
              <div className="form-row">
                <label>动效时长 ms</label>
                <input type="number" value={o.motion?.duration ?? 900}
                  onChange={(e) => setOv(sid, idx, (ov) => {
                    ov.motion = { enter: ov.motion?.enter ?? 'fly-in', duration: Number(e.target.value) };
                  })} />
              </div>
              <div className="form-row">
                <label>延迟 ms</label>
                <input type="number" value={o.motion?.delay ?? 0}
                  onChange={(e) => setOv(sid, idx, (ov) => {
                    ov.motion = { ...(ov.motion ?? { enter: 'fly-in', duration: 900 }), delay: Number(e.target.value) };
                  })} />
              </div>
            </div>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 10, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {Object.keys(episode.scenes).map((sid) => (
          <button key={sid} className="btn sm" onClick={() => addOv(sid)}>+ 在 {sid} 添加</button>
        ))}
      </div>
    </div>
  );
}
