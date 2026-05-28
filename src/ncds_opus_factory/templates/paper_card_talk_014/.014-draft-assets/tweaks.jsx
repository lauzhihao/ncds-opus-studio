/* Tweaks 面板：标题、配色、配音、字号
 *
 * 字幕带固定走纸底（透明背景 + ink 文字），不再可调。
 *
 * 全部默认值与候选都来自 window.EPISODE（由 bootstrap.js 从 episode.json 加载）。
 * 本文件不再含 per-episode 字面值；调参请改 episode.json。
 *
 * 持久化：在 127.0.0.1 / localhost 下，每次 setTweak 后 500ms debounce 自动 POST
 * /__save_episode 把改动按 dot-path 写回 episode.json；线上 ncds.cc 不发请求。
 * 一次性的"摸索性微调"也会被记下来——所以别把它当临时调味，调完就是确定的样式。
 */

(function () {
  const ep = window.EPISODE;
  if (!ep) {
    console.error('tweaks.jsx: window.EPISODE missing — bootstrap.js 未先加载？');
    return;
  }

  const TITLE_OPTIONS = (ep.meta && ep.meta.titleOptions) || [];

  // 配色调色板：跨 episode 共用，故留在引擎层
  const PALETTES = {
    paper:  { name: "暖纸（默认）", "--bg": "#e6dfd0", "--card": "#fbf6e8", "--ink": "#1c1a16", "--ink-soft": "#4a4639", "--accent": "#c1392b", "--accent-soft": "#d4a44a", "--band": "#131210", "--band-text": "#ffffff", "--band-sub": "#cfc9b8" },
    sage:   { name: "鼠尾草",     "--bg": "#dde3d4", "--card": "#f3f5ea", "--ink": "#1d2419", "--ink-soft": "#4a5042", "--accent": "#2f6b50", "--accent-soft": "#c98a3c", "--band": "#11140f", "--band-text": "#ffffff", "--band-sub": "#c8cdbe" },
    dusty:  { name: "灰蓝",       "--bg": "#d8dde3", "--card": "#f1f3f6", "--ink": "#1a1f26", "--ink-soft": "#454c55", "--accent": "#234c8a", "--accent-soft": "#d4a44a", "--band": "#0f1218", "--band-text": "#ffffff", "--band-sub": "#c4c9d2" },
    bw:     { name: "黑白报刊",   "--bg": "#ebe7df", "--card": "#ffffff", "--ink": "#0b0b0b", "--ink-soft": "#3a3a3a", "--accent": "#cc1f1f", "--accent-soft": "#8a8a8a", "--band": "#000000", "--band-text": "#ffffff", "--band-sub": "#b9b9b9" },
  };

  const DEFAULTS = {
    title:          (ep.meta && ep.meta.title) || "",
    disclaimer:     (ep.meta && ep.meta.disclaimer) || "",
    palette:        (ep.visual && ep.visual.palette) || "paper",
    rate:           (ep.playback && ep.playback.rate) || 1,
    capZhSize:      (ep.visual && ep.visual.capZhSize) || 60,
    capEnSize:      (ep.visual && ep.visual.capEnSize) || 40,
    showSubtitleEn: (ep.visual && ep.visual.showSubtitleEn) !== false,
    kenBurns:       (ep.visual && ep.visual.kenBurns) !== false,
  };

  function applyPalette(name) {
    const p = PALETTES[name] || PALETTES.paper;
    const root = document.documentElement;
    ["--bg", "--card", "--ink", "--ink-soft", "--accent", "--accent-soft", "--band", "--band-text", "--band-sub"]
      .forEach(k => root.style.setProperty(k, p[k]));
  }

  // 字幕带固定纸底：透明背景，文字直接走 ink 色。
  // 每次切配色后都要重跑一次，因为 applyPalette 会把 --band-* 改回 palette 自带的深色值。
  function applyPaperBand() {
    const root = document.documentElement;
    document.body.classList.add("band-paper");
    root.style.setProperty("--band", "transparent");
    root.style.setProperty("--band-text", "var(--ink)");
    root.style.setProperty("--band-sub", "var(--ink-soft)");
  }

  // 字段 → episode.json dot-path 映射（auto-save 用）
  const FIELD_PATHS = {
    title:          'meta.title',
    disclaimer:     'meta.disclaimer',
    palette:        'visual.palette',
    rate:           'playback.rate',
    capZhSize:      'visual.capZhSize',
    capEnSize:      'visual.capEnSize',
    showSubtitleEn: 'visual.showSubtitleEn',
    kenBurns:       'visual.kenBurns',
  };

  // bootstrap.js 启动时已 ping /__ping，可达才启用 auto-save；
  // 不可达（线上纯静态托管）发了也是 404，省点噪音
  const IS_LOCAL = !!window.__editServerOk;

  // module-scope 的待写补丁 + 计时器：跨多次 setTweak 合并成一次 POST
  const _pending = {};
  let _saveTimer = null;
  function autoSaveField(field, value) {
    if (!IS_LOCAL) return;
    const p = FIELD_PATHS[field];
    if (!p) return; // 没映射的字段不动 episode.json
    _pending[p] = value;
    if (_saveTimer) clearTimeout(_saveTimer);
    _saveTimer = setTimeout(flushSave, 500);
  }
  async function flushSave() {
    _saveTimer = null;
    if (Object.keys(_pending).length === 0) return;
    // 先快照再清，让随后的 setTweak 进入新批次；不能 `const patches = _pending`
    // 然后清 _pending —— 那是同一个对象的引用，会把 patches 也清空（之前的坑）
    const patches = Object.assign({}, _pending);
    for (const k of Object.keys(_pending)) delete _pending[k];
    const slug = (ep.__slug) || (ep.meta && ep.meta.slug);
    try {
      const res = await fetch('./__save_episode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slug, patches }),
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => res.statusText);
        console.warn('[tweaks] save failed', res.status, txt);
        toastSave('保存失败 · ' + res.status, true);
      } else {
        // 同步内存中的 EPISODE，让其它读者拿到一致快照
        for (const [path, value] of Object.entries(patches)) {
          const parts = path.split('.');
          let cur = ep;
          for (const p of parts.slice(0, -1)) {
            if (!cur[p] || typeof cur[p] !== 'object') cur[p] = {};
            cur = cur[p];
          }
          cur[parts[parts.length - 1]] = value;
        }
        toastSave('已保存');
      }
    } catch (e) {
      console.warn('[tweaks] save error', e);
      toastSave('保存出错', true);
    }
  }
  function toastSave(msg, isErr) {
    const t = document.createElement('div');
    t.textContent = msg;
    t.style.cssText = 'position:fixed;left:50%;top:16px;transform:translateX(-50%);'
      + 'background:' + (isErr ? 'rgba(180,40,40,.9)' : 'rgba(0,0,0,.78)') + ';'
      + 'color:#fff;padding:4px 10px;border-radius:6px;'
      + 'font:11px ui-sans-serif,system-ui,sans-serif;z-index:2147483647;'
      + 'pointer-events:none;transition:opacity .25s;opacity:1;';
    document.body.appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; }, 800);
    setTimeout(() => t.remove(), 1100);
  }

  function App() {
    const [t, setTweakRaw] = useTweaks(DEFAULTS);
    // 包一层 setTweak：每次写也排进 auto-save 队列
    const setTweak = React.useCallback((k, v) => {
      setTweakRaw(k, v);
      autoSaveField(k, v);
    }, [setTweakRaw]);

    React.useEffect(() => { document.getElementById("brandTitle").textContent = t.title; }, [t.title]);
    React.useEffect(() => { document.querySelector(".disclaimer").textContent = t.disclaimer; }, [t.disclaimer]);
    React.useEffect(() => {
      window.__lastPalette = t.palette;
      applyPalette(t.palette);
      applyPaperBand();
    }, [t.palette]);
    React.useEffect(() => {
      document.documentElement.style.setProperty("--type-cap-zh", t.capZhSize + "px");
    }, [t.capZhSize]);
    React.useEffect(() => {
      document.documentElement.style.setProperty("--type-cap-en", t.capEnSize + "px");
    }, [t.capEnSize]);
    React.useEffect(() => {
      document.getElementById("capEn").style.display = t.showSubtitleEn ? "" : "none";
    }, [t.showSubtitleEn]);
    React.useEffect(() => {
      document.body.classList.toggle("ken-burns", t.kenBurns);
    }, [t.kenBurns]);
    React.useEffect(() => {
      const r = document.getElementById("rate");
      if (r) { r.value = t.rate; }
    }, [t.rate]);

    const goRecord = () => window.__player && window.__player.enterRecording();
    const goPlay = () => window.__player && window.__player.play();

    return (
      <TweaksPanel title="Tweaks">
        <TweakSection title="标题与署名">
          <TweakSelect label="标题候选" value={t.title}
            options={TITLE_OPTIONS.map(o => ({ value: o, label: o }))}
            onChange={v => setTweak("title", v)} />
          <TweakText label="自定标题" value={t.title}
            onChange={v => setTweak("title", v)} />
          <TweakText label="右上声明" value={t.disclaimer}
            onChange={v => setTweak("disclaimer", v)} />
        </TweakSection>

        <TweakSection title="配色">
          <TweakSelect label="主题" value={t.palette}
            options={Object.entries(PALETTES).map(([k, v]) => ({ value: k, label: v.name }))}
            onChange={v => setTweak("palette", v)} />
        </TweakSection>

        <TweakSection title="字幕">
          <TweakToggle label="显示英文字幕" value={t.showSubtitleEn}
            onChange={v => setTweak("showSubtitleEn", v)} />
          <TweakSlider label="中文字号" value={t.capZhSize} min={48} max={110} step={2}
            onChange={v => setTweak("capZhSize", v)} />
          <TweakSlider label="英文字号" value={t.capEnSize} min={20} max={56} step={1}
            onChange={v => setTweak("capEnSize", v)} />
        </TweakSection>

        <TweakSection title="节奏">
          <TweakSlider label="节奏速度" value={t.rate} min={0.6} max={1.6} step={0.05}
            onChange={v => {
              setTweak("rate", v);
              const r = document.getElementById("rate"); if (r) r.value = v;
            }} />
        </TweakSection>

        <TweakSection title="动画">
          <TweakToggle label="Ken Burns 缓推" value={t.kenBurns}
            onChange={v => setTweak("kenBurns", v)} />
        </TweakSection>

        <TweakSection title="录制">
          <TweakButton label="▶ 从头播放" onClick={goPlay} />
          <TweakButton label="● 进入录制模式" onClick={goRecord} />
        </TweakSection>
      </TweaksPanel>
    );
  }

  const root = document.createElement("div");
  root.id = "__tweaks_mount";
  document.body.appendChild(root);
  ReactDOM.createRoot(root).render(<App />);
})();
