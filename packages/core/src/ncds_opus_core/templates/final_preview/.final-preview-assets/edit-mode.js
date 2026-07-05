/* edit-mode.js — final preview composition editor
 *
 * E toggles a lightweight editor for the new final_preview schema. The editor
 * writes to episode.meta and episode.visual.finalPreview, then persists the
 * whole episode through PUT /jobs/{job_id}/episode.
 */
(function () {
  if (window.__finalPreviewEditMode) return;

  const EP = window.EPISODE;
  if (!EP) {
    console.error('edit-mode: EPISODE missing');
    return;
  }

  const STYLE = `
    body.fp-editing .controls { display: none; }
    .fp-edit-panel {
      position: fixed;
      left: 50%;
      top: 50%;
      width: min(var(--fp-panel-width, 540px), calc(100vw - 24px));
      max-height: min(var(--fp-panel-max-h, 780px), calc(100vh - 24px));
      transform: translate(-50%, -50%);
      z-index: 2147483647;
      display: flex;
      flex-direction: column;
      background: rgba(251, 248, 240, .94);
      color: #1c1a16;
      border: 1px solid rgba(28, 26, 22, .18);
      box-shadow: 0 18px 48px rgba(0, 0, 0, .24);
      font: 13px/1.45 Inter, ui-sans-serif, system-ui, -apple-system, sans-serif;
      backdrop-filter: blur(18px);
    }
    .fp-edit-modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 2147483646;
      background: rgba(20, 18, 15, .12);
      cursor: default;
      touch-action: none;
    }
    body.fp-edit-modal-open .fp-edit-inspector {
      display: none !important;
      pointer-events: none;
    }
    .brand {
      transform: translate(var(--brand-x), var(--brand-y)) scale(var(--brand-scale, 1));
      transform-origin: left center;
    }
    .disclaimer {
      transform: translate(var(--disclaimer-x), var(--disclaimer-y)) scale(var(--disclaimer-scale, 1));
      transform-origin: right center;
    }
    .subtitle-band {
      transform: translate(var(--subtitle-x, 0px), var(--subtitle-y, 0px)) scale(var(--subtitle-scale, 1));
      transform-origin: center center;
    }
    .stage-background img {
      object-position: var(--stage-bg-x, 50%) var(--stage-bg-y, 50%);
      transform: scale(var(--stage-bg-scale, 1));
      transform-origin: var(--stage-bg-x, 50%) var(--stage-bg-y, 50%);
    }
    body.fp-editing .overlay-layer,
    body.fp-editing .sketch-layer {
      pointer-events: auto;
    }
    body.fp-editing .scene.active .scene-overlay,
    body.fp-editing .scene.active .scene-sketch {
      pointer-events: auto;
      cursor: move;
    }
    body.fp-editing .scene.active .scene-overlay:not(.fp-motion-preview),
    body.fp-editing .scene.active .scene-sketch:not(.fp-motion-preview) {
      animation: none !important;
      opacity: 1 !important;
    }
    body.fp-hide-subtitle-en .subtitle-en {
      display: none;
    }
    .brand-mark.fp-has-custom-logo {
      background: transparent;
      color: inherit;
      box-shadow: none;
      border-radius: 0;
    }
    .brand-mark.fp-has-custom-logo img {
      object-fit: cover;
      border-radius: inherit;
    }
    .fp-edit-head {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 10px 12px 9px;
      border-bottom: 1px solid rgba(28, 26, 22, .12);
      cursor: grab;
      touch-action: none;
      user-select: none;
    }
    .fp-edit-head.dragging {
      cursor: grabbing;
    }
    .fp-edit-head .fp-edit-actions,
    .fp-edit-head .fp-edit-actions * {
      cursor: auto;
    }
    .fp-edit-title {
      font-weight: 800;
      font-size: 15px;
      letter-spacing: 0;
      flex: 1;
    }
    .fp-edit-body {
      overflow: auto;
      padding: 9px 12px 12px;
      display: grid;
      gap: 8px;
    }
    .fp-edit-footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 9px 12px 11px;
      border-top: 1px solid rgba(28, 26, 22, .12);
      background: rgba(251, 248, 240, .72);
    }
    .fp-edit-footer-spacer {
      flex: 1 1 auto;
    }
    .fp-edit-section {
      border: 1px solid rgba(28, 26, 22, .12);
      background: rgba(255, 255, 255, .44);
      padding: 8px;
      display: grid;
      gap: 6px;
    }
    .fp-edit-section h4 {
      margin: 0 0 2px;
      font-size: 12px;
      font-weight: 800;
      color: #b8362a;
      letter-spacing: 0;
    }
    .fp-edit-tabs {
      display: flex;
      align-items: center;
      gap: 8px;
      border-bottom: 1px solid rgba(28, 26, 22, .14);
      padding: 0;
    }
    .fp-edit-tab {
      appearance: none;
      background: transparent;
      border: none;
      border-bottom: 2px solid transparent;
      color: rgba(28, 26, 22, .58);
      cursor: pointer;
      font-family: "JetBrains Mono", "SF Mono", Menlo, monospace;
      font-size: 13px;
      font-weight: 800;
      letter-spacing: .04em;
      margin-bottom: -1px;
      padding: 8px 18px 9px;
      transition: color .16s ease, border-color .16s ease;
    }
    .fp-edit-tab:hover {
      color: #1c1a16;
    }
    .fp-edit-tab:focus-visible {
      outline: 2px solid rgba(184, 54, 42, .28);
      outline-offset: -2px;
    }
    .fp-edit-tab[aria-selected="true"],
    .fp-edit-tab.active {
      border-bottom-color: #b8362a;
      color: #b8362a;
    }
    .fp-edit-tab-panels {
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    .fp-edit-tab-panel {
      display: grid;
      gap: 16px;
      padding-top: 14px;
    }
    .fp-edit-tab-panel > .fp-edit-section:first-child {
      margin-top: 0;
    }
    .fp-edit-tab-panel .fp-edit-section {
      border: 0;
      background: transparent;
      padding: 0;
      gap: 10px;
    }
    .fp-edit-tab-panel .fp-edit-section h4 {
      margin-bottom: 4px;
    }
    .fp-edit-tab-shell {
      display: flex;
      flex-direction: column;
      min-width: 0;
      min-height: 0;
    }
    .fp-edit-tab-panel:focus {
      outline: none;
    }
    .fp-edit-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px;
    }
    .fp-edit-field {
      display: grid;
      gap: 3px;
      min-width: 0;
    }
    .fp-edit-field-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 6px;
      min-width: 0;
    }
    .fp-edit-field.full { grid-column: 1 / -1; }
    .fp-edit-field.inline {
      display: flex;
      align-items: center;
      gap: 7px;
      padding-top: 16px;
    }
    .fp-edit-field span {
      color: rgba(28, 26, 22, .68);
      font-size: 11px;
      font-weight: 700;
    }
    .fp-edit-mini-action {
      appearance: none;
      width: 30px;
      height: 20px;
      border: 1px solid rgba(184, 54, 42, .38);
      background: rgba(184, 54, 42, .08);
      color: #b8362a;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      border-radius: 3px;
      flex: 0 0 auto;
      padding: 0;
    }
    .fp-edit-mini-action::before {
      content: "";
      width: 0;
      height: 0;
      border-top: 4px solid transparent;
      border-bottom: 4px solid transparent;
      border-left: 7px solid currentColor;
      margin-left: 2px;
    }
    .fp-edit-mini-action:hover {
      background: #b8362a;
      color: #fff;
    }
    .fp-edit-panel input,
    .fp-edit-panel select,
    .fp-edit-panel textarea {
      width: 100%;
      min-width: 0;
      border: 1px solid rgba(28, 26, 22, .18);
      background: rgba(255, 255, 255, .78);
      color: #1c1a16;
      padding: 6px 7px;
      border-radius: 0;
      font: inherit;
      outline: none;
    }
    .fp-edit-panel input:focus,
    .fp-edit-panel select:focus,
    .fp-edit-panel textarea:focus {
      border-color: #b8362a;
      box-shadow: 0 0 0 2px rgba(184, 54, 42, .14);
    }
    .fp-edit-panel textarea {
      min-height: 74px;
      resize: vertical;
    }
    .fp-edit-panel input[type="checkbox"] {
      width: 16px;
      height: 16px;
      padding: 0;
    }
    .fp-edit-panel input[type="color"] {
      min-height: 31px;
      padding: 3px;
      cursor: pointer;
    }
    .fp-edit-panel input[type="file"] {
      padding: 6px 8px;
      cursor: pointer;
    }
    .fp-edit-actions {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .fp-edit-status-dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: rgba(28, 26, 22, .28);
      box-shadow: inset 0 0 0 1px rgba(28, 26, 22, .12);
      flex: 0 0 auto;
    }
    .fp-edit-status-dot.saved {
      background: #2f8f5b;
      box-shadow: 0 0 0 3px rgba(47, 143, 91, .14);
    }
    .fp-edit-status-dot.dirty,
    .fp-edit-status-dot.saving {
      background: #b89434;
      box-shadow: 0 0 0 3px rgba(184, 148, 52, .14);
    }
    .fp-edit-status-dot.failed {
      background: #b8362a;
      box-shadow: 0 0 0 3px rgba(184, 54, 42, .14);
    }
    .fp-edit-btn {
      appearance: none;
      border: 1px solid rgba(28, 26, 22, .2);
      background: rgba(255, 255, 255, .62);
      color: #1c1a16;
      border-radius: 0;
      padding: 7px 10px;
      font: inherit;
      font-weight: 800;
      cursor: pointer;
      white-space: nowrap;
    }
    .fp-edit-btn.primary {
      border-color: #b8362a;
      background: #b8362a;
      color: #fff;
    }
    .fp-edit-btn:disabled {
      opacity: .48;
      cursor: default;
    }
    .fp-font-preview-box {
      grid-column: 1 / -1;
      border: 0;
      background: transparent;
      padding: 0;
      max-height: 300px;
      overflow: auto;
    }
    .fp-font-preview-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 7px;
    }
    .fp-font-preview-item {
      appearance: none;
      border: 1px solid rgba(28, 26, 22, .14);
      background: rgba(251, 248, 240, .84);
      color: #1c1a16;
      min-height: 78px;
      padding: 7px 13px;
      display: flex;
      align-items: center;
      justify-content: center;
      position: relative;
      font-family: var(--fp-preview-font);
      font-size: 67px;
      font-style: var(--fp-preview-style, normal);
      font-weight: var(--fp-preview-weight, 400);
      line-height: 1.1;
      text-decoration-line: var(--fp-preview-decoration, none);
      cursor: pointer;
      overflow: hidden;
    }
    .fp-font-preview-item:hover {
      border-color: rgba(184, 54, 42, .42);
      background: rgba(255, 255, 255, .72);
    }
    .fp-edit-field .fp-font-preview-sample {
      color: inherit;
      display: inline-block;
      font-size: var(--fp-preview-size, 84px);
      font-style: inherit;
      font-weight: inherit;
      line-height: .95;
      max-width: 100%;
      text-decoration-line: inherit;
      transform: translateY(var(--fp-preview-shift, 0));
      transform-origin: center center;
      white-space: nowrap;
    }
    .fp-font-preview-item.active {
      border-color: rgba(28, 26, 22, .14);
      box-shadow: inset 0 -3px 0 #b8362a;
      color: #b8362a;
      background: rgba(184, 54, 42, .04);
    }
    .fp-edit-style-buttons {
      display: flex;
      gap: 6px;
    }
    .fp-edit-style-btn {
      appearance: none;
      width: 36px;
      height: 32px;
      border: 1px solid rgba(28, 26, 22, .18);
      background: rgba(255, 255, 255, .66);
      color: #1c1a16;
      font: 800 15px/1 Inter, ui-sans-serif, system-ui, sans-serif;
      cursor: pointer;
    }
    .fp-edit-style-btn[data-font-toggle="italic"] {
      font-style: italic;
    }
    .fp-edit-style-btn[data-font-toggle="underline"] {
      text-decoration: underline;
    }
    .fp-edit-style-btn[data-font-toggle="strike"] {
      text-decoration: line-through;
    }
    .fp-edit-style-btn[data-font-toggle="super"],
    .fp-edit-style-btn[data-font-toggle="sub"] {
      font-size: 13px;
    }
    .fp-edit-style-btn.active {
      border-color: #b8362a;
      background: #b8362a;
      color: #fff;
    }
    .fp-edit-inspector {
      position: fixed;
      z-index: 2147483645;
      display: none;
      border: 2px dashed rgba(184, 54, 42, .9);
      background: rgba(184, 54, 42, .08);
      cursor: grab;
      touch-action: none;
    }
    .fp-edit-inspector.visible { display: block; }
    .fp-edit-inspector.dragging { cursor: grabbing; }
    .fp-edit-inspector.no-drag { cursor: pointer; }
    .fp-edit-grid-overlay {
      position: fixed;
      z-index: 2147483644;
      display: none;
      pointer-events: none;
      border: 1px solid rgba(184, 54, 42, .32);
      background-image:
        linear-gradient(rgba(184, 54, 42, .18) 1px, transparent 1px),
        linear-gradient(90deg, rgba(184, 54, 42, .18) 1px, transparent 1px),
        linear-gradient(rgba(28, 26, 22, .18) 1px, transparent 1px),
        linear-gradient(90deg, rgba(28, 26, 22, .18) 1px, transparent 1px);
      background-size:
        var(--fp-grid-minor, 40px) var(--fp-grid-minor, 40px),
        var(--fp-grid-minor, 40px) var(--fp-grid-minor, 40px),
        var(--fp-grid-major, 160px) var(--fp-grid-major, 160px),
        var(--fp-grid-major, 160px) var(--fp-grid-major, 160px);
      background-position: left top;
    }
    .fp-edit-grid-overlay.visible { display: block; }
    .fp-edit-handle {
      position: absolute;
      width: 28px;
      height: 28px;
      background: transparent;
      border: 0;
      box-shadow: none;
      pointer-events: auto;
    }
    .fp-edit-handle::after {
      content: "";
      position: absolute;
      left: 50%;
      top: 50%;
      width: 12px;
      height: 12px;
      transform: translate(-50%, -50%);
      background: #b8362a;
      border: 2px solid #fff;
      box-shadow: 0 2px 8px rgba(0, 0, 0, .22);
    }
    .fp-edit-handle[data-resize="nw"] { left: -15px; top: -15px; cursor: nwse-resize; }
    .fp-edit-handle[data-resize="n"] { left: 50%; top: -15px; transform: translateX(-50%); cursor: ns-resize; }
    .fp-edit-handle[data-resize="ne"] { right: -15px; top: -15px; cursor: nesw-resize; }
    .fp-edit-handle[data-resize="e"] { right: -15px; top: 50%; transform: translateY(-50%); cursor: ew-resize; }
    .fp-edit-handle[data-resize="se"] { right: -15px; bottom: -15px; cursor: nwse-resize; }
    .fp-edit-handle[data-resize="s"] { left: 50%; bottom: -15px; transform: translateX(-50%); cursor: ns-resize; }
    .fp-edit-handle[data-resize="sw"] { left: -15px; bottom: -15px; cursor: nesw-resize; }
    .fp-edit-handle[data-resize="w"] { left: -15px; top: 50%; transform: translateY(-50%); cursor: ew-resize; }
    .fp-edit-badge {
      position: absolute;
      right: 10px;
      bottom: 10px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 54px;
      height: 54px;
      padding: 0;
      appearance: none;
      background: #b8362a;
      color: #fff;
      border: 0;
      font-size: 12px;
      font-weight: 800;
      white-space: nowrap;
      pointer-events: auto;
      cursor: pointer;
      clip-path: polygon(100% 0, 100% 100%, 0 100%);
      filter: drop-shadow(0 4px 10px rgba(0, 0, 0, .18));
    }
    .fp-edit-badge:hover {
      background: #a82f25;
    }
    .fp-edit-badge svg {
      position: absolute;
      right: 8px;
      bottom: 8px;
      width: 18px;
      height: 18px;
      display: block;
      padding: 0;
      background: transparent;
      color: inherit;
      border-radius: 0;
      box-shadow: none;
      transform: none;
      pointer-events: none;
    }
    body.recording .fp-edit-panel { display: none !important; }
  `;

  const styleEl = document.createElement('style');
  styleEl.dataset.finalPreviewEdit = 'true';
  styleEl.textContent = STYLE;
  document.head.appendChild(styleEl);

  const ENTER_OPTIONS = [
    ['zoom-pop', '弹入'],
    ['drift-in', '漂入'],
    ['fade', '淡入'],
    ['bounce', '回弹'],
    ['ink-bleed', '墨迹'],
    ['slide-left', '左滑入'],
    ['slide-right', '右滑入'],
    ['slide-up', '上滑入'],
    ['slide-down', '下滑入'],
    ['spin-in', '旋入'],
    ['drop-in', '落入'],
    ['shake-attention', '抖动强调'],
    ['unfold', '展开'],
    ['elastic-pop', '弹性弹入'],
    ['tilt-in', '倾斜入场'],
    ['blur-pulse', '虚化脉冲'],
    ['rise-glow', '上浮发光'],
    ['none', '无'],
  ];
  const TEXT_ENTER_OPTIONS = [
    ['fade', '淡入'],
    ['zoom-in', '缩放'],
    ['fly-in', '飞入'],
    ['stamp', '印章'],
    ['blur', '虚化'],
    ['zoom-pop', '弹跳放大'],
    ['typewriter', '打字机'],
    ['handwrite', '手写显现'],
    ['ink-bleed', '墨迹晕开'],
    ['slide-clip', '遮罩滑入'],
    ['bounce', '回弹'],
    ['drift-in', '漂入'],
    ['spin-in', '旋入'],
    ['drop-in', '落入'],
    ['shake-attention', '抖动强调'],
    ['unfold', '展开'],
    ['letter-spread', '字距展开'],
    ['elastic-pop', '弹性弹入'],
    ['tilt-in', '倾斜入场'],
    ['fold-down', '折叠展开'],
    ['blur-pulse', '虚化脉冲'],
    ['rise-glow', '上浮发光'],
    ['shimmer-sweep', '高光扫过'],
    ['glitch', '故障闪入'],
    ['iris', '圆形揭示'],
    ['none', '无'],
  ];
  const WEIGHT_OPTIONS = [
    ['400', '400 Regular'],
    ['500', '500 Medium'],
    ['600', '600 SemiBold'],
    ['700', '700 Bold'],
    ['900', '900 Black'],
  ];
  const FONT_STYLE_OPTIONS = [
    ['normal', '正常'],
    ['italic', '斜体'],
  ];

  let panel = null;
  let modalBackdrop = null;
  let inspector = null;
  let gridOverlay = null;
  let editOpen = false;
  let dirty = false;
  let activeRegion = null;
  let panelRegion = null;
  let dragState = null;
  let panelDragState = null;
  let panelPosition = null;
  const panelTabs = {};
  const LOGO_EXPORT_MAX_SIZE = 512;
  const DRAG_THRESHOLD = 4;
  const AUTO_SAVE_DELAY = 650;
  let autoSaveTimer = null;
  let saving = false;
  let saveAgain = false;
  let changeVersion = 0;
  let savedVersion = 0;
  const REGION_DEFS = [
    { id: 'brand', label: 'Logo + Title', selector: '.brand', xField: 'titleX', yField: 'titleY' },
    { id: 'disclaimer', label: '声明', selector: '.disclaimer', xField: 'disclaimerX', yField: 'disclaimerY' },
    { id: 'subtitle', label: '字幕区域', selector: '.subtitle-band', xField: 'subtitleX', yField: 'subtitleY' },
    { id: 'asset', label: '剪影素材默认', selector: '.scene.active .scene-sketch, .scene-sketch', xField: 'assetsX', yField: 'assetsY' },
    { id: 'text', label: '文字素材', selector: '.scene.active .scene-overlay, .scene-overlay', xField: 'textX', yField: 'textY' },
  ];
  const PANEL_SIZES = {
    brand: ['720px', '840px'],
    disclaimer: ['680px', '630px'],
    subtitle: ['760px', '840px'],
    asset: ['540px', '780px'],
    text: ['680px', '780px'],
    sketchItem: ['560px', '720px'],
    overlayItem: ['760px', '840px'],
    default: ['540px', '630px'],
  };

  function ensureConfig() {
    EP.meta = EP.meta || {};
    EP.visual = EP.visual || {};
    const fp = EP.visual.finalPreview || {};
    fp.title = fp.title || {};
    fp.logo = fp.logo || {};
    fp.disclaimer = fp.disclaimer || {};
    fp.subtitle = fp.subtitle || {};
    fp.assets = fp.assets || {};
    fp.text = fp.text || {};
    EP.visual.finalPreview = fp;
    EP.visual.stage = EP.visual.stage || {};
    EP.visual.stage.background = EP.visual.stage.background || {};
    return fp;
  }

  ensureConfig();
  const INITIAL_EP = cloneValue(EP);

  function num(value, fallback) {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  }

  function int(value, fallback) {
    return Math.round(num(value, fallback));
  }

  function toPx(value, fallback) {
    const n = Number(value);
    return Number.isFinite(n) ? n + 'px' : fallback;
  }

  function toPct(value, fallback) {
    const n = Number(value);
    return Number.isFinite(n) ? n + '%' : fallback;
  }

  function toUnit(value, fallback) {
    const n = Number(value);
    return Number.isFinite(n) ? String(n) : fallback;
  }

  function fontValue(value, fallbackStack) {
    const text = String(value || '').trim();
    if (!text) return fallbackStack;
    if (/^(var\(|inherit|initial|unset|serif|sans-serif|monospace|system-ui|ui-)/.test(text)) return text;
    return '"' + text.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '", ' + fallbackStack;
  }

  function decorationValue(value) {
    const text = String(value || '').trim();
    return text && text !== 'none' ? text : 'none';
  }

  function verticalShift(value) {
    if (String(value) === 'super') return '-0.34em';
    if (String(value) === 'sub') return '0.28em';
    return '0';
  }

  function toggleDecorationValue(current, token) {
    const items = String(current || '').split(/\s+/).filter((item) => item && item !== 'none');
    const set = new Set(items);
    if (set.has(token)) set.delete(token);
    else set.add(token);
    return set.size ? Array.from(set).join(' ') : 'none';
  }

  function cloneValue(value) {
    if (value == null) return value;
    try {
      return JSON.parse(JSON.stringify(value));
    } catch (err) {
      console.warn('edit-mode: clone failed', err);
      return value;
    }
  }

  function resetObject(target, source) {
    Object.keys(target).forEach((key) => delete target[key]);
    Object.assign(target, cloneValue(source || {}));
  }

  function resetProp(target, source, key) {
    if (Object.prototype.hasOwnProperty.call(source || {}, key)) {
      target[key] = cloneValue(source[key]);
    } else {
      delete target[key];
    }
  }

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function selectedOptions(options, current) {
    return options.map(([value, label]) => (
      '<option value="' + value + '"' + (value === current ? ' selected' : '') + '>' + label + '</option>'
    )).join('');
  }

  function fontOptions() {
    const seen = new Set();
    const options = [];
    for (const f of (EP.fonts || [])) {
      const family = f && f.family ? String(f.family) : '';
      const src = f && f.src ? String(f.src) : '';
      if (!family || !src || seen.has(family)) continue;
      seen.add(family);
      options.push([family, family]);
    }
    return options;
  }

  function currentBeatIndex() {
    if (window.__player && typeof window.__player.currentBeat === 'function') {
      return int(window.__player.currentBeat(), 0);
    }
    const input = document.getElementById('progressInput');
    return Math.max(0, int(input && input.value, 1) - 1);
  }

  function shotsList(sourceEp) {
    const visual = (sourceEp || EP).visual || {};
    return Array.isArray(visual.shots) ? visual.shots : [];
  }

  function shotBeatIndex(shot, shotIndex) {
    const beatIndex = Number(shot && shot.beatIndex);
    if (Number.isFinite(beatIndex) && beatIndex > 0) return Math.round(beatIndex) - 1;
    return shotIndex;
  }

  function shotIdForEdit(shot, shotIndex) {
    return String((shot && (shot.shotId || shot.id)) || ('b' + String(shotIndex + 1).padStart(3, '0')));
  }

  function sketchGlobalId(shot, shotIndex, asset, assetIndex) {
    const imageFile = asset && asset.imageFile ? String(asset.imageFile) : '';
    const fromFile = imageFile.split('/').pop().replace(/\.[^.]+$/, '');
    if (fromFile) return fromFile;
    const shotId = shotIdForEdit(shot, shotIndex);
    const assetId = String((asset && asset.id) || ('a' + (assetIndex + 1)));
    return shotId + '-' + assetId;
  }

  function sketchLabel(shot, shotIndex, asset, assetIndex) {
    return '剪影素材 ' + sketchGlobalId(shot, shotIndex, asset, assetIndex);
  }

  function sketchNavEntries() {
    const entries = [];
    const shots = shotsList(EP);
    shots.forEach((shot, shotIndex) => {
      const assets = shot && Array.isArray(shot.assets) ? shot.assets : [];
      assets.forEach((asset, assetIndex) => {
        entries.push({
          shotIndex,
          assetIndex,
          beatIndex: shotBeatIndex(shot, shotIndex),
          label: sketchLabel(shot, shotIndex, asset, assetIndex),
        });
      });
    });
    return entries;
  }

  function currentSketchNavIndex(region, entries) {
    if (!region || region.id !== 'sketchItem') return -1;
    const shotIndex = currentShotIndex();
    const assetIndex = int(region.itemIndex, 0);
    return (entries || sketchNavEntries()).findIndex((entry) => (
      entry.shotIndex === shotIndex && entry.assetIndex === assetIndex
    ));
  }

  function currentShot() {
    if (window.__player && typeof window.__player.currentShot === 'function') {
      return window.__player.currentShot();
    }
    const shots = shotsList(EP);
    return shots[currentBeatIndex()] || null;
  }

  function currentShotIndex() {
    const shot = currentShot();
    const shots = shotsList(EP);
    const idx = shots.indexOf(shot);
    return idx >= 0 ? idx : currentBeatIndex();
  }

  function activeSceneEl() {
    if (window.__player && window.__player.sceneNodes) {
      return window.__player.sceneNodes['visual-stage'] || null;
    }
    return document.querySelector('.scene.active.visual-stage, .scene.active');
  }

  function itemIndexFromDataset(el, key) {
    const raw = el && el.dataset ? el.dataset[key] : '';
    const n = Number(raw);
    return Number.isFinite(n) ? n : 0;
  }

  function sketchForRegion(region, sourceEp) {
    const shots = shotsList(sourceEp || EP);
    const shot = sourceEp ? shots[currentShotIndex()] : currentShot();
    const items = shot && Array.isArray(shot.assets) ? shot.assets : [];
    return items[region && region.itemIndex] || null;
  }

  function overlayForRegion(region, sourceEp) {
    const shots = shotsList(sourceEp || EP);
    const shot = sourceEp ? shots[currentShotIndex()] : currentShot();
    const items = shot && Array.isArray(shot.emphasis) ? shot.emphasis : [];
    return items[region && region.itemIndex] || null;
  }

  function ensurePos(item) {
    item.pos = item.pos && typeof item.pos === 'object' ? item.pos : {};
    if (item.pos.x == null) item.pos.x = 50;
    if (item.pos.y == null) item.pos.y = 50;
    return item.pos;
  }

  function ensureStyle(item) {
    item.style = item.style && typeof item.style === 'object'
      ? item.style
      : (typeof item.style === 'string' && item.style !== 'auto' ? { preset: item.style } : {});
    return item.style;
  }

  function ensureMotion(item) {
    item.motion = item.motion && typeof item.motion === 'object' ? item.motion : {};
    return item.motion;
  }

  function itemRectBasis(region) {
    const parent = region && region.el && region.el.closest('.scene');
    const rect = parent ? parent.getBoundingClientRect() : null;
    return rect && rect.width && rect.height ? rect : null;
  }

  function itemPosition(region) {
    const item = region && region.id === 'sketchItem' ? sketchForRegion(region) : overlayForRegion(region);
    const pos = item ? ensurePos(item) : {};
    return {
      x: num(pos.x, 50),
      y: num(pos.y, 50),
    };
  }

  function overlayPresetOptions() {
    const styles = (window.__overlays && Array.isArray(window.__overlays.STYLES)) ? window.__overlays.STYLES : [];
    return [['auto', '自动']].concat(styles.map((value) => [value, value]));
  }

  function jobIdFromUrl() {
    if (window.__previewJobId) return window.__previewJobId;
    const m = location.pathname.match(/^\/preview\/([^\/]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function setStatus(text) {
    if (!panel) return;
    const el = panel.querySelector('[data-status-dot]');
    if (!el) return;
    const value = text || '';
    el.title = value || 'idle';
    el.classList.remove('saved', 'dirty', 'saving', 'failed');
    if (value === 'saved') el.classList.add('saved');
    else if (/fail|error/i.test(value)) el.classList.add('failed');
    else if (/saving/i.test(value)) el.classList.add('saving');
    else if (value) el.classList.add('dirty');
  }

  function markDirty() {
    dirty = true;
    changeVersion += 1;
    setStatus('changed');
    scheduleAutoSave();
  }

  function scheduleAutoSave() {
    if (!jobIdFromUrl()) return;
    if (autoSaveTimer) window.clearTimeout(autoSaveTimer);
    autoSaveTimer = window.setTimeout(() => {
      autoSaveTimer = null;
      save();
    }, AUTO_SAVE_DELAY);
  }

  function apply() {
    if (window.__finalPreviewApply) window.__finalPreviewApply(EP);
    else if (window.__player && window.__player.refreshVisual) window.__player.refreshVisual();
    syncPositionVars();
    syncLogoState();
    updateInspector();
  }

  function syncExtraVars() {
    const fp = ensureConfig();
    const bg = (EP.visual.stage || {}).background || {};
    const title = fp.title || {};
    const disclaimer = fp.disclaimer || {};
    const subtitle = fp.subtitle || {};
    document.documentElement.style.setProperty('--brand-title-font', fontValue(title.font, '"Noto Serif SC", "Source Han Serif SC", "Songti SC", serif'));
    document.documentElement.style.setProperty('--brand-title-size', toPx(title.size, '44px'));
    document.documentElement.style.setProperty('--brand-title-weight', toUnit(title.weight, '700'));
    document.documentElement.style.setProperty('--brand-title-style', title.fontStyle || 'normal');
    document.documentElement.style.setProperty('--brand-title-decoration', decorationValue(title.decoration));
    document.documentElement.style.setProperty('--brand-title-baseline-shift', verticalShift(title.vertical));
    document.documentElement.style.setProperty('--disclaimer-font', fontValue(disclaimer.font, '"Noto Serif SC", "Source Han Serif SC", "Songti SC", serif'));
    document.documentElement.style.setProperty('--disclaimer-size', toPx(disclaimer.size, '30px'));
    document.documentElement.style.setProperty('--disclaimer-weight', toUnit(disclaimer.weight, '600'));
    document.documentElement.style.setProperty('--disclaimer-style', disclaimer.fontStyle || 'normal');
    document.documentElement.style.setProperty('--disclaimer-decoration', decorationValue(disclaimer.decoration));
    document.documentElement.style.setProperty('--disclaimer-baseline-shift', verticalShift(disclaimer.vertical));
    document.documentElement.style.setProperty('--subtitle-x', toPx(fp.subtitle.x, '0px'));
    document.documentElement.style.setProperty('--subtitle-y', toPx(fp.subtitle.y, '0px'));
    document.documentElement.style.setProperty('--subtitle-scale', toUnit(fp.subtitle.scale, '1'));
    document.documentElement.style.setProperty('--subtitle-zh-font', fontValue(subtitle.font, '"Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif'));
    document.documentElement.style.setProperty('--subtitle-zh-size', toPx(subtitle.size, '78px'));
    document.documentElement.style.setProperty('--subtitle-zh-weight', toUnit(subtitle.weight, '700'));
    document.documentElement.style.setProperty('--subtitle-zh-style', subtitle.fontStyle || 'normal');
    document.documentElement.style.setProperty('--subtitle-zh-decoration', decorationValue(subtitle.decoration));
    document.documentElement.style.setProperty('--subtitle-zh-baseline-shift', verticalShift(subtitle.vertical));
    document.documentElement.style.setProperty('--subtitle-en-font', fontValue(subtitle.subFont, '"Inter", "Helvetica Neue", system-ui, sans-serif'));
    document.documentElement.style.setProperty('--subtitle-en-size', toPx(subtitle.subSize, '38px'));
    document.documentElement.style.setProperty('--subtitle-en-weight', toUnit(subtitle.subWeight, '500'));
    document.documentElement.style.setProperty('--subtitle-en-style', subtitle.subFontStyle || 'normal');
    document.documentElement.style.setProperty('--subtitle-en-decoration', decorationValue(subtitle.subDecoration));
    document.documentElement.style.setProperty('--subtitle-en-baseline-shift', verticalShift(subtitle.subVertical));
    document.documentElement.style.setProperty('--stage-bg-x', toPct(bg.x, '50%'));
    document.documentElement.style.setProperty('--stage-bg-y', toPct(bg.y, '50%'));
    document.documentElement.style.setProperty('--stage-bg-scale', toUnit(bg.scale, '1'));
    document.documentElement.style.setProperty('--type-cap-zh', toPx(fp.subtitle.size || EP.visual.capZhSize, '78px'));
    document.documentElement.style.setProperty('--type-cap-en', toPx(fp.subtitle.subSize || EP.visual.capEnSize, '38px'));
    document.body.classList.toggle('fp-hide-subtitle-en', EP.visual.showSubtitleEn === false);
  }

  function syncPositionVars() {
    const fp = ensureConfig();
    document.documentElement.style.setProperty('--brand-x', toPx(fp.title.x, '0px'));
    document.documentElement.style.setProperty('--brand-y', toPx(fp.title.y, '0px'));
    document.documentElement.style.setProperty('--brand-scale', toUnit(fp.title.scale, '1'));
    document.documentElement.style.setProperty('--disclaimer-x', toPx(fp.disclaimer.x, '0px'));
    document.documentElement.style.setProperty('--disclaimer-y', toPx(fp.disclaimer.y, '0px'));
    document.documentElement.style.setProperty('--disclaimer-scale', toUnit(fp.disclaimer.scale, '1'));
    document.documentElement.style.setProperty('--asset-x', toPx(fp.assets.x, '0px'));
    document.documentElement.style.setProperty('--asset-y', toPx(fp.assets.y, '0px'));
    document.documentElement.style.setProperty('--asset-scale', toUnit(fp.assets.scale, '1'));
    document.documentElement.style.setProperty('--text-x', toPx(fp.text.x, '0px'));
    document.documentElement.style.setProperty('--text-y', toPx(fp.text.y, '0px'));
    document.documentElement.style.setProperty('--text-scale', toUnit(fp.text.scale, '1'));
    document.documentElement.style.setProperty('--text-font', fontValue(fp.text.font, '"Noto Sans SC", "PingFang SC", sans-serif'));
    document.documentElement.style.setProperty('--text-weight', toUnit(fp.text.weight, 'normal'));
    document.documentElement.style.setProperty('--text-style', fp.text.fontStyle || 'normal');
    document.documentElement.style.setProperty('--text-decoration', decorationValue(fp.text.decoration));
    document.documentElement.style.setProperty('--text-baseline-shift', verticalShift(fp.text.vertical));
    syncExtraVars();
  }

  function notifyParent(editing) {
    if (window.parent === window) return;
    window.parent.postMessage({
      type: 'final-preview-edit-mode',
      editing: !!editing,
    }, window.location.origin);
  }

  function stageScale() {
    const stage = document.getElementById('stage');
    if (!stage) return 1;
    const rect = stage.getBoundingClientRect();
    return rect.width ? rect.width / 1920 : 1;
  }

  function syncLogoState() {
    const mark = document.querySelector('.brand-mark');
    if (!mark) return;
    mark.classList.toggle('fp-has-custom-logo', !!mark.querySelector('img'));
  }

  function updatePanelLayout() {
    if (!panel) return;
    const size = PANEL_SIZES[(panelRegion && panelRegion.id) || 'default'] || PANEL_SIZES.default;
    panel.style.setProperty('--fp-panel-width', size[0]);
    panel.style.setProperty('--fp-panel-max-h', size[1]);
    if (panelPosition) {
      requestAnimationFrame(() => applyPanelPosition(panelPosition));
    } else {
      panel.style.left = '50%';
      panel.style.top = '50%';
      panel.style.transform = 'translate(-50%, -50%)';
    }
  }

  function clampPanelPosition(left, top) {
    if (!panel) return { left, top };
    const rect = panel.getBoundingClientRect();
    const margin = 12;
    const maxLeft = Math.max(margin, window.innerWidth - rect.width - margin);
    const maxTop = Math.max(margin, window.innerHeight - rect.height - margin);
    return {
      left: Math.min(Math.max(margin, left), maxLeft),
      top: Math.min(Math.max(margin, top), maxTop),
    };
  }

  function applyPanelPosition(position) {
    if (!panel || !position) return;
    const next = clampPanelPosition(position.left, position.top);
    panelPosition = next;
    panel.style.left = Math.round(next.left) + 'px';
    panel.style.top = Math.round(next.top) + 'px';
    panel.style.transform = 'none';
  }

  function ensureInspector() {
    if (inspector) return inspector;
    inspector = document.createElement('div');
    inspector.className = 'fp-edit-inspector';
    inspector.innerHTML =
      '<span class="fp-edit-handle" data-resize="nw"></span>' +
      '<span class="fp-edit-handle" data-resize="n"></span>' +
      '<span class="fp-edit-handle" data-resize="ne"></span>' +
      '<span class="fp-edit-handle" data-resize="e"></span>' +
      '<span class="fp-edit-handle" data-resize="se"></span>' +
      '<span class="fp-edit-handle" data-resize="s"></span>' +
      '<span class="fp-edit-handle" data-resize="sw"></span>' +
      '<span class="fp-edit-handle" data-resize="w"></span>' +
      '<button type="button" class="fp-edit-badge" aria-label="打开控制面板" title="打开控制面板">' +
      '<svg viewBox="0 0 512 512" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="42" d="M96 416h84l216-216a59.4 59.4 0 0 0 0-84l-8-8a59.4 59.4 0 0 0-84 0L88 324v84a8 8 0 0 0 8 8Z"/><path fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="42" d="m268 144l100 100"/></svg>' +
      '</button>';
    inspector.addEventListener('pointerdown', onInspectorPointerDown);
    document.body.appendChild(inspector);
    return inspector;
  }

  function ensureGridOverlay() {
    if (gridOverlay) return gridOverlay;
    gridOverlay = document.createElement('div');
    gridOverlay.className = 'fp-edit-grid-overlay';
    document.body.appendChild(gridOverlay);
    return gridOverlay;
  }

  function updateGridOverlay() {
    if (!gridOverlay || !editOpen) return;
    const stage = document.getElementById('stage');
    if (!stage) {
      gridOverlay.classList.remove('visible');
      return;
    }
    const rect = stage.getBoundingClientRect();
    const k = stageScale() || 1;
    gridOverlay.style.left = Math.round(rect.left) + 'px';
    gridOverlay.style.top = Math.round(rect.top) + 'px';
    gridOverlay.style.width = Math.round(rect.width) + 'px';
    gridOverlay.style.height = Math.round(rect.height) + 'px';
    gridOverlay.style.setProperty('--fp-grid-minor', Math.max(16, Math.round(80 * k)) + 'px');
    gridOverlay.style.setProperty('--fp-grid-major', Math.max(64, Math.round(320 * k)) + 'px');
  }

  function showGridOverlay() {
    ensureGridOverlay();
    updateGridOverlay();
    if (gridOverlay) gridOverlay.classList.add('visible');
  }

  function hideGridOverlay() {
    if (gridOverlay) gridOverlay.classList.remove('visible');
  }

  function isPanelEventTarget(target) {
    return !!(target && target.closest && (target.closest('.fp-edit-panel') || target.closest('.controls')));
  }

  function findRegionFromTarget(target) {
    if (!target || !target.closest || isPanelEventTarget(target)) return null;
    if (target.closest('.fp-edit-inspector')) return activeRegion;
    const sketch = target.closest('.scene.active .scene-sketch, .scene-sketch');
    if (sketch) {
      const idx = Math.max(0, itemIndexFromDataset(sketch, 'sketchIndex') - 1);
      const shotIdx = currentShotIndex();
      const shot = currentShot();
      const asset = shot && Array.isArray(shot.assets) ? shot.assets[idx] : null;
      return {
        id: 'sketchItem',
        label: sketchLabel(shot, shotIdx, asset, idx),
        el: sketch,
        itemIndex: idx,
        xField: 'sketchItemX',
        yField: 'sketchItemY',
      };
    }
    const overlay = target.closest('.scene.active .scene-overlay, .scene-overlay');
    if (overlay) {
      const idx = Math.max(0, itemIndexFromDataset(overlay, 'overlayIndex'));
      return {
        id: 'overlayItem',
        label: '文字素材 #' + (idx + 1),
        el: overlay,
        itemIndex: idx,
        xField: 'overlayItemX',
        yField: 'overlayItemY',
      };
    }
    for (const def of REGION_DEFS) {
      const el = target.closest(def.selector);
      if (!el) continue;
      return { ...def, el };
    }
    return null;
  }

  function findRegionAtPoint(x, y) {
    if (!inspector) return findRegionFromTarget(document.elementFromPoint(x, y));
    const prev = inspector.style.pointerEvents;
    inspector.style.pointerEvents = 'none';
    const target = document.elementFromPoint(x, y);
    inspector.style.pointerEvents = prev;
    return findRegionFromTarget(target);
  }

  function sameRegion(a, b) {
    return !!a && !!b && a.id === b.id && a.el === b.el;
  }

  function updateInspector(region) {
    if (!inspector || !editOpen) return;
    const next = region || activeRegion;
    if (!next || !next.el || !next.el.isConnected) {
      inspector.classList.remove('visible');
      return;
    }
    const rect = next.el.getBoundingClientRect();
    const pad = 6;
    inspector.style.left = Math.round(rect.left - pad) + 'px';
    inspector.style.top = Math.round(rect.top - pad) + 'px';
    inspector.style.width = Math.round(rect.width + pad * 2) + 'px';
    inspector.style.height = Math.round(rect.height + pad * 2) + 'px';
    inspector.classList.toggle('no-drag', !next.xField || !next.yField);
    inspector.classList.add('visible');
  }

  function clearInspector() {
    if (dragState) return;
    activeRegion = null;
    if (inspector) inspector.classList.remove('visible');
  }

  function stagePoint(selector, fallbackX, fallbackY) {
    const stage = document.getElementById('stage');
    const el = selector ? document.querySelector(selector) : null;
    if (!stage || !el) return { x: int(fallbackX, 0), y: int(fallbackY, 0) };
    const s = stage.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    const k = stageScale();
    return {
      x: Math.round((r.left - s.left) / k),
      y: Math.round((r.top - s.top) / k),
    };
  }

  function actualFieldValue(name, fallback) {
    const fp = ensureConfig();
    const table = {
      titleX: ['.brand', fp.title.x || 0, fp.title.y || 0, 'x'],
      titleY: ['.brand', fp.title.x || 0, fp.title.y || 0, 'y'],
      disclaimerX: ['.disclaimer', fp.disclaimer.x || 0, fp.disclaimer.y || 0, 'x'],
      disclaimerY: ['.disclaimer', fp.disclaimer.x || 0, fp.disclaimer.y || 0, 'y'],
      subtitleX: ['.subtitle-band', fp.subtitle.x || 0, fp.subtitle.y || 0, 'x'],
      subtitleY: ['.subtitle-band', fp.subtitle.x || 0, fp.subtitle.y || 0, 'y'],
      assetsX: ['.scene.active .scene-sketch, .scene-sketch', fp.assets.x || 0, fp.assets.y || 0, 'x'],
      assetsY: ['.scene.active .scene-sketch, .scene-sketch', fp.assets.x || 0, fp.assets.y || 0, 'y'],
      textX: ['.scene.active .scene-overlay, .scene-overlay', fp.text.x || 0, fp.text.y || 0, 'x'],
      textY: ['.scene.active .scene-overlay, .scene-overlay', fp.text.x || 0, fp.text.y || 0, 'y'],
    };
    const cfg = table[name];
    if (!cfg) return int(fallback, 0);
    const point = stagePoint(cfg[0], cfg[1], cfg[2]);
    return point[cfg[3]];
  }

  function applyActualPosition(name, raw) {
    const fp = ensureConfig();
    const table = {
      titleX: [fp.title, '.brand', 'x'],
      titleY: [fp.title, '.brand', 'y'],
      disclaimerX: [fp.disclaimer, '.disclaimer', 'x'],
      disclaimerY: [fp.disclaimer, '.disclaimer', 'y'],
      subtitleX: [fp.subtitle, '.subtitle-band', 'x'],
      subtitleY: [fp.subtitle, '.subtitle-band', 'y'],
      assetsX: [fp.assets, '.scene.active .scene-sketch, .scene-sketch', 'x'],
      assetsY: [fp.assets, '.scene.active .scene-sketch, .scene-sketch', 'y'],
      textX: [fp.text, '.scene.active .scene-overlay, .scene-overlay', 'x'],
      textY: [fp.text, '.scene.active .scene-overlay, .scene-overlay', 'y'],
    };
    const cfg = table[name];
    if (!cfg) return false;
    const target = num(raw, null);
    if (target == null) return true;
    const axis = cfg[2];
    const current = actualFieldValue(name, 0);
    const prop = axis === 'x' ? 'x' : 'y';
    cfg[0][prop] = num(cfg[0][prop], 0) + (target - current);
    return true;
  }

  function positionTargetForRegion(region) {
    const fp = ensureConfig();
    if (!region) return null;
    if (region.id === 'brand') return fp.title;
    if (region.id === 'disclaimer') return fp.disclaimer;
    if (region.id === 'subtitle') return fp.subtitle;
    if (region.id === 'asset') return fp.assets;
    if (region.id === 'text') return fp.text;
    return null;
  }

  function scaleTargetForRegion(region) {
    const fp = ensureConfig();
    if (!region) return null;
    if (region.id === 'brand') return fp.title;
    if (region.id === 'disclaimer') return fp.disclaimer;
    if (region.id === 'subtitle') return fp.subtitle;
    if (region.id === 'asset') return fp.assets;
    if (region.id === 'text') return fp.text;
    return null;
  }

  function resolveRegionElement(region) {
    if (!region) return null;
    const scene = activeSceneEl();
    if (region.id === 'sketchItem') {
      return scene && scene.querySelector(':scope > .sketch-layer > .scene-sketch[data-sketch-index="' + (region.itemIndex + 1) + '"]');
    }
    if (region.id === 'overlayItem') {
      return scene && scene.querySelector(':scope > .overlay-layer > .scene-overlay[data-overlay-index="' + region.itemIndex + '"]');
    }
    return region.el || null;
  }

  function refreshRegionElement(region) {
    if (!region) return region;
    const el = resolveRegionElement(region);
    if (el) region.el = el;
    return region;
  }

  function applyItemLive(region) {
    if (!region) return;
    const scene = activeSceneEl();
    if (!scene || !window.__overlays) return;
    if (region.id === 'sketchItem') {
      const item = sketchForRegion(region);
      if (item && window.__overlays.updateSketchLive) {
        window.__overlays.updateSketchLive(scene, region.itemIndex, {
          pos: ensurePos(item),
          size: num(item.size, 30),
        });
      }
    } else if (region.id === 'overlayItem') {
      const item = overlayForRegion(region);
      if (item && window.__overlays.updateLive) {
        window.__overlays.updateLive(scene, region.itemIndex, {
          text: item.text,
          pos: ensurePos(item),
          style: ensureStyle(item),
        });
      }
    }
  }

  function moveRegionByDelta(region, dx, dy) {
    if (!region || !region.xField || !region.yField) return;
    if (region.id === 'sketchItem' || region.id === 'overlayItem') {
      const item = region.id === 'sketchItem' ? sketchForRegion(region) : overlayForRegion(region);
      const basis = itemRectBasis(region);
      if (!item || !basis) return;
      const pos = ensurePos(item);
      pos.x = num(pos.x, 50) + (dx / basis.width) * 100;
      pos.y = num(pos.y, 50) + (dy / basis.height) * 100;
      applyItemLive(region);
      return;
    }
    const target = positionTargetForRegion(region);
    if (!target) return;
    const k = stageScale() || 1;
    target.x = num(target.x, 0) + dx / k;
    target.y = num(target.y, 0) + dy / k;
  }

  function resizeRegion(region, handle, dx, dy, startRect, startScale) {
    if (!region || !startRect) return;
    const west = handle.indexOf('w') >= 0;
    const east = handle.indexOf('e') >= 0;
    const north = handle.indexOf('n') >= 0;
    const south = handle.indexOf('s') >= 0;
    const xDelta = east ? dx : (west ? -dx : 0);
    const yDelta = south ? dy : (north ? -dy : 0);
    const xFactor = startRect.width ? 1 + xDelta / startRect.width : 1;
    const yFactor = startRect.height ? 1 + yDelta / startRect.height : 1;
    let factor = 1;
    if ((east || west) && (north || south)) factor = Math.max(xFactor, yFactor);
    else if (east || west) factor = xFactor;
    else if (north || south) factor = yFactor;
    if (region.id === 'sketchItem') {
      const item = sketchForRegion(region);
      if (!item) return;
      item.size = Math.max(2, Math.min(160, startScale * factor));
      applyItemLive(region);
      return;
    }
    if (region.id === 'overlayItem') {
      const item = overlayForRegion(region);
      if (!item) return;
      const style = ensureStyle(item);
      style.size = Math.max(8, Math.min(240, startScale * factor));
      applyItemLive(region);
      return;
    }
    const target = scaleTargetForRegion(region);
    if (!target) return;
    target.scale = Math.max(0.1, Math.min(5, startScale * factor));
  }

  function regionPositionText(region) {
    if (!region) return '';
    if (region.id === 'sketchItem' || region.id === 'overlayItem') {
      const pos = itemPosition(region);
      return region.label + ' x=' + pos.x.toFixed(1) + '% y=' + pos.y.toFixed(1) + '%';
    }
    const x = actualFieldValue(region.xField, 0);
    const y = actualFieldValue(region.yField, 0);
    return region.label + ' x=' + x + 'px y=' + y + 'px';
  }

  function regionScaleText(region) {
    if (region && region.id === 'sketchItem') {
      const item = sketchForRegion(region);
      return region.label + ' size=' + num(item && item.size, 30).toFixed(1) + '%';
    }
    if (region && region.id === 'overlayItem') {
      const item = overlayForRegion(region);
      const style = item ? ensureStyle(item) : {};
      return region.label + ' font=' + num(style.size, 48).toFixed(0) + 'px';
    }
    const target = scaleTargetForRegion(region);
    const scale = target ? num(target.scale, 1) : 1;
    return region.label + ' scale=' + scale.toFixed(2);
  }

  function clearMotionPreview(el) {
    if (!el) return;
    el.classList.remove('fp-motion-preview');
    el.style.removeProperty('animation-name');
    el.style.removeProperty('animation-duration');
    el.style.removeProperty('animation-delay');
    el.style.removeProperty('animation-fill-mode');
    el.style.removeProperty('animation-iteration-count');
    el._fpPreviewTimer = null;
  }

  function previewRegionMotion(region) {
    const targetRegion = refreshRegionElement(region || panelRegion || activeRegion);
    if (!targetRegion || !targetRegion.el) return;
    const el = targetRegion.el;
    let animName = '';
    for (const cls of el.classList) {
      if (
        cls.indexOf('mo-ov-') === 0 ||
        cls.indexOf('oa-') === 0 ||
        cls.indexOf('sk-enter-') === 0
      ) {
        animName = cls;
        break;
      }
    }
    if (!animName) {
      setStatus('no motion');
      return;
    }
    const item = targetRegion.id === 'sketchItem' ? sketchForRegion(targetRegion) : overlayForRegion(targetRegion);
    const motion = (item && item.motion) || {};
    const duration = num(motion.duration, targetRegion.id === 'sketchItem' ? 600 : 700);
    const delay = num(motion.delay, 0);
    if (el._fpPreviewTimer) {
      window.clearTimeout(el._fpPreviewTimer);
      clearMotionPreview(el);
    }
    el.classList.add('fp-motion-preview');
    el.style.setProperty('animation-name', 'none', 'important');
    void el.offsetWidth;
    el.style.setProperty('animation-name', animName, 'important');
    el.style.setProperty('animation-duration', duration + 'ms', 'important');
    el.style.setProperty('animation-delay', delay + 'ms', 'important');
    el.style.setProperty('animation-fill-mode', 'forwards', 'important');
    el.style.setProperty('animation-iteration-count', '1', 'important');
    el._fpPreviewTimer = window.setTimeout(() => {
      clearMotionPreview(el);
    }, delay + duration + 120);
    setStatus('motion preview');
  }

  function scheduleRegionMotionPreview(region) {
    if (!region || (region.id !== 'sketchItem' && region.id !== 'overlayItem')) return;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => previewRegionMotion(region));
    });
  }

  function resetRegionToInitial(region) {
    if (!region) return false;
    const fp = ensureConfig();
    const meta = EP.meta || {};
    const visual = EP.visual || {};
    const initialMeta = INITIAL_EP.meta || {};
    const initialVisual = INITIAL_EP.visual || {};
    const initialFp = initialVisual.finalPreview || {};

    if (region.id === 'brand') {
      resetProp(meta, initialMeta, 'brandTitle');
      resetObject(fp.title, initialFp.title);
      resetObject(fp.logo, initialFp.logo);
    } else if (region.id === 'disclaimer') {
      resetProp(meta, initialMeta, 'disclaimer');
      resetObject(fp.disclaimer, initialFp.disclaimer);
    } else if (region.id === 'subtitle') {
      resetProp(visual, initialVisual, 'showSubtitleEn');
      resetObject(fp.subtitle, initialFp.subtitle);
    } else if (region.id === 'asset') {
      resetObject(fp.assets, initialFp.assets);
    } else if (region.id === 'text') {
      resetObject(fp.text, initialFp.text);
    } else if (region.id === 'sketchItem' || region.id === 'overlayItem') {
      const shotIdx = currentShotIndex();
      const shot = shotsList(EP)[shotIdx];
      const initialShot = shotsList(INITIAL_EP)[shotIdx];
      const key = region.id === 'sketchItem' ? 'assets' : 'emphasis';
      const items = shot && Array.isArray(shot[key]) ? shot[key] : null;
      const initialItems = initialShot && Array.isArray(initialShot[key]) ? initialShot[key] : null;
      if (!items || !items[region.itemIndex]) return false;
      if (initialItems && initialItems[region.itemIndex]) {
        resetObject(items[region.itemIndex], initialItems[region.itemIndex]);
      } else {
        if (region.id === 'sketchItem') {
          items[region.itemIndex].pos = { x: 50, y: 50 };
          delete items[region.itemIndex].size;
          delete items[region.itemIndex].motion;
        } else {
          items[region.itemIndex].pos = { x: 50, y: 50 };
          delete items[region.itemIndex].style;
          delete items[region.itemIndex].motion;
        }
      }
    } else {
      return false;
    }

    apply();
    refreshRegionElement(region);
    markDirty();
    renderPanel(region);
    updatePanelLayout();
    setStatus(region.label + ' reset');
    return true;
  }

  function cssColorToHex(value, fallback) {
    const raw = value || fallback || '#1c1a16';
    if (/^#[0-9a-f]{6}$/i.test(raw)) return raw;
    if (/^#[0-9a-f]{3}$/i.test(raw)) {
      return '#' + raw.slice(1).split('').map((c) => c + c).join('');
    }
    const probe = document.createElement('span');
    probe.style.color = raw;
    document.body.appendChild(probe);
    const color = getComputedStyle(probe).color;
    probe.remove();
    const m = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
    if (!m) return fallback || '#1c1a16';
    return '#' + [m[1], m[2], m[3]].map((v) => {
      const hex = Number(v).toString(16);
      return hex.length === 1 ? '0' + hex : hex;
    }).join('');
  }

  function squareLogoDataUrl(rawUrl, fileType) {
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        const width = img.naturalWidth || img.width || 0;
        const height = img.naturalHeight || img.height || 0;
        const sourceSize = Math.min(width, height);
        if (!sourceSize) {
          resolve(rawUrl);
          return;
        }
        const canvasSize = Math.max(1, Math.min(LOGO_EXPORT_MAX_SIZE, sourceSize));
        const canvas = document.createElement('canvas');
        canvas.width = canvasSize;
        canvas.height = canvasSize;
        const ctx = canvas.getContext('2d');
        if (!ctx) {
          resolve(rawUrl);
          return;
        }
        const sx = Math.round((width - sourceSize) / 2);
        const sy = Math.round((height - sourceSize) / 2);
        ctx.clearRect(0, 0, canvasSize, canvasSize);
        ctx.drawImage(img, sx, sy, sourceSize, sourceSize, 0, 0, canvasSize, canvasSize);
        try {
          resolve(canvas.toDataURL(fileType === 'image/jpeg' ? 'image/jpeg' : 'image/png', 0.92));
        } catch (err) {
          console.warn('edit-mode: square logo export failed', err);
          resolve(rawUrl);
        }
      };
      img.onerror = () => resolve(rawUrl);
      img.src = rawUrl;
    });
  }

  function handleLogoUpload(file) {
    if (!file) return;
    if (!/^image\//.test(file.type || '')) {
      setStatus('logo upload failed');
      return;
    }
    const reader = new FileReader();
    reader.onload = async () => {
      const fp = ensureConfig();
      const rawUrl = String(reader.result || '');
      fp.logo.url = await squareLogoDataUrl(rawUrl, file.type || '');
      apply();
      markDirty();
      setStatus('logo changed: 1:1');
    };
    reader.onerror = () => setStatus('logo upload failed');
    reader.readAsDataURL(file);
  }

  function updateRegionScopedField(name, raw) {
    const region = panelRegion || activeRegion;
    if (!region || (region.id !== 'sketchItem' && region.id !== 'overlayItem')) return false;
    const value = raw;
    let needsRefresh = false;
    let autoPreviewMotion = false;

    if (region.id === 'sketchItem') {
      const item = sketchForRegion(region);
      if (!item) return true;
      const pos = ensurePos(item);
      const motion = ensureMotion(item);
      switch (name) {
        case 'sketchItemX':
          pos.x = num(value, 50);
          break;
        case 'sketchItemY':
          pos.y = num(value, 50);
          break;
        case 'sketchItemSize':
          item.size = num(value, 30);
          break;
        case 'sketchItemEnter':
          motion.enter = String(value) === 'none' ? 'none' : String(value);
          needsRefresh = true;
          autoPreviewMotion = true;
          break;
        case 'sketchItemDuration':
          motion.duration = num(value, 600);
          needsRefresh = true;
          break;
        case 'sketchItemDelay':
          motion.delay = num(value, 0);
          needsRefresh = true;
          break;
        default:
          return false;
      }
    } else if (region.id === 'overlayItem') {
      const item = overlayForRegion(region);
      if (!item) return true;
      const pos = ensurePos(item);
      const style = ensureStyle(item);
      const motion = ensureMotion(item);
      switch (name) {
        case 'overlayItemText':
          item.text = String(value);
          break;
        case 'overlayItemX':
          pos.x = num(value, 50);
          break;
        case 'overlayItemY':
          pos.y = num(value, 50);
          break;
        case 'overlayItemPreset':
          if (String(value) === 'auto') delete style.preset;
          else style.preset = String(value);
          needsRefresh = true;
          break;
        case 'overlayItemEnter':
          motion.enter = String(value) === 'none' ? 'none' : String(value);
          needsRefresh = true;
          autoPreviewMotion = true;
          break;
        case 'overlayItemFrom':
          motion.from = String(value);
          needsRefresh = true;
          autoPreviewMotion = true;
          break;
        case 'overlayItemDuration':
          motion.duration = num(value, 600);
          needsRefresh = true;
          break;
        case 'overlayItemDelay':
          motion.delay = num(value, 0);
          needsRefresh = true;
          break;
        case 'overlayItemFont':
          if (String(value).trim()) style.font = String(value).trim();
          else {
            delete style.font;
            needsRefresh = true;
          }
          break;
        case 'overlayItemSize':
          style.size = num(value, 48);
          break;
        case 'overlayItemWeight':
          style.weight = String(value) === 'normal' ? 'normal' : num(value, 400);
          break;
        case 'overlayItemFontStyle':
          style.fontStyle = String(value) || 'normal';
          break;
        case 'overlayItemDecoration':
          style.textDecoration = decorationValue(value);
          break;
        case 'overlayItemVertical':
          style.vertical = String(value) || 'baseline';
          style.baselineShift = verticalShift(value);
          break;
        case 'overlayItemColor':
          style.color = String(value).trim();
          break;
        case 'overlayItemRotation':
          style.rotation = num(value, 0);
          break;
        case 'overlayItemLetterSpacing':
          style.letterSpacing = num(value, 0);
          break;
        case 'overlayItemBackground':
          if (String(value).trim()) style.background = String(value).trim();
          else {
            delete style.background;
            needsRefresh = true;
          }
          break;
        case 'overlayItemPadding':
          if (String(value).trim()) style.padding = String(value).trim();
          else {
            delete style.padding;
            needsRefresh = true;
          }
          break;
        case 'overlayItemBorder':
          if (String(value).trim()) style.border = String(value).trim();
          else {
            delete style.border;
            needsRefresh = true;
          }
          break;
        case 'overlayItemBorderRadius':
          style.borderRadius = num(value, 0);
          break;
        case 'overlayItemShadow':
          if (String(value).trim()) style.shadow = String(value).trim();
          else {
            delete style.shadow;
            needsRefresh = true;
          }
          break;
        case 'overlayItemWhiteSpace':
          style.whiteSpace = String(value).trim() || 'nowrap';
          break;
        default:
          return false;
      }
    }

    if (needsRefresh && window.__player && window.__player.refreshVisual) {
      window.__player.refreshVisual();
      refreshRegionElement(region);
    } else {
      applyItemLive(region);
    }
    updateInspector(region);
    markDirty();
    if (autoPreviewMotion) scheduleRegionMotionPreview(region);
    return true;
  }

  function updateField(name, raw, isCheckbox) {
    if (updateRegionScopedField(name, raw)) return;
    const fp = ensureConfig();
    const value = isCheckbox ? !!raw : raw;
    switch (name) {
      case 'titleText':
        EP.meta.brandTitle = String(value);
        break;
      case 'titleX':
        applyActualPosition(name, value);
        break;
      case 'titleY':
        applyActualPosition(name, value);
        break;
      case 'titleScale':
        fp.title.scale = num(value, 1);
        break;
      case 'titleFont':
        fp.title.font = String(value).trim();
        break;
      case 'titleSize':
        fp.title.size = num(value, 44);
        break;
      case 'titleWeight':
        fp.title.weight = num(value, 700);
        break;
      case 'titleFontStyle':
        fp.title.fontStyle = String(value) || 'normal';
        break;
      case 'titleDecoration':
        fp.title.decoration = decorationValue(value);
        break;
      case 'titleVertical':
        fp.title.vertical = String(value) || 'baseline';
        break;
      case 'logoHidden':
        fp.logo.hidden = value;
        break;
      case 'logoSize':
        fp.logo.size = num(value, 60);
        break;
      case 'logoUrl':
        fp.logo.url = String(value).trim();
        break;
      case 'disclaimerText':
        EP.meta.disclaimer = String(value);
        break;
      case 'disclaimerX':
        applyActualPosition(name, value);
        break;
      case 'disclaimerY':
        applyActualPosition(name, value);
        break;
      case 'disclaimerScale':
        fp.disclaimer.scale = num(value, 1);
        break;
      case 'disclaimerFont':
        fp.disclaimer.font = String(value).trim();
        break;
      case 'disclaimerSize':
        fp.disclaimer.size = num(value, 30);
        break;
      case 'disclaimerWeight':
        fp.disclaimer.weight = num(value, 600);
        break;
      case 'disclaimerFontStyle':
        fp.disclaimer.fontStyle = String(value) || 'normal';
        break;
      case 'disclaimerDecoration':
        fp.disclaimer.decoration = decorationValue(value);
        break;
      case 'disclaimerVertical':
        fp.disclaimer.vertical = String(value) || 'baseline';
        break;
      case 'showSubtitleEn':
        EP.visual.showSubtitleEn = value;
        break;
      case 'subtitleX':
        applyActualPosition(name, value);
        break;
      case 'subtitleY':
        applyActualPosition(name, value);
        break;
      case 'subtitleScale':
        fp.subtitle.scale = num(value, 1);
        break;
      case 'subtitleFont':
        fp.subtitle.font = String(value).trim();
        break;
      case 'subtitleSubFont':
        fp.subtitle.subFont = String(value).trim();
        break;
      case 'subtitleWeight':
        fp.subtitle.weight = num(value, 700);
        break;
      case 'subtitleSubWeight':
        fp.subtitle.subWeight = num(value, 500);
        break;
      case 'subtitleFontStyle':
        fp.subtitle.fontStyle = String(value) || 'normal';
        break;
      case 'subtitleSubFontStyle':
        fp.subtitle.subFontStyle = String(value) || 'normal';
        break;
      case 'subtitleDecoration':
        fp.subtitle.decoration = decorationValue(value);
        break;
      case 'subtitleSubDecoration':
        fp.subtitle.subDecoration = decorationValue(value);
        break;
      case 'subtitleVertical':
        fp.subtitle.vertical = String(value) || 'baseline';
        break;
      case 'subtitleSubVertical':
        fp.subtitle.subVertical = String(value) || 'baseline';
        break;
      case 'subtitleColor':
        fp.subtitle.color = String(value).trim();
        break;
      case 'subtitleSubColor':
        fp.subtitle.subColor = String(value).trim();
        break;
      case 'subtitleSize':
        fp.subtitle.size = num(value, 78);
        break;
      case 'subtitleSubSize':
        fp.subtitle.subSize = num(value, 38);
        break;
      case 'subtitleHeight':
        fp.subtitle.height = num(value, 220);
        break;
      case 'assetsX':
        applyActualPosition(name, value);
        break;
      case 'assetsY':
        applyActualPosition(name, value);
        break;
      case 'assetsScale':
        fp.assets.scale = num(value, 1);
        break;
      case 'assetsSize':
        fp.assets.size = num(value, 30);
        break;
      case 'assetsEnter':
        fp.assets.enter = String(value) === 'none' ? '' : String(value);
        break;
      case 'assetsDuration':
        fp.assets.duration = num(value, 600);
        break;
      case 'textX':
        applyActualPosition(name, value);
        break;
      case 'textY':
        applyActualPosition(name, value);
        break;
      case 'textScale':
        fp.text.scale = num(value, 1);
        break;
      case 'textFont':
        fp.text.font = String(value).trim();
        break;
      case 'textWeight':
        fp.text.weight = String(value) === 'normal' ? 'normal' : num(value, 400);
        break;
      case 'textFontStyle':
        fp.text.fontStyle = String(value) || 'normal';
        break;
      case 'textDecoration':
        fp.text.decoration = decorationValue(value);
        break;
      case 'textVertical':
        fp.text.vertical = String(value) || 'baseline';
        break;
      case 'textSize':
        fp.text.size = num(value, 48);
        break;
      case 'textColor':
        fp.text.color = String(value).trim();
        break;
      case 'textEnter':
        fp.text.enter = String(value) === 'none' ? '' : String(value);
        break;
      default:
        return;
    }
    apply();
    markDirty();
  }

  function onDocumentMouseMove(event) {
    if (!editOpen || dragState) return;
    if (isPanelEventTarget(event.target)) return;
    const region = findRegionAtPoint(event.clientX, event.clientY);
    if (!region) {
      clearInspector();
      return;
    }
    if (!sameRegion(activeRegion, region)) activeRegion = region;
    updateInspector(activeRegion);
  }

  function onInspectorPointerDown(event) {
    if (!editOpen || !activeRegion) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.target && event.target.closest && event.target.closest('.fp-edit-badge')) {
      openPanel(activeRegion);
      return;
    }
    const resizeHandle = event.target && event.target.dataset ? event.target.dataset.resize : '';
    const scaleTarget = scaleTargetForRegion(activeRegion);
    let startScale = scaleTarget ? num(scaleTarget.scale, 1) : 1;
    if (activeRegion.id === 'sketchItem') {
      const item = sketchForRegion(activeRegion);
      startScale = num(item && item.size, 30);
    } else if (activeRegion.id === 'overlayItem') {
      const item = overlayForRegion(activeRegion);
      const style = item ? ensureStyle(item) : {};
      startScale = num(style.size, 48);
    }
    dragState = {
      kind: resizeHandle ? 'resize' : 'move',
      region: activeRegion,
      handle: resizeHandle || '',
      startRect: activeRegion.el ? activeRegion.el.getBoundingClientRect() : null,
      startScale,
      startX: event.clientX,
      startY: event.clientY,
      lastX: event.clientX,
      lastY: event.clientY,
      moved: false,
    };
    if (inspector && inspector.setPointerCapture) {
      try { inspector.setPointerCapture(event.pointerId); } catch (err) {}
    }
    if (inspector) inspector.classList.add('dragging');
    showGridOverlay();
    window.addEventListener('pointermove', onWindowPointerMove);
    window.addEventListener('pointerup', onWindowPointerUp);
    window.addEventListener('pointercancel', onWindowPointerUp);
  }

  function onWindowPointerMove(event) {
    if (!dragState) return;
    const dxTotal = event.clientX - dragState.startX;
    const dyTotal = event.clientY - dragState.startY;
    if (!dragState.moved && Math.hypot(dxTotal, dyTotal) < DRAG_THRESHOLD) return;
    dragState.moved = true;
    const dx = event.clientX - dragState.lastX;
    const dy = event.clientY - dragState.lastY;
    dragState.lastX = event.clientX;
    dragState.lastY = event.clientY;
    if (dragState.kind === 'resize') {
      resizeRegion(dragState.region, dragState.handle, dxTotal, dyTotal, dragState.startRect, dragState.startScale);
    } else {
      moveRegionByDelta(dragState.region, dx, dy);
    }
    syncPositionVars();
    updateInspector(dragState.region);
    updateGridOverlay();
    markDirty();
    setStatus(dragState.kind === 'resize' ? regionScaleText(dragState.region) : regionPositionText(dragState.region));
  }

  function onWindowPointerUp() {
    if (!dragState) return;
    const finished = dragState;
    window.removeEventListener('pointermove', onWindowPointerMove);
    window.removeEventListener('pointerup', onWindowPointerUp);
    window.removeEventListener('pointercancel', onWindowPointerUp);
    if (inspector) inspector.classList.remove('dragging');
    hideGridOverlay();
    dragState = null;
    if (finished.moved) {
      activeRegion = finished.region;
      updateInspector(activeRegion);
      setStatus(finished.kind === 'resize' ? regionScaleText(finished.region) : regionPositionText(finished.region));
    } else {
      activeRegion = finished.region;
      updateInspector(activeRegion);
    }
  }

  function onViewportChange() {
    updatePanelLayout();
    updateInspector();
    updateGridOverlay();
  }

  async function save() {
    const jobId = jobIdFromUrl();
    if (!jobId) {
      setStatus('save failed: missing job id');
      return;
    }
    if (autoSaveTimer) {
      window.clearTimeout(autoSaveTimer);
      autoSaveTimer = null;
    }
    if (saving) {
      saveAgain = true;
      return;
    }
    saving = true;
    const version = changeVersion;
    let succeeded = false;
    setStatus('saving...');
    try {
      const res = await fetch('/jobs/' + encodeURIComponent(jobId) + '/episode', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(EP),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status + ': ' + await res.text());
      savedVersion = version;
      dirty = changeVersion !== savedVersion;
      succeeded = true;
      setStatus('saved');
    } catch (err) {
      dirty = true;
      setStatus('save failed');
      console.error('edit-mode: save episode failed', err);
    } finally {
      saving = false;
      if (saveAgain || (succeeded && dirty)) {
        saveAgain = false;
        scheduleAutoSave();
      }
    }
  }

  function activePanelTab(regionId) {
    return panelTabs[regionId] || 'base';
  }

  function scheduleFontPreviewFit() {
    if (!panel || !panel.querySelector('.fp-font-preview-item')) return;
    const run = () => fitFontPreviewSamples();
    window.requestAnimationFrame(() => {
      run();
      if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(run).catch(() => {});
        const loads = Array.from(panel.querySelectorAll('.fp-font-preview-item')).map((item) => {
          const cs = window.getComputedStyle(item);
          const font = (cs.fontStyle || 'normal') + ' ' + (cs.fontWeight || '400') + ' 120px ' + (cs.fontFamily || 'sans-serif');
          return document.fonts.load(font, '能成大事').catch(() => null);
        });
        Promise.all(loads).then(run).catch(() => {});
      }
    });
  }

  function fitFontPreviewSamples() {
    if (!panel) return;
    const items = Array.from(panel.querySelectorAll('.fp-font-preview-item'));
    if (!items.length) return;
    const canvas = fitFontPreviewSamples.canvas || document.createElement('canvas');
    fitFontPreviewSamples.canvas = canvas;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    for (const item of items) {
      const sample = item.querySelector('.fp-font-preview-sample');
      if (!sample) continue;
      const cs = window.getComputedStyle(item);
      const baseSize = 100;
      const family = cs.fontFamily || 'sans-serif';
      const style = cs.fontStyle || 'normal';
      const weight = cs.fontWeight || '400';
      ctx.font = style + ' ' + weight + ' ' + baseSize + 'px ' + family;
      const metric = ctx.measureText(sample.textContent || '能成大事');
      const inkWidth = Math.max(
        1,
        (metric.actualBoundingBoxLeft || 0) + (metric.actualBoundingBoxRight || 0),
        metric.width || 0
      );
      const inkHeight = Math.max(
        1,
        (metric.actualBoundingBoxAscent || 0) + (metric.actualBoundingBoxDescent || 0),
        baseSize * 0.72
      );
      const targetWidth = Math.max(1, item.clientWidth * 0.88);
      const targetHeight = Math.max(1, item.clientHeight * 0.66);
      const fitSize = Math.floor(baseSize * Math.min(targetWidth / inkWidth, targetHeight / inkHeight));
      const nextSize = Math.max(41, Math.min(112, fitSize));
      sample.style.setProperty('--fp-preview-size', nextSize + 'px');
    }
  }

  function tabbedContent(regionId, baseContent, fontContent) {
    const tab = activePanelTab(regionId);
    const panelId = 'fp-edit-panel-' + regionId + '-' + tab;
    return '<div class="fp-edit-tab-shell">' +
      '<div class="fp-edit-tabs" role="tablist" aria-label="设置分类">' +
      '<button type="button" role="tab" aria-selected="' + (tab === 'base' ? 'true' : 'false') + '" class="fp-edit-tab' + (tab === 'base' ? ' active' : '') + '" data-action="panel-tab" data-tab="base">基础</button>' +
      '<button type="button" role="tab" aria-selected="' + (tab === 'font' ? 'true' : 'false') + '" class="fp-edit-tab' + (tab === 'font' ? ' active' : '') + '" data-action="panel-tab" data-tab="font">字体</button>' +
      '</div>' +
      '<div class="fp-edit-tab-panels">' +
      '<div class="fp-edit-tab-panel" id="' + esc(panelId) + '" role="tabpanel" tabindex="-1">' +
      (tab === 'font' ? fontContent : baseContent) +
      '</div>' +
      '</div>' +
      '</div>';
  }

  function fontPreviewField(label, name, value, fallbackStack, preview) {
    const current = String(value || '');
    const p = preview || {};
    const weight = p.weight || 400;
    const fontStyle = p.fontStyle || 'normal';
    const decoration = decorationValue(p.decoration);
    const shift = verticalShift(p.vertical);
    const buttons = fontOptions().map(([font, fontLabel]) => {
      const selected = String(font || '') === current;
      return '<button type="button" class="fp-font-preview-item' + (selected ? ' active' : '') + '"' +
        ' data-font-field="' + name + '"' +
        ' data-font-value="' + esc(font || '') + '"' +
        ' title="' + esc(fontLabel) + '"' +
        ' aria-label="' + esc(fontLabel) + '"' +
        ' style="--fp-preview-font: ' + esc(fontValue(font, fallbackStack)) + ';' +
        ' --fp-preview-weight: ' + esc(weight) + ';' +
        ' --fp-preview-style: ' + esc(fontStyle) + ';' +
        ' --fp-preview-decoration: ' + esc(decoration) + ';' +
        ' --fp-preview-shift: ' + esc(shift) + ';">' +
        '<span class="fp-font-preview-sample">能成大事</span></button>';
    }).join('');
    return '<div class="fp-edit-field full">' +
      (label ? '<span>' + esc(label) + '</span>' : '') +
      '<div class="fp-font-preview-box"><div class="fp-font-preview-grid">' + buttons + '</div></div>' +
      '</div>';
  }

  function styleButtonField(weightField, styleField, decorationField, verticalField, weight, fontStyle, decoration, vertical) {
    const isBold = num(weight, 0) >= 700;
    const isItalic = String(fontStyle || 'normal') === 'italic';
    const decor = decorationValue(decoration);
    const isUnderline = decor.split(/\s+/).indexOf('underline') >= 0;
    const isStrike = decor.split(/\s+/).indexOf('line-through') >= 0;
    const isSuper = String(vertical || 'baseline') === 'super';
    const isSub = String(vertical || 'baseline') === 'sub';
    const underlineNext = toggleDecorationValue(decor, 'underline');
    const strikeNext = toggleDecorationValue(decor, 'line-through');
    return '<div class="fp-edit-field full">' +
      '<span>样式</span>' +
      '<div class="fp-edit-style-buttons">' +
      '<button type="button" class="fp-edit-style-btn' + (isBold ? ' active' : '') + '" data-font-toggle="bold" data-button-field="' + weightField + '" data-button-value="' + (isBold ? '400' : '700') + '">B</button>' +
      '<button type="button" class="fp-edit-style-btn' + (isItalic ? ' active' : '') + '" data-font-toggle="italic" data-button-field="' + styleField + '" data-button-value="' + (isItalic ? 'normal' : 'italic') + '">I</button>' +
      '<button type="button" class="fp-edit-style-btn' + (isUnderline ? ' active' : '') + '" data-font-toggle="underline" data-button-field="' + decorationField + '" data-button-value="' + esc(underlineNext) + '">U</button>' +
      '<button type="button" class="fp-edit-style-btn' + (isStrike ? ' active' : '') + '" data-font-toggle="strike" data-button-field="' + decorationField + '" data-button-value="' + esc(strikeNext) + '">S</button>' +
      '<button type="button" class="fp-edit-style-btn' + (isSuper ? ' active' : '') + '" data-font-toggle="super" data-button-field="' + verticalField + '" data-button-value="' + (isSuper ? 'baseline' : 'super') + '">x²</button>' +
      '<button type="button" class="fp-edit-style-btn' + (isSub ? ' active' : '') + '" data-font-toggle="sub" data-button-field="' + verticalField + '" data-button-value="' + (isSub ? 'baseline' : 'sub') + '">x₂</button>' +
      '</div>' +
      '</div>';
  }

  function renderPanel(region) {
    const fp = ensureConfig();
    if (region && region.id === 'sketchItem') {
      const shotIdx = currentShotIndex();
      const shot = currentShot();
      const assets = shot && Array.isArray(shot.assets) ? shot.assets : [];
      region.label = sketchLabel(shot, shotIdx, assets[region.itemIndex], region.itemIndex);
    }
    const titleText = EP.meta.brandTitle || EP.meta.title || '';
    const disclaimerText = EP.meta.disclaimer || '';
    const assetsEnter = fp.assets.enter || 'zoom-pop';
    const textEnter = fp.text.enter || 'fade';
    const rid = region ? region.id : '';
    const title = region ? region.label : '选择区域';
    let content = '';
    let footer = '';
    if (rid === 'brand') {
      const baseContent = section('', [
          field('标题文字', 'titleText', titleText, 'text', 'full'),
          coordField('X', 'titleX'),
          coordField('Y', 'titleY'),
          field('Logo 尺寸', 'logoSize', fp.logo.size || 60, 'number'),
          checkbox('隐藏 Logo', 'logoHidden', !!fp.logo.hidden),
          fileField('上传图片', 'logoUpload', 'full'),
      ]);
      const fontContent = section('标题字体', [
        fontPreviewField('', 'titleFont', fp.title.font || '', '"Noto Serif SC", "Source Han Serif SC", "Songti SC", serif', {
          weight: fp.title.weight || 700,
          fontStyle: fp.title.fontStyle || 'normal',
          decoration: fp.title.decoration || 'none',
          vertical: fp.title.vertical || 'baseline',
        }),
        field('字号', 'titleSize', fp.title.size || 44, 'number'),
        selectField('字重', 'titleWeight', String(fp.title.weight || 700), WEIGHT_OPTIONS),
        styleButtonField('titleWeight', 'titleFontStyle', 'titleDecoration', 'titleVertical', fp.title.weight || 700, fp.title.fontStyle || 'normal', fp.title.decoration || 'none', fp.title.vertical || 'baseline'),
      ]);
      content = tabbedContent(rid, baseContent, fontContent);
    } else if (rid === 'disclaimer') {
      const baseContent = section('右上声明', [
        field('声明文案', 'disclaimerText', disclaimerText, 'text', 'full'),
        coordField('X', 'disclaimerX'),
        coordField('Y', 'disclaimerY'),
        field('缩放', 'disclaimerScale', fp.disclaimer.scale || 1, 'number'),
      ]);
      const fontContent = section('声明字体', [
        fontPreviewField('', 'disclaimerFont', fp.disclaimer.font || '', '"Noto Serif SC", "Source Han Serif SC", "Songti SC", serif', {
          weight: fp.disclaimer.weight || 600,
          fontStyle: fp.disclaimer.fontStyle || 'normal',
          decoration: fp.disclaimer.decoration || 'none',
          vertical: fp.disclaimer.vertical || 'baseline',
        }),
        field('字号', 'disclaimerSize', fp.disclaimer.size || 30, 'number'),
        selectField('字重', 'disclaimerWeight', String(fp.disclaimer.weight || 600), WEIGHT_OPTIONS),
        styleButtonField('disclaimerWeight', 'disclaimerFontStyle', 'disclaimerDecoration', 'disclaimerVertical', fp.disclaimer.weight || 600, fp.disclaimer.fontStyle || 'normal', fp.disclaimer.decoration || 'none', fp.disclaimer.vertical || 'baseline'),
      ]);
      content = tabbedContent(rid, baseContent, fontContent);
    } else if (rid === 'subtitle') {
      const baseContent = section('字幕区域', [
        coordField('X', 'subtitleX'),
        coordField('Y', 'subtitleY'),
        field('缩放', 'subtitleScale', fp.subtitle.scale || 1, 'number'),
        field('高度', 'subtitleHeight', fp.subtitle.height || 220, 'number'),
        checkbox('显示英文', 'showSubtitleEn', EP.visual.showSubtitleEn !== false, 'full'),
        colorField('主字幕色', 'subtitleColor', fp.subtitle.color || 'var(--ink)', '#1c1a16'),
        colorField('副字幕色', 'subtitleSubColor', fp.subtitle.subColor || 'var(--ink-soft)', '#4a4639'),
      ]);
      const fontContent =
        section('中文字幕体', [
          fontPreviewField('', 'subtitleFont', fp.subtitle.font || '', '"Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif', {
            weight: fp.subtitle.weight || 700,
            fontStyle: fp.subtitle.fontStyle || 'normal',
            decoration: fp.subtitle.decoration || 'none',
            vertical: fp.subtitle.vertical || 'baseline',
          }),
          field('字号', 'subtitleSize', fp.subtitle.size || EP.visual.capZhSize || 78, 'number'),
          selectField('字重', 'subtitleWeight', String(fp.subtitle.weight || 700), WEIGHT_OPTIONS),
          styleButtonField('subtitleWeight', 'subtitleFontStyle', 'subtitleDecoration', 'subtitleVertical', fp.subtitle.weight || 700, fp.subtitle.fontStyle || 'normal', fp.subtitle.decoration || 'none', fp.subtitle.vertical || 'baseline'),
        ]) +
        section('英文字幕体', [
          fontPreviewField('', 'subtitleSubFont', fp.subtitle.subFont || '', '"Inter", "Helvetica Neue", system-ui, sans-serif', {
            weight: fp.subtitle.subWeight || 500,
            fontStyle: fp.subtitle.subFontStyle || 'normal',
            decoration: fp.subtitle.subDecoration || 'none',
            vertical: fp.subtitle.subVertical || 'baseline',
          }),
          field('字号', 'subtitleSubSize', fp.subtitle.subSize || EP.visual.capEnSize || 38, 'number'),
          selectField('字重', 'subtitleSubWeight', String(fp.subtitle.subWeight || 500), WEIGHT_OPTIONS),
          styleButtonField('subtitleSubWeight', 'subtitleSubFontStyle', 'subtitleSubDecoration', 'subtitleSubVertical', fp.subtitle.subWeight || 500, fp.subtitle.subFontStyle || 'normal', fp.subtitle.subDecoration || 'none', fp.subtitle.subVertical || 'baseline'),
        ]);
      content = tabbedContent(rid, baseContent, fontContent);
    } else if (rid === 'sketchItem') {
      const item = sketchForRegion(region) || {};
      const pos = ensurePos(item);
      const motion = ensureMotion(item);
      content = section('剪影素材', [
        percentField('X%', 'sketchItemX', pos.x),
        percentField('Y%', 'sketchItemY', pos.y),
        field('尺寸%', 'sketchItemSize', item.size == null ? 30 : item.size, 'number'),
        selectActionField('入场', 'sketchItemEnter', String(motion.enter || 'zoom-pop'), ENTER_OPTIONS, 'preview-region-motion', '重播入场'),
        field('时长 ms', 'sketchItemDuration', motion.duration == null ? 600 : motion.duration, 'number'),
        field('延迟 ms', 'sketchItemDelay', motion.delay == null ? 0 : motion.delay, 'number'),
      ]);
      footer = sketchItemFooter(region);
    } else if (rid === 'overlayItem') {
      const item = overlayForRegion(region) || {};
      const pos = ensurePos(item);
      const style = ensureStyle(item);
      const motion = ensureMotion(item);
      const baseContent = section('文字素材', [
        textareaField('文字', 'overlayItemText', item.text || '', 'full'),
        percentField('X%', 'overlayItemX', pos.x),
        percentField('Y%', 'overlayItemY', pos.y),
        selectField('预设', 'overlayItemPreset', style.preset || 'auto', overlayPresetOptions()),
        selectActionField('入场', 'overlayItemEnter', String(motion.enter || 'fade'), TEXT_ENTER_OPTIONS, 'preview-region-motion', '重播入场'),
        selectField('方向', 'overlayItemFrom', String(motion.from || 'right'), [['left', '左'], ['right', '右'], ['top', '上'], ['bottom', '下']]),
        field('时长 ms', 'overlayItemDuration', motion.duration == null ? 600 : motion.duration, 'number'),
        field('延迟 ms', 'overlayItemDelay', motion.delay == null ? 0 : motion.delay, 'number'),
        field('旋转°', 'overlayItemRotation', style.rotation == null ? 0 : style.rotation, 'number'),
        field('字距 em', 'overlayItemLetterSpacing', style.letterSpacing == null ? 0 : style.letterSpacing, 'number'),
        textField('背景', 'overlayItemBackground', style.background || ''),
        textField('内边距', 'overlayItemPadding', style.padding || ''),
        textField('边框', 'overlayItemBorder', style.border || ''),
        field('圆角', 'overlayItemBorderRadius', style.borderRadius == null ? 0 : style.borderRadius, 'number'),
        textField('阴影', 'overlayItemShadow', style.shadow || '', 'full'),
        selectField('换行', 'overlayItemWhiteSpace', style.whiteSpace || 'nowrap', [['nowrap', '不换行'], ['normal', '自动换行'], ['pre-line', '保留换行']]),
      ]);
      const fontContent = section('文字字体', [
        fontPreviewField('', 'overlayItemFont', style.font || '', '"Noto Sans SC", "PingFang SC", sans-serif', {
          weight: style.weight || 400,
          fontStyle: style.fontStyle || 'normal',
          decoration: style.textDecoration || 'none',
          vertical: style.vertical || 'baseline',
        }),
        field('字号', 'overlayItemSize', style.size || 48, 'number'),
        selectField('字重', 'overlayItemWeight', String(style.weight || 'normal'), [['normal', '默认']].concat(WEIGHT_OPTIONS)),
        styleButtonField('overlayItemWeight', 'overlayItemFontStyle', 'overlayItemDecoration', 'overlayItemVertical', style.weight || 400, style.fontStyle || 'normal', style.textDecoration || 'none', style.vertical || 'baseline'),
        colorField('颜色', 'overlayItemColor', style.color || 'var(--ink)', '#1c1a16'),
      ]);
      content = tabbedContent(rid, baseContent, fontContent);
    } else if (rid === 'asset') {
      content = section('剪影素材默认', [
        coordField('X', 'assetsX'),
        coordField('Y', 'assetsY'),
        field('缩放', 'assetsScale', fp.assets.scale || 1, 'number'),
        field('默认尺寸%', 'assetsSize', fp.assets.size || 30, 'number'),
        selectField('入场', 'assetsEnter', assetsEnter || 'none', ENTER_OPTIONS),
        field('时长 ms', 'assetsDuration', fp.assets.duration || 600, 'number'),
      ]);
    } else if (rid === 'text') {
      const baseContent = section('文字素材', [
        coordField('X', 'textX'),
        coordField('Y', 'textY'),
        field('缩放', 'textScale', fp.text.scale || 1, 'number'),
        selectField('入场', 'textEnter', textEnter || 'none', TEXT_ENTER_OPTIONS),
      ]);
      const fontContent = section('文字字体', [
        fontPreviewField('', 'textFont', fp.text.font || '', '"Noto Sans SC", "PingFang SC", sans-serif', {
          weight: fp.text.weight || 400,
          fontStyle: fp.text.fontStyle || 'normal',
          decoration: fp.text.decoration || 'none',
          vertical: fp.text.vertical || 'baseline',
        }),
        field('字号', 'textSize', fp.text.size || 48, 'number'),
        selectField('字重', 'textWeight', String(fp.text.weight || 'normal'), [['normal', '默认']].concat(WEIGHT_OPTIONS)),
        styleButtonField('textWeight', 'textFontStyle', 'textDecoration', 'textVertical', fp.text.weight || 400, fp.text.fontStyle || 'normal', fp.text.decoration || 'none', fp.text.vertical || 'baseline'),
        colorField('颜色', 'textColor', fp.text.color || 'var(--ink)', '#1c1a16'),
      ]);
      content = tabbedContent(rid, baseContent, fontContent);
    } else {
      content = section('编辑模式', [
        '<div class="fp-edit-field full"><span>提示</span><input type="text" value="点击画面区域打开对应面板，拖动虚线框记录位置" disabled></div>',
      ]);
    }

    panel.innerHTML =
      '<div class="fp-edit-head">' +
      '  <div class="fp-edit-title">' + esc(title) + '</div>' +
      '  <div class="fp-edit-actions">' +
      '    <span class="fp-edit-status-dot saved" data-status-dot title="saved"></span>' +
      '    <button class="fp-edit-btn" data-action="close-panel">关闭</button>' +
      '    <button class="fp-edit-btn" data-action="reset-region">重置</button>' +
      '  </div>' +
      '</div>' +
      '<div class="fp-edit-body">' +
      content +
      '</div>' +
      footer;
    scheduleFontPreviewFit();
  }

  function section(title, fields) {
    return '<section class="fp-edit-section">' + (title ? '<h4>' + esc(title) + '</h4>' : '') + '<div class="fp-edit-grid">' + fields.join('') + '</div></section>';
  }

  function sketchItemFooter(region) {
    const entries = sketchNavEntries();
    if (!region || region.id !== 'sketchItem' || entries.length <= 1) return '';
    const idx = currentSketchNavIndex(region, entries);
    if (idx < 0) return '';
    const prev = idx > 0
      ? '<button type="button" class="fp-edit-btn" data-action="sketch-prev">上一个</button>'
      : '';
    const next = idx < entries.length - 1
      ? '<button type="button" class="fp-edit-btn primary" data-action="sketch-next">下一个</button>'
      : '';
    return '<div class="fp-edit-footer">' +
      (prev || '<span class="fp-edit-footer-spacer"></span>') +
      '<span class="fp-edit-footer-spacer"></span>' +
      (next || '') +
      '</div>';
  }

  function field(label, name, value, type, klass) {
    const attrs = /Scale$/.test(name) ? ' step="0.05" min="0.1" max="5"' : '';
    return '<label class="fp-edit-field ' + (klass || '') + '">' +
      '<span>' + esc(label) + '</span>' +
      '<input data-field="' + name + '" type="' + type + '" value="' + esc(value) + '"' + attrs + '>' +
      '</label>';
  }

  function textField(label, name, value, klass) {
    return '<label class="fp-edit-field ' + (klass || '') + '">' +
      '<span>' + esc(label) + '</span>' +
      '<input data-field="' + name + '" type="text" value="' + esc(value) + '">' +
      '</label>';
  }

  function textareaField(label, name, value, klass) {
    return '<label class="fp-edit-field ' + (klass || '') + '">' +
      '<span>' + esc(label) + '</span>' +
      '<textarea data-field="' + name + '">' + esc(value) + '</textarea>' +
      '</label>';
  }

  function coordField(label, name) {
    return '<label class="fp-edit-field">' +
      '<span>' + esc(label) + '</span>' +
      '<input data-field="' + name + '" data-position="actual" type="number" step="1" value="' + esc(actualFieldValue(name, 0)) + '">' +
      '</label>';
  }

  function percentField(label, name, value) {
    return '<label class="fp-edit-field">' +
      '<span>' + esc(label) + '</span>' +
      '<input data-field="' + name + '" type="number" step="0.1" value="' + esc(num(value, 50)) + '">' +
      '</label>';
  }

  function colorField(label, name, value, fallback) {
    return '<label class="fp-edit-field">' +
      '<span>' + esc(label) + '</span>' +
      '<input data-field="' + name + '" type="color" value="' + esc(cssColorToHex(value, fallback)) + '">' +
      '</label>';
  }

  function fileField(label, name, klass) {
    return '<label class="fp-edit-field ' + (klass || '') + '">' +
      '<span>' + esc(label) + '</span>' +
      '<input data-field="' + name + '" type="file" accept="image/*">' +
      '</label>';
  }

  function checkbox(label, name, checked, klass) {
    return '<label class="fp-edit-field inline ' + (klass || '') + '">' +
      '<input data-field="' + name + '" type="checkbox"' + (checked ? ' checked' : '') + '>' +
      '<span>' + esc(label) + '</span>' +
      '</label>';
  }

  function selectField(label, name, value, options, klass) {
    return '<label class="fp-edit-field ' + (klass || '') + '">' +
      '<span>' + esc(label) + '</span>' +
      '<select data-field="' + name + '">' + selectedOptions(options, value) + '</select>' +
      '</label>';
  }

  function selectActionField(label, name, value, options, action, title, klass) {
    return '<div class="fp-edit-field ' + (klass || '') + '">' +
      '<div class="fp-edit-field-head">' +
      '<span>' + esc(label) + '</span>' +
      '<button type="button" class="fp-edit-mini-action" data-action="' + esc(action) + '" title="' + esc(title) + '" aria-label="' + esc(title) + '"></button>' +
      '</div>' +
      '<select data-field="' + name + '">' + selectedOptions(options, value) + '</select>' +
      '</div>';
  }

  function ensureModalBackdrop() {
    if (modalBackdrop) return modalBackdrop;
    modalBackdrop = document.createElement('div');
    modalBackdrop.className = 'fp-edit-modal-backdrop';
    modalBackdrop.setAttribute('aria-hidden', 'true');
    modalBackdrop.addEventListener('pointerdown', (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (panel) panel.focus();
    });
    modalBackdrop.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
    });
    document.body.appendChild(modalBackdrop);
    document.body.classList.add('fp-edit-modal-open');
    document.addEventListener('keydown', onModalDocumentKeyDown, true);
    return modalBackdrop;
  }

  function removeModalBackdrop() {
    if (!modalBackdrop) return;
    modalBackdrop.remove();
    modalBackdrop = null;
    document.body.classList.remove('fp-edit-modal-open');
    document.removeEventListener('keydown', onModalDocumentKeyDown, true);
  }

  function onModalDocumentKeyDown(event) {
    if (!panel) return;
    if (panel.contains(event.target)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (event.key === 'Escape') closePanel();
  }

  function panelFocusableElements() {
    if (!panel) return [];
    return Array.from(panel.querySelectorAll(
      'button, input, select, textarea, [href], [tabindex]:not([tabindex="-1"])'
    )).filter((el) => !el.disabled && el.offsetParent !== null);
  }

  function onPanelKeyDown(event) {
    event.stopPropagation();
    if (event.key === 'Escape') {
      event.preventDefault();
      closePanel();
      return;
    }
    if (event.key === 'Tab') {
      const items = panelFocusableElements();
      if (!items.length) {
        event.preventDefault();
        panel.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  }

  function onPanelPointerDown(event) {
    if (!panel || (event.button != null && event.button !== 0)) return;
    const head = event.target && event.target.closest ? event.target.closest('.fp-edit-head') : null;
    if (!head || !panel.contains(head)) return;
    if (event.target.closest('.fp-edit-actions, button, input, select, textarea, a, [data-action]')) return;
    event.preventDefault();
    event.stopPropagation();
    const rect = panel.getBoundingClientRect();
    panelDragState = {
      head,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
    };
    head.classList.add('dragging');
    applyPanelPosition({ left: rect.left, top: rect.top });
    window.addEventListener('pointermove', onPanelPointerMove);
    window.addEventListener('pointerup', onPanelPointerUp);
    window.addEventListener('pointercancel', onPanelPointerUp);
  }

  function onPanelPointerMove(event) {
    if (!panelDragState) return;
    event.preventDefault();
    applyPanelPosition({
      left: event.clientX - panelDragState.offsetX,
      top: event.clientY - panelDragState.offsetY,
    });
  }

  function onPanelPointerUp() {
    if (!panelDragState) return;
    if (panelDragState.head) panelDragState.head.classList.remove('dragging');
    panelDragState = null;
    window.removeEventListener('pointermove', onPanelPointerMove);
    window.removeEventListener('pointerup', onPanelPointerUp);
    window.removeEventListener('pointercancel', onPanelPointerUp);
  }

  function openPanel(region) {
    if (!region) return;
    panelRegion = region;
    ensureModalBackdrop();
    if (!panel) {
      panel = document.createElement('aside');
      panel.className = 'fp-edit-panel';
      panel.setAttribute('role', 'dialog');
      panel.setAttribute('aria-modal', 'true');
      panel.setAttribute('aria-label', '控制面板');
      panel.tabIndex = -1;
      document.body.appendChild(panel);
      panel.addEventListener('input', onPanelInput);
      panel.addEventListener('change', onPanelInput);
      panel.addEventListener('click', onPanelClick);
      panel.addEventListener('keydown', onPanelKeyDown);
      panel.addEventListener('pointerdown', onPanelPointerDown);
    }
    renderPanel(region);
    updatePanelLayout();
    panel.focus();
  }

  function closePanel() {
    if (!panel) {
      removeModalBackdrop();
      return;
    }
    onPanelPointerUp();
    panel.removeEventListener('input', onPanelInput);
    panel.removeEventListener('change', onPanelInput);
    panel.removeEventListener('click', onPanelClick);
    panel.removeEventListener('keydown', onPanelKeyDown);
    panel.removeEventListener('pointerdown', onPanelPointerDown);
    panel.remove();
    panel = null;
    panelRegion = null;
    removeModalBackdrop();
  }

  function open() {
    if (editOpen) return;
    editOpen = true;
    ensureInspector();
    syncPositionVars();
    syncLogoState();
    document.body.classList.add('fp-editing');
    if (window.__player && window.__player.refreshVisual) window.__player.refreshVisual();
    notifyParent(true);
    const editBtn = document.getElementById('editBtn');
    if (editBtn) editBtn.dataset.state = 'open';
    if (window.__player && window.__player.pause) window.__player.pause();
    document.addEventListener('mousemove', onDocumentMouseMove);
    window.addEventListener('resize', onViewportChange);
    window.addEventListener('scroll', onViewportChange, true);
  }

  function close() {
    if (!editOpen) return;
    editOpen = false;
    window.removeEventListener('resize', onViewportChange);
    window.removeEventListener('scroll', onViewportChange, true);
    document.removeEventListener('mousemove', onDocumentMouseMove);
    closePanel();
    clearInspector();
    hideGridOverlay();
    document.body.classList.remove('fp-editing');
    if (window.__player && window.__player.refreshVisual) window.__player.refreshVisual();
    notifyParent(false);
    const editBtn = document.getElementById('editBtn');
    if (editBtn) editBtn.dataset.state = 'closed';
  }

  function onPanelInput(event) {
    const target = event.target;
    if (!target || !target.dataset || !target.dataset.field) return;
    if (target.type === 'file') {
      handleLogoUpload(target.files && target.files[0]);
      target.value = '';
      return;
    }
    if (target.dataset.position === 'actual' && event.type === 'input') return;
    updateField(target.dataset.field, target.type === 'checkbox' ? target.checked : target.value, target.type === 'checkbox');
    if (/Font|Weight|Style|Decoration|Vertical/.test(target.dataset.field)) {
      scheduleFontPreviewFit();
    }
  }

  function switchSketchItem(delta) {
    const current = panelRegion && panelRegion.id === 'sketchItem' ? panelRegion : activeRegion;
    if (!current || current.id !== 'sketchItem') return;
    const entries = sketchNavEntries();
    const navIndex = currentSketchNavIndex(current, entries);
    if (navIndex < 0) return;
    const nextEntry = entries[navIndex + delta];
    if (!nextEntry) return;
    if (window.__player && typeof window.__player.pause === 'function') {
      window.__player.pause();
    }
    if (window.__player && typeof window.__player.showBeat === 'function') {
      window.__player.showBeat(nextEntry.beatIndex);
    }
    const nextRegion = {
      id: 'sketchItem',
      label: nextEntry.label,
      itemIndex: nextEntry.assetIndex,
      xField: 'sketchItemX',
      yField: 'sketchItemY',
    };
    refreshRegionElement(nextRegion);
    activeRegion = nextRegion;
    panelRegion = nextRegion;
    openPanel(nextRegion);
    updateInspector(nextRegion);
  }

  function onPanelClick(event) {
    const target = event.target;
    if (!target || !target.closest) return;
    const tabButton = target.closest('[data-action="panel-tab"]');
    if (tabButton && panelRegion) {
      panelTabs[panelRegion.id] = tabButton.dataset.tab || 'base';
      renderPanel(panelRegion);
      updatePanelLayout();
      return;
    }
    const fontButton = target.closest('[data-font-field]');
    if (fontButton) {
      updateField(fontButton.dataset.fontField, fontButton.dataset.fontValue || '', false);
      if (panelRegion) {
        renderPanel(panelRegion);
        updatePanelLayout();
        setStatus('changed');
      }
      return;
    }
    const styleButton = target.closest('[data-font-toggle]');
    if (styleButton) {
      updateField(styleButton.dataset.buttonField, styleButton.dataset.buttonValue || '', false);
      if (panelRegion) {
        renderPanel(panelRegion);
        updatePanelLayout();
        setStatus('changed');
      }
      return;
    }
    const actionTarget = target.closest('[data-action]');
    const action = actionTarget && actionTarget.dataset ? actionTarget.dataset.action : '';
    if (action === 'save') save();
    if (action === 'close-panel') closePanel();
    if (action === 'reset-region') resetRegionToInitial(panelRegion || activeRegion);
    if (action === 'preview-region-motion') previewRegionMotion(panelRegion || activeRegion);
    if (action === 'sketch-prev') switchSketchItem(-1);
    if (action === 'sketch-next') switchSketchItem(1);
  }

  function toggle() {
    if (editOpen) close();
    else open();
  }

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && editOpen && !panel) {
      event.preventDefault();
      close();
      return;
    }
    const tag = (event.target && event.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || (event.target && event.target.isContentEditable)) return;
    if (event.key === 'e' || event.key === 'E') {
      event.preventDefault();
      toggle();
    }
  });

  const editBtn = document.getElementById('editBtn');
  if (editBtn) {
    editBtn.dataset.state = 'closed';
    editBtn.addEventListener('click', () => toggle());
  }

  window.addEventListener('pagehide', () => notifyParent(false));
  syncPositionVars();
  syncLogoState();

  window.__finalPreviewEditMode = {
    open,
    close,
    toggle,
    isOpen: () => editOpen,
    isDirty: () => dirty,
    save,
  };
})();
