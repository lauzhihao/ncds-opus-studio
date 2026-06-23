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
      left: 18px;
      top: 18px;
      bottom: 18px;
      width: 376px;
      z-index: 2147483646;
      display: flex;
      flex-direction: column;
      background: rgba(251, 248, 240, .94);
      color: #1c1a16;
      border: 1px solid rgba(28, 26, 22, .18);
      box-shadow: 0 18px 48px rgba(0, 0, 0, .24);
      font: 13px/1.45 Inter, ui-sans-serif, system-ui, -apple-system, sans-serif;
      backdrop-filter: blur(18px);
    }
    .fp-edit-head {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 14px 14px 12px;
      border-bottom: 1px solid rgba(28, 26, 22, .12);
    }
    .fp-edit-title {
      font-weight: 800;
      font-size: 15px;
      letter-spacing: 0;
      flex: 1;
    }
    .fp-edit-body {
      overflow: auto;
      padding: 12px 14px 18px;
      display: grid;
      gap: 12px;
    }
    .fp-edit-section {
      border: 1px solid rgba(28, 26, 22, .12);
      background: rgba(255, 255, 255, .44);
      padding: 10px;
      display: grid;
      gap: 9px;
    }
    .fp-edit-section h4 {
      margin: 0 0 2px;
      font-size: 12px;
      font-weight: 800;
      color: #b8362a;
      letter-spacing: 0;
    }
    .fp-edit-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .fp-edit-field {
      display: grid;
      gap: 4px;
      min-width: 0;
    }
    .fp-edit-field.full { grid-column: 1 / -1; }
    .fp-edit-field.inline {
      display: flex;
      align-items: center;
      gap: 8px;
      padding-top: 18px;
    }
    .fp-edit-field span {
      color: rgba(28, 26, 22, .68);
      font-size: 11px;
      font-weight: 700;
    }
    .fp-edit-panel input,
    .fp-edit-panel select {
      width: 100%;
      min-width: 0;
      border: 1px solid rgba(28, 26, 22, .18);
      background: rgba(255, 255, 255, .78);
      color: #1c1a16;
      padding: 7px 8px;
      border-radius: 0;
      font: inherit;
      outline: none;
    }
    .fp-edit-panel input:focus,
    .fp-edit-panel select:focus {
      border-color: #b8362a;
      box-shadow: 0 0 0 2px rgba(184, 54, 42, .14);
    }
    .fp-edit-panel input[type="checkbox"] {
      width: 16px;
      height: 16px;
      padding: 0;
    }
    .fp-edit-panel input[type="color"] {
      min-height: 34px;
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
    .fp-edit-status {
      padding: 0 14px 12px;
      min-height: 22px;
      color: rgba(28, 26, 22, .58);
      font-size: 12px;
      font-family: "JetBrains Mono", "SF Mono", Menlo, monospace;
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
    ['none', '无'],
  ];
  const TEXT_ENTER_OPTIONS = [
    ['fade', '淡入'],
    ['zoom-in', '缩放'],
    ['fly-in', '飞入'],
    ['stamp', '印章'],
    ['blur', '虚化'],
    ['none', '无'],
  ];

  let panel = null;
  let dirty = false;

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

  function num(value, fallback) {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  }

  function int(value, fallback) {
    return Math.round(num(value, fallback));
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

  function jobIdFromUrl() {
    if (window.__previewJobId) return window.__previewJobId;
    const m = location.pathname.match(/^\/preview\/([^\/]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function setStatus(text) {
    if (!panel) return;
    const el = panel.querySelector('[data-status]');
    if (el) el.textContent = text || '';
  }

  function markDirty() {
    dirty = true;
    const save = panel && panel.querySelector('[data-action="save"]');
    if (save) save.disabled = false;
    setStatus('changed');
  }

  function apply() {
    if (window.__finalPreviewApply) window.__finalPreviewApply(EP);
    else if (window.__player && window.__player.refreshVisual) window.__player.refreshVisual();
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

  function handleLogoUpload(file) {
    if (!file) return;
    if (!/^image\//.test(file.type || '')) {
      setStatus('logo upload failed');
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const fp = ensureConfig();
      fp.logo.url = String(reader.result || '');
      apply();
      markDirty();
      setStatus('logo changed');
    };
    reader.onerror = () => setStatus('logo upload failed');
    reader.readAsDataURL(file);
  }

  function updateField(name, raw, isCheckbox) {
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
      case 'backgroundFit':
        EP.visual.stage.background.imageFit = String(value);
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

  async function save() {
    const jobId = jobIdFromUrl();
    if (!jobId) {
      setStatus('save failed: missing job id');
      return;
    }
    const saveBtn = panel && panel.querySelector('[data-action="save"]');
    if (saveBtn) saveBtn.disabled = true;
    setStatus('saving...');
    try {
      const res = await fetch('/jobs/' + encodeURIComponent(jobId) + '/episode', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(EP),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status + ': ' + await res.text());
      dirty = false;
      setStatus('saved');
    } catch (err) {
      if (saveBtn) saveBtn.disabled = false;
      setStatus('save failed');
      console.error('edit-mode: save episode failed', err);
    }
  }

  function renderPanel() {
    const fp = ensureConfig();
    const bg = (EP.visual.stage || {}).background || {};
    const titleText = EP.meta.brandTitle || EP.meta.title || '';
    const disclaimerText = EP.meta.disclaimer || '';
    const assetsEnter = fp.assets.enter || 'zoom-pop';
    const textEnter = fp.text.enter || 'fade';

    panel.innerHTML =
      '<div class="fp-edit-head">' +
      '  <div class="fp-edit-title">控制面板</div>' +
      '  <div class="fp-edit-actions">' +
      '    <button class="fp-edit-btn primary" data-action="save" disabled>保存</button>' +
      '    <button class="fp-edit-btn" data-action="close">关闭</button>' +
      '  </div>' +
      '</div>' +
      '<div class="fp-edit-body">' +
      section('标题', [
        field('标题文字', 'titleText', titleText, 'text', 'full'),
        coordField('X', 'titleX'),
        coordField('Y', 'titleY'),
      ]) +
      section('Logo', [
        field('尺寸', 'logoSize', fp.logo.size || 60, 'number'),
        checkbox('隐藏 Logo', 'logoHidden', !!fp.logo.hidden),
        fileField('上传图片', 'logoUpload', 'full'),
      ]) +
      section('右上声明', [
        field('声明文案', 'disclaimerText', disclaimerText, 'text', 'full'),
        coordField('X', 'disclaimerX'),
        coordField('Y', 'disclaimerY'),
      ]) +
      section('背景图', [
        selectField('填充方式', 'backgroundFit', bg.imageFit || 'cover', [
          ['cover', '填满裁切 cover'],
          ['contain', '完整显示 contain'],
          ['fill', '拉伸铺满 fill'],
        ], 'full'),
      ]) +
      section('字幕区域', [
        field('高度', 'subtitleHeight', fp.subtitle.height || 220, 'number'),
        field('字号', 'subtitleSize', fp.subtitle.size || 78, 'number'),
        colorField('主字幕色', 'subtitleColor', fp.subtitle.color || 'var(--ink)', '#1c1a16'),
        colorField('副字幕色', 'subtitleSubColor', fp.subtitle.subColor || 'var(--ink-soft)', '#4a4639'),
      ]) +
      section('剪影素材', [
        coordField('X', 'assetsX'),
        coordField('Y', 'assetsY'),
        field('缩放', 'assetsScale', fp.assets.scale || 1, 'number'),
        field('默认尺寸%', 'assetsSize', fp.assets.size || 30, 'number'),
        selectField('入场', 'assetsEnter', assetsEnter || 'none', ENTER_OPTIONS),
        field('时长 ms', 'assetsDuration', fp.assets.duration || 600, 'number'),
      ]) +
      section('文字素材', [
        coordField('X', 'textX'),
        coordField('Y', 'textY'),
        field('缩放', 'textScale', fp.text.scale || 1, 'number'),
        field('字号', 'textSize', fp.text.size || 48, 'number'),
        colorField('颜色', 'textColor', fp.text.color || 'var(--ink)', '#1c1a16'),
        selectField('入场', 'textEnter', textEnter || 'none', TEXT_ENTER_OPTIONS),
      ]) +
      '</div>' +
      '<div class="fp-edit-status" data-status>按 E 关闭编辑模式</div>';
  }

  function section(title, fields) {
    return '<section class="fp-edit-section"><h4>' + esc(title) + '</h4><div class="fp-edit-grid">' + fields.join('') + '</div></section>';
  }

  function field(label, name, value, type, klass) {
    return '<label class="fp-edit-field ' + (klass || '') + '">' +
      '<span>' + esc(label) + '</span>' +
      '<input data-field="' + name + '" type="' + type + '" value="' + esc(value) + '">' +
      '</label>';
  }

  function coordField(label, name) {
    return '<label class="fp-edit-field">' +
      '<span>' + esc(label) + '</span>' +
      '<input data-field="' + name + '" data-position="actual" type="number" step="1" value="' + esc(actualFieldValue(name, 0)) + '">' +
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

  function open() {
    if (panel) return;
    panel = document.createElement('aside');
    panel.className = 'fp-edit-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', '控制面板');
    renderPanel();
    document.body.appendChild(panel);
    document.body.classList.add('fp-editing');
    notifyParent(true);
    const editBtn = document.getElementById('editBtn');
    if (editBtn) editBtn.dataset.state = 'open';
    if (window.__player && window.__player.pause) window.__player.pause();
    panel.addEventListener('input', onPanelInput);
    panel.addEventListener('change', onPanelInput);
    panel.addEventListener('click', onPanelClick);
  }

  function close() {
    if (!panel) return;
    panel.removeEventListener('input', onPanelInput);
    panel.removeEventListener('change', onPanelInput);
    panel.removeEventListener('click', onPanelClick);
    panel.remove();
    panel = null;
    document.body.classList.remove('fp-editing');
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
  }

  function onPanelClick(event) {
    const target = event.target;
    const action = target && target.dataset ? target.dataset.action : '';
    if (action === 'save') save();
    if (action === 'close') close();
  }

  function toggle() {
    if (panel) close();
    else open();
  }

  document.addEventListener('keydown', (event) => {
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

  window.__finalPreviewEditMode = {
    open,
    close,
    toggle,
    isOpen: () => !!panel,
    isDirty: () => dirty,
    save,
  };
})();
