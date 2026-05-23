/* Tweaks 面板：标题、配色、配音、字号、字幕带样式 */

const TITLE_OPTIONS = [
  "你以为上不上学，只是你一个人的事吗？",
  "你逃避的不是作业，是未来说「不」的资格",
  "读书不是为了学历，是为了给自己留退路",
];

const PALETTES = {
  paper:  { name: "暖纸（默认）", "--bg": "#e6dfd0", "--card": "#fbf6e8", "--ink": "#1c1a16", "--ink-soft": "#4a4639", "--accent": "#c1392b", "--accent-soft": "#d4a44a", "--band": "#131210", "--band-text": "#ffffff", "--band-sub": "#cfc9b8" },
  sage:   { name: "鼠尾草",     "--bg": "#dde3d4", "--card": "#f3f5ea", "--ink": "#1d2419", "--ink-soft": "#4a5042", "--accent": "#2f6b50", "--accent-soft": "#c98a3c", "--band": "#11140f", "--band-text": "#ffffff", "--band-sub": "#c8cdbe" },
  dusty:  { name: "灰蓝",       "--bg": "#d8dde3", "--card": "#f1f3f6", "--ink": "#1a1f26", "--ink-soft": "#454c55", "--accent": "#234c8a", "--accent-soft": "#d4a44a", "--band": "#0f1218", "--band-text": "#ffffff", "--band-sub": "#c4c9d2" },
  bw:     { name: "黑白报刊",   "--bg": "#ebe7df", "--card": "#ffffff", "--ink": "#0b0b0b", "--ink-soft": "#3a3a3a", "--accent": "#cc1f1f", "--accent-soft": "#8a8a8a", "--band": "#000000", "--band-text": "#ffffff", "--band-sub": "#b9b9b9" },
};

const DEFAULTS = /*EDITMODE-BEGIN*/{
  "title": "你以为上不上学，只是你一个人的事吗？",
  "disclaimer": "观点仅供参考　无不良引导",
  "palette": "paper",
  "rate": 0.95,
  "capZhSize": 60,
  "capEnSize": 40,
  "bandStyle": "paper",
  "showSubtitleEn": true,
  "kenBurns": true
}/*EDITMODE-END*/;

function applyPalette(name) {
  const p = PALETTES[name] || PALETTES.paper;
  const root = document.documentElement;
  ["--bg", "--card", "--ink", "--ink-soft", "--accent", "--accent-soft", "--band", "--band-text", "--band-sub"]
    .forEach(k => root.style.setProperty(k, p[k]));
}

function applyBandStyle(s) {
  const root = document.documentElement;
  document.body.classList.toggle("band-dark", s !== "paper");
  document.body.classList.toggle("band-paper", s === "paper");
  if (s === "paper") {
    // 字幕区改成纸底深字
    root.style.setProperty("--band", "transparent");
    root.style.setProperty("--band-text", "var(--ink)");
    root.style.setProperty("--band-sub", "var(--ink-soft)");
  } else {
    // dark 默认
    const p = PALETTES[window.__lastPalette || "paper"];
    root.style.setProperty("--band", p["--band"]);
    root.style.setProperty("--band-text", p["--band-text"]);
    root.style.setProperty("--band-sub", p["--band-sub"]);
  }
}

function App() {
  const [t, setTweak] = useTweaks(DEFAULTS);

  // 应用到页面
  React.useEffect(() => { document.getElementById("brandTitle").textContent = t.title; }, [t.title]);
  React.useEffect(() => { document.querySelector(".disclaimer").textContent = t.disclaimer; }, [t.disclaimer]);
  React.useEffect(() => {
    window.__lastPalette = t.palette;
    applyPalette(t.palette);
    applyBandStyle(t.bandStyle);
  }, [t.palette]);
  React.useEffect(() => { applyBandStyle(t.bandStyle); }, [t.bandStyle]);
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
    // 用 body class 切换 Ken Burns，让 player.js 在每个 scene 激活时
    // 可以按 scene 的总播放时长动态设 transform-duration（短句不再"还没动就切"）。
    // 不清 inline transition：showBeat 在每个 scene 重激活时会自行根据当前 ken-burns 状态重置。
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
        <TweakRadio label="字幕带" value={t.bandStyle}
          options={[{ value: "dark", label: "深色带" }, { value: "paper", label: "纸底" }]}
          onChange={v => setTweak("bandStyle", v)} />
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
        <div style={{ fontSize: 11, color: "rgba(255,255,255,.55)", marginTop: 6, lineHeight: 1.5 }}>
          按字幕字数估算每句停留时长；其他 TTS / 配音在剪映等后期加。
        </div>
      </TweakSection>

      <TweakSection title="动画">
        <TweakToggle label="Ken Burns 缓推" value={t.kenBurns}
          onChange={v => setTweak("kenBurns", v)} />
      </TweakSection>

      <TweakSection title="录制">
        <TweakButton label="▶ 从头播放" onClick={goPlay} />
        <TweakButton label="● 进入录制模式" onClick={goRecord} />
        <div style={{ fontSize: 11, color: "rgba(255,255,255,.55)", marginTop: 6, lineHeight: 1.5 }}>
          进入录制模式后会隐藏所有控件并 3 秒倒数自动播放，按 Esc 退出。建议先开 OBS / 剪映 / 录屏宝再点。
        </div>
      </TweakSection>
    </TweaksPanel>
  );
}

const root = document.createElement("div");
document.body.appendChild(root);
ReactDOM.createRoot(root).render(<App />);
