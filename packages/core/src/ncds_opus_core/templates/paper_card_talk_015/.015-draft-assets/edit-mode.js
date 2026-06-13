/* edit-mode.js — 浏览器内 overlay 微调编辑器
 *
 * 痛点：作者已经知道 overlay 大概在哪、内容也定了，只想就着浏览器拖一拖、改个颜色、
 * 改个字，然后让磁盘上的 episode.json 跟着变；不想为每次 ±0.5% 都跑一趟 Claude。
 *
 * 交互（按 E 切入 / 切出）：
 *   - 切入时：暂停播放；把当前 scene 的所有 overlay 重新以 edit 模式渲染
 *     （跳过 at.match 延后入场与 enter 动效），叠一层半透明 ring 提示可选
 *   - 鼠标点 overlay → 选中（蓝色 ring）
 *   - 鼠标拖 overlay → 实时改 pos.x/y（按 scene 元素 % 计算）
 *   - 方向键 ±0.5% / Shift+方向键 ±2% / Alt+方向键 ±0.1%（毫毛级）
 *   - "[" / "]" → 上一个 / 下一个 *带 overlay* 的 scene
 *   - Esc → 取消选中（仍在编辑模式）；再 E → 退出编辑模式
 *   - 任何改动塞进 dirty buffer；inspector 提供 Save 按钮，POST 到 edit-server.py
 *
 * 暴露 window.__editMode 给 inspector.jsx：
 *   isActive() / enter() / exit()
 *   getCurrentSceneId() / setSceneId(id) / getSceneIdsWithOverlays()
 *   getSelected() → {sceneId, index, def, merged}
 *   select(sceneId, index) / deselect()
 *   apply(patch)               把 patch 合并到当前选中 overlay（live + dirty）
 *   getDirty()                 列出所有未保存改动
 *   getDirtyCount()
 *   resetSelectedToDisk()      撤回当前 overlay 的本地修改
 *   save() → Promise<{touched}>
 *   onChange(cb) → unsubscribe 把任何状态变化（选中、scene 切换、apply、save）回调出去
 */
(function () {
  if (window.__editMode) return;
  // 没探测到 edit-server（线上纯静态托管），按 E 也只能拖个寂寞，UI 还会盖住正常观看。
  // 在启动层早退；inspector.jsx 见 __editMode 不存在会自己 no-op。
  // bootstrap.js 用 GET /__ping 判定是否启用，比按 hostname 白名单更鲁棒
  // （本机 nginx 反代到 127.0.0.1:3000 + 自定义 host 时白名单会误伤）。
  if (!window.__editServerOk) return;

  const EP = window.EPISODE;
  if (!EP) { console.error('edit-mode: EPISODE missing'); return; }

  const STYLE_CSS = `
    /* 编辑模式下隐掉黑色控制条（.controls） —— 它的功能（播放/快进/录制/速率）
       在编辑场景里都用不上；要导航就按 E 退出编辑模式。
       注意：跳 beat 已经通过 inspector 的场景选择器 + [/] 快捷键覆盖了，
       per-beat 精度的播放控制在编辑期不需要。 */
    body.edit-mode .controls { display: none; }
    /* ── 左侧合并抽屉: 顶部 tabs 切换 Tweaks/Inspector, 同时只显示一个 ──
       触发: hover/focus (临时滑出) 或 .pinned (固定; 选中 overlay 时自动加) */
    .em-drawer {
      position: fixed; left: 0; top: 0;
      height: 100vh; width: 340px;
      z-index: 2147483646;
      display: flex; flex-direction: column;
      background: rgba(250,249,247,.82);
      -webkit-backdrop-filter: blur(24px) saturate(160%);
      backdrop-filter: blur(24px) saturate(160%);
      border-right: .5px solid rgba(0,0,0,.1);
      box-shadow: 0 12px 40px rgba(0,0,0,.18);
      transform: translateX(calc(-100% + 6px));
      transition: transform .25s ease;
      pointer-events: auto;
      font: 11.5px/1.4 ui-sans-serif,system-ui,-apple-system,sans-serif;
      color: #29261b;
    }
    .em-drawer::before {
      content: ""; position: absolute;
      right: 0; top: 0; width: 6px; height: 100%;
      background: linear-gradient(180deg, rgba(10,132,255,.55), rgba(10,132,255,.15));
      pointer-events: none;
    }
    .em-drawer:hover, .em-drawer:focus-within, .em-drawer.pinned {
      transform: translateX(0);
    }
    /* tabs bar */
    .em-drawer-tabs {
      display: flex; gap: 2px;
      padding: 10px 10px 0;
      border-bottom: .5px solid rgba(0,0,0,.08);
      flex: 0 0 auto;
    }
    .em-drawer-tabs button {
      appearance: none; border: 0; background: transparent;
      color: rgba(41,38,27,.55); font: inherit; font-weight: 500;
      padding: 7px 14px;
      border-radius: 8px 8px 0 0;
      cursor: pointer; position: relative;
    }
    .em-drawer-tabs button:hover { color: #29261b; }
    .em-drawer-tabs button.active {
      color: #29261b;
      background: rgba(255,255,255,.55);
    }
    .em-drawer-tabs button.active::after {
      content: ""; position: absolute;
      left: 8px; right: 8px; bottom: -1px; height: 2px;
      background: #0a84ff;
    }
    /* body 区, 装两个 mount; 通过 data-active-tab 控制显示 */
    .em-drawer-body {
      flex: 1 1 0; min-height: 0;
      display: flex; flex-direction: column;
    }
    .em-drawer-body > * {
      flex: 1 1 0; min-height: 0;
      display: flex; flex-direction: column;
    }
    .em-drawer[data-active-tab="tweaks"] #inspector-mount,
    .em-drawer[data-active-tab="inspector"] #__tweaks_mount {
      display: none;
    }
    /* drawer 内的 panel: 重置定位/边角/transform, 让它撑满 mount 容器 */
    .em-drawer .twk-panel {
      position: relative !important;
      left: auto !important; right: auto !important;
      top: auto !important; bottom: auto !important;
      width: 100% !important;
      max-height: 100% !important;
      flex: 1 1 0; min-height: 0;
      border-radius: 0 !important;
      box-shadow: none !important;
      background: transparent !important;
      border: 0 !important;
      transform: none !important;
      opacity: 1 !important;
    }
    /* 抽屉里的 panel 自带 header (Tweaks / Overlay Inspector 标题条) 隐藏掉,
       因为 tabs 已经标识当前面板; 这样空间全留给内容 */
    .em-drawer .twk-hd { display: none !important; }
    /* 字号 ×1.2: 原 panel 11.5px 太小, 整体放大到 14px;
       section heading 原 10px → 12px. 用 !important 覆盖 inline style 里的 fontSize. */
    .em-drawer,
    .em-drawer .twk-panel,
    .em-drawer .twk-panel * { font-size: 14px !important; }
    .em-drawer .twk-sect,
    .em-drawer .twk-sect * { font-size: 12px !important; }
    .em-drawer-tabs button { font-size: 14px !important; }
    /* 字体预览 chip: family name 小标 + 示例字大号, 都覆盖整体 14px override */
    .em-drawer .em-font-chip-label { font-size: 10px !important; }
    .em-drawer .em-font-chip-sample { font-size: 20px !important; line-height: 1.15 !important; }
    /* 关键：正常播放时 .overlay-layer 是 pointer-events:none，事件穿过去。
       编辑模式让 layer 仍透明但每个 overlay 自己接收事件；
       同时强制 opacity:1，覆盖未播放时的 opacity:0 入场起点。 */
    body.edit-mode .scene-overlay:not(.em-replay) {
      pointer-events: auto !important;
      opacity: 1 !important;
      animation: none !important;
      cursor: grab;
      outline: 1px dashed rgba(0,120,255,.35);
      outline-offset: 2px;
      touch-action: none;
    }
    /* .em-replay: 给被预览的单个 overlay 临时解除上面 :not 的 override,
       让 motion class (mo-ov-*/oa-*) 在 edit 模式下也能跑入场动效 */
    body.edit-mode .scene-overlay.em-replay {
      pointer-events: auto !important;
      cursor: grab;
      outline: 2px solid rgba(10,132,255,.7);
      outline-offset: 2px;
    }
    body.edit-mode .scene-overlay:hover { outline: 2px solid rgba(0,120,255,.7); }
    /* hover 图片容器时给手型，提示这块也是可点击/可操作区域 */
    body.edit-mode .scene image-slot:hover { cursor: pointer; }
    body.edit-mode .scene-overlay.em-selected { outline: 2px solid #0a84ff; outline-offset: 3px; box-shadow: 0 0 0 6px rgba(10,132,255,.18); cursor: grabbing; }
    body.edit-mode .em-toast {
      position: fixed; left: 50%; top: 16px; transform: translateX(-50%);
      background: rgba(0,0,0,.78); color: #fff; padding: 6px 12px;
      border-radius: 8px; font: 12px/1.4 ui-sans-serif,system-ui,sans-serif;
      z-index: 2147483647; pointer-events: none;
      transition: opacity .25s; opacity: 1;
    }
  `;
  const styleEl = document.createElement('style');
  styleEl.dataset.editMode = 'true';
  styleEl.textContent = STYLE_CSS;
  document.head.appendChild(styleEl);

  // dirty[sceneId+'#'+index] = patch（深合并的累积）
  const dirty = new Map();
  let active = false;
  let currentSceneId = null;
  let selection = null; // { sceneId, index, el }
  let focusMode = 'scene'; // 'scene' | 'overlay' — Inspector 内部根据这个切显示
  let drawerEl = null;  // 编辑模式专用：把两个 panel mount 都搬进来的左侧抽屉
  const listeners = new Set();
  const notify = () => listeners.forEach((cb) => { try { cb(); } catch (e) { console.error(e); } });

  function toast(msg, ms) {
    const t = document.createElement('div');
    t.className = 'em-toast';
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; }, ms || 1400);
    setTimeout(() => t.remove(), (ms || 1400) + 400);
  }

  function deepMerge(dst, patch) {
    if (!dst || typeof dst !== 'object') return JSON.parse(JSON.stringify(patch));
    if (!patch || typeof patch !== 'object') return patch;
    const out = Array.isArray(dst) ? dst.slice() : Object.assign({}, dst);
    for (const k of Object.keys(patch)) {
      const v = patch[k];
      if (v && typeof v === 'object' && !Array.isArray(v) && out[k] && typeof out[k] === 'object' && !Array.isArray(out[k])) {
        out[k] = deepMerge(out[k], v);
      } else {
        out[k] = v && typeof v === 'object' ? JSON.parse(JSON.stringify(v)) : v;
      }
    }
    return out;
  }

  function sceneIdsWithOverlays() {
    const scenes = EP.scenes || {};
    return Object.keys(scenes).filter((id) => Array.isArray(scenes[id].overlays) && scenes[id].overlays.length > 0);
  }

  function currentBeatSceneId() {
    // 从 player 拿当前 beat 的 scene；没有就退到第一个有 overlay 的 scene
    const beats = (window.__player && window.__player.beats) || [];
    const activeEl = document.querySelector('.scene.active');
    if (activeEl && activeEl.dataset.sceneId) return activeEl.dataset.sceneId;
    if (beats.length) return beats[0].scene;
    return sceneIdsWithOverlays()[0] || null;
  }

  function sceneElOf(sceneId) {
    return (window.__player && window.__player.sceneNodes && window.__player.sceneNodes[sceneId]) || null;
  }

  // 合并后的 overlay 定义 = 原盘 + dirty patch
  function mergedOverlay(sceneId, index) {
    const base = (EP.scenes[sceneId] && EP.scenes[sceneId].overlays || [])[index];
    if (!base) return null;
    const patch = dirty.get(sceneId + '#' + index);
    return patch ? deepMerge(base, patch) : JSON.parse(JSON.stringify(base));
  }

  function renderSceneEdit(sceneId) {
    const sceneEl = sceneElOf(sceneId);
    if (!sceneEl) return;
    const base = (EP.scenes[sceneId] && EP.scenes[sceneId].overlays) || [];
    const merged = base.map((_, i) => mergedOverlay(sceneId, i));
    window.__overlays.renderInto(sceneEl, merged, { edit: true });
    // 标 dirty 节点
    sceneEl.querySelectorAll(':scope > .overlay-layer > .scene-overlay').forEach((el) => {
      const idx = Number(el.dataset.overlayIndex);
      if (dirty.has(sceneId + '#' + idx)) el.classList.add('em-dirty');
    });
    wireOverlayPointers(sceneEl);
    // 重新挂选中态
    if (selection && selection.sceneId === sceneId) {
      const sel = sceneEl.querySelector(
        '.scene-overlay[data-overlay-index="' + selection.index + '"]'
      );
      if (sel) { selection.el = sel; sel.classList.add('em-selected'); }
      else { selection = null; }
    }
  }

  function activateScene(sceneId) {
    if (!sceneId) return;
    // 切到不同 scene 时清掉旧 selection ——
    // inspector 顶部 `EM.getSelected()` 一直返回旧 merged，OverlayFields 持续
    // 渲染旧 overlay 的字段（text / at.match / x / y），受控 input 显示旧值看着像"没清空"。
    if (selection && selection.sceneId !== sceneId) {
      if (selection.el) selection.el.classList.remove('em-selected');
      selection = null;
    }
    const nodes = (window.__player && window.__player.sceneNodes) || {};
    Object.keys(nodes).forEach((id) => nodes[id].classList.toggle('active', id === sceneId));
    currentSceneId = sceneId;
    renderSceneEdit(sceneId);
    notify();
  }

  function select(sceneId, index) {
    if (selection && selection.el) selection.el.classList.remove('em-selected');
    if (sceneId == null) {
      selection = null; notify();
      if (drawerEl) drawerEl.classList.remove('pinned');
      return;
    }
    if (currentSceneId !== sceneId) activateScene(sceneId);
    const sceneEl = sceneElOf(sceneId);
    const el = sceneEl && sceneEl.querySelector(
      '.scene-overlay[data-overlay-index="' + index + '"]'
    );
    if (!el) {
      selection = null; notify();
      if (drawerEl) drawerEl.classList.remove('pinned');
      return;
    }
    el.classList.add('em-selected');
    selection = { sceneId, index, el };
    focusMode = 'overlay';
    notify();
    // 选中 overlay -> 钉住抽屉 + 切到 Inspector tab, 否则 hover 走开就会缩回
    if (drawerEl) {
      drawerEl.classList.add('pinned');
      setDrawerTab('inspector');
    }
  }

  // 点击图片容器 -> 进 scene 模式: 不选 overlay, 抽屉 pin 在 Inspector tab, 显示场景设置
  function selectScene(sid) {
    if (selection && selection.el) selection.el.classList.remove('em-selected');
    selection = null;
    focusMode = 'scene';
    if (sid && currentSceneId !== sid) activateScene(sid);
    notify();
    if (drawerEl) {
      drawerEl.classList.add('pinned');
      setDrawerTab('inspector');
    }
  }

  function apply(patch) {
    if (!selection || !patch) return;
    const key = selection.sceneId + '#' + selection.index;
    const prev = dirty.get(key) || {};
    const next = deepMerge(prev, patch);
    dirty.set(key, next);
    // updateLive 只热改 pos / text / inline style 子集；preset 是 class 上挂的
    // (overlays.js renderInto 里 classNames.push(presetFromObj))，applyStyleObject 跳过
    // preset 字段，所以下拉里选 os-marker → os-stamp 不会立刻变样。这里检测到 preset
    // 切换或 style 类型变（字符串 ↔ 对象 ↔ null）时整 scene 重渲，重渲会按 dirty
    // buffer 自动重挂选中态和 em-dirty 标记。
    const styleSwapsClass = (
      'style' in patch && (
        typeof patch.style === 'string'
        || patch.style === null
        || (patch.style && typeof patch.style === 'object' && 'preset' in patch.style)
      )
    );
    // motion class 也是 renderInto 时挂的, updateLive 不动它. 改 enter/from 必须重渲
    // 整 scene, 否则面板预览看不到新动效, 且新 from 不会落到 oa-fly-XXX class 上.
    const motionSwapsClass = 'motion' in patch && patch.motion && typeof patch.motion === 'object'
      && ('enter' in patch.motion || 'from' in patch.motion);
    if (styleSwapsClass || motionSwapsClass) {
      renderSceneEdit(selection.sceneId);
    } else {
      window.__overlays.updateLive(sceneElOf(selection.sceneId), selection.index, patch);
      selection.el.classList.add('em-dirty');
    }
    notify();
    scheduleAutoSave();
  }

  // 入场动效预览: edit-mode + body:not(.motion-enabled) 两层 CSS 都把 animation 置 none.
  // inline style 带 !important 用 setProperty('animation-name', ..., 'important')
  // 是唯一比 author !important 优先级更高的方式. 动画播完移除 em-replay + 清 inline.
  // 注意 mo-ov-* / oa-* class 名跟 @keyframes 同名, 直接拿 class 当 animation-name.
  function previewMotion(sceneId, index) {
    if (!active) return;
    const sceneEl = sceneElOf(sceneId);
    if (!sceneEl) return;
    const el = sceneEl.querySelector(
      ':scope > .overlay-layer > .scene-overlay[data-overlay-index="' + index + '"]'
    );
    if (!el) return;
    let animName = null;
    for (const cls of el.classList) {
      if (cls.startsWith('mo-ov-') || cls.startsWith('oa-')) { animName = cls; break; }
    }
    if (!animName) return; // 没 motion class 没法 preview
    const merged = mergedOverlay(sceneId, index);
    const m = merged.motion || {};
    const dur = m.duration || 700;
    const delay = m.delay || 0;
    el.classList.add('em-replay');
    el.style.setProperty('animation-name', 'none', 'important');
    void el.offsetWidth;            // 强制 reflow 让动画从 0 开始
    el.style.setProperty('animation-name', animName, 'important');
    el.style.setProperty('animation-duration', dur + 'ms', 'important');
    el.style.setProperty('animation-delay', delay + 'ms', 'important');
    el.style.setProperty('animation-fill-mode', 'forwards', 'important');
    el.style.setProperty('animation-iteration-count', '1', 'important');
    if (el._previewTimer) clearTimeout(el._previewTimer);
    el._previewTimer = setTimeout(() => {
      el.classList.remove('em-replay');
      el.style.removeProperty('animation-name');
      el.style.removeProperty('animation-duration');
      el.style.removeProperty('animation-delay');
      el.style.removeProperty('animation-fill-mode');
      el.style.removeProperty('animation-iteration-count');
      el._previewTimer = null;
    }, delay + dur + 80);
  }

  // auto-save：每次 apply 后 300ms debounce 触发 save()。仅 edit-server 可达时生效。
  // 拖动连发的 patch 会在静止时一次写盘；线上无端点时上层 enter() 已早退，这里只是保险。
  let _autoSaveTimer = null;
  function scheduleAutoSave() {
    if (!window.__editServerOk) return;
    if (_autoSaveTimer) clearTimeout(_autoSaveTimer);
    _autoSaveTimer = setTimeout(() => {
      _autoSaveTimer = null;
      if (dirty.size === 0) return;
      save({ silent: true }).catch((e) => console.warn('[edit-mode] auto-save failed:', e.message));
    }, 300);
  }

  function resetSelectedToDisk() {
    if (!selection) return;
    const key = selection.sceneId + '#' + selection.index;
    if (!dirty.has(key)) return;
    dirty.delete(key);
    // 重新渲染当前 scene 让该 overlay 回到磁盘形态
    const sid = selection.sceneId;
    const idx = selection.index;
    renderSceneEdit(sid);
    select(sid, idx);
  }

  // ── 鼠标拖拽 ─────────────────────────────────────────────
  function wireOverlayPointers(sceneEl) {
    const els = sceneEl.querySelectorAll(':scope > .overlay-layer > .scene-overlay');
    els.forEach((el) => {
      el.addEventListener('pointerdown', onPointerDown);
    });
    // image-slot 点击 -> 弹抽屉 + scene 模式 (落在容器本身, overlay 自己 stopPropagation 不会冒到这里)
    sceneEl.querySelectorAll('image-slot').forEach((slot) => {
      slot.addEventListener('pointerdown', onSlotPointerDown);
    });
  }
  function onSlotPointerDown(e) {
    if (!active) return;
    if (e.button !== 0) return;
    const sceneEl = e.currentTarget.closest('.scene');
    if (!sceneEl) return;
    selectScene(sceneEl.dataset.sceneId);
    e.stopPropagation();
  }

  let drag = null; // { startX, startY, startPosX, startPosY, sceneRect, sceneId, index, el, moved }
  function onPointerDown(e) {
    if (!active) return;
    if (e.button !== 0) return;
    const downEl = e.currentTarget;
    const sceneId = downEl.dataset.sceneId;
    const index = Number(downEl.dataset.overlayIndex);
    const sceneEl = sceneElOf(sceneId);
    if (!sceneEl) return;
    select(sceneId, index);
    // select() 切 scene 时会 renderSceneEdit -> renderInto，原 downEl 已脱离 DOM，
    // 必须用 select 后挂在 selection 上的新节点做 pointer capture。
    const el = (selection && selection.el && selection.el.isConnected) ? selection.el : downEl;
    const m = mergedOverlay(sceneId, index);
    const startPosX = (m.pos && m.pos.x != null) ? m.pos.x : (m.xPct != null ? m.xPct : 50);
    const startPosY = (m.pos && m.pos.y != null) ? m.pos.y : (m.yPct != null ? m.yPct : 50);
    drag = {
      startX: e.clientX, startY: e.clientY,
      startPosX, startPosY,
      sceneRect: sceneEl.getBoundingClientRect(),
      sceneId, index, el, moved: false,
    };
    if (el.isConnected) el.setPointerCapture(e.pointerId);
    e.preventDefault();
    e.stopPropagation();
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp, { once: true });
  }
  function onPointerMove(e) {
    if (!drag) return;
    const dxPct = (e.clientX - drag.startX) / drag.sceneRect.width * 100;
    const dyPct = (e.clientY - drag.startY) / drag.sceneRect.height * 100;
    if (Math.abs(dxPct) + Math.abs(dyPct) < 0.05) return;
    drag.moved = true;
    let nx = drag.startPosX + dxPct;
    let ny = drag.startPosY + dyPct;
    // Shift = 0.5% 网格吸附；默认 0.5% 精度（一位小数显示）
    const step = e.shiftKey ? 5 : 0.5;
    nx = Math.round(nx / step) * step;
    ny = Math.round(ny / step) * step;
    nx = Math.max(0, Math.min(100, nx));
    ny = Math.max(0, Math.min(100, ny));
    apply({ pos: { x: round1(nx), y: round1(ny) } });
  }
  function onPointerUp() {
    window.removeEventListener('pointermove', onPointerMove);
    if (drag && drag.el) {
      try { drag.el.releasePointerCapture && drag.el.releasePointerCapture; } catch (_) {}
    }
    drag = null;
  }
  function round1(n) { return Math.round(n * 10) / 10; }

  // ── 键盘 ─────────────────────────────────────────────
  function isTypingTarget(t) {
    if (!t) return false;
    const tag = (t.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'textarea' || tag === 'select' || t.isContentEditable;
  }

  document.addEventListener('keydown', (e) => {
    // 进 / 出编辑模式（任何上下文，但不抢输入框）
    if ((e.key === 'e' || e.key === 'E') && !isTypingTarget(e.target) && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      active ? exit() : enter();
      return;
    }
    if (!active) return;
    if (isTypingTarget(e.target)) return;

    if (e.key === 'Escape') {
      if (selection) { select(null); return; }
      exit();
      return;
    }

    // Backspace / Delete: 删掉当前选中的 overlay
    if ((e.key === 'Backspace' || e.key === 'Delete') && selection) {
      e.preventDefault();
      const sid = selection.sceneId;
      const idx = selection.index;
      const m = mergedOverlay(sid, idx);
      const preview = (m.text || '').slice(0, 20) || '(空)';
      if (confirm(`删除 overlay #${idx}「${preview}」?`)) {
        deleteOverlay(sid, idx);
      }
      return;
    }

    // scene 翻页
    if (e.key === '[' || e.key === ']') {
      const ids = sceneIdsWithOverlays();
      if (!ids.length) return;
      const cur = ids.indexOf(currentSceneId);
      const next = e.key === ']' ? (cur + 1) % ids.length : (cur - 1 + ids.length) % ids.length;
      activateScene(ids[next]);
      return;
    }

    // 方向键微调（仅当有选中）
    if (selection && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) {
      e.preventDefault();
      const step = e.shiftKey ? 2 : (e.altKey ? 0.1 : 0.5);
      const m = mergedOverlay(selection.sceneId, selection.index);
      let x = (m.pos && m.pos.x != null) ? m.pos.x : (m.xPct != null ? m.xPct : 50);
      let y = (m.pos && m.pos.y != null) ? m.pos.y : (m.yPct != null ? m.yPct : 50);
      if (e.key === 'ArrowLeft')  x -= step;
      if (e.key === 'ArrowRight') x += step;
      if (e.key === 'ArrowUp')    y -= step;
      if (e.key === 'ArrowDown')  y += step;
      apply({ pos: { x: round1(x), y: round1(y) } });
      return;
    }

    // Ctrl/Cmd+S 保存
    if ((e.metaKey || e.ctrlKey) && (e.key === 's' || e.key === 'S')) {
      e.preventDefault();
      save().catch((err) => toast('Save failed: ' + err.message, 3000));
    }
  });

  // 点击空白处反选（点 .scene 而不是 overlay）
  // 关键: 用 mousedown 起点判断, 不能用 click 的 target —— 在抽屉里 input/textarea
  // 内向右拖选文字时 mouseup 会落到抽屉外, click 的 target 变成共同祖先 (body),
  // 闭包到老逻辑会误反选并 unpin 抽屉, 体感"抽屉被关掉了".
  let _mouseDownInPanel = false;
  document.addEventListener('mousedown', (e) => {
    _mouseDownInPanel = !!e.target.closest('.em-drawer, .twk-panel, .em-toast');
  }, true);
  document.addEventListener('click', (e) => {
    if (!active) return;
    if (_mouseDownInPanel) return;
    if (e.target.closest('.scene-overlay')) return;
    if (e.target.closest('.em-drawer')) return;
    if (e.target.closest('.twk-panel')) return;
    if (e.target.closest('.em-toast')) return;
    if (selection) select(null);
  });

  // ── 进 / 出编辑模式 ─────────────────────────────────────
  function enter() {
    if (active) return;
    active = true;
    document.body.classList.add('edit-mode');
    if (window.__player && window.__player.pause) window.__player.pause();
    // 默认 scene = 当前 active；若 active 没 overlay，跳到第一个有 overlay 的
    const startSid = currentBeatSceneId();
    const withOv = sceneIdsWithOverlays();
    const sid = (withOv.indexOf(startSid) >= 0) ? startSid : (withOv[0] || startSid);
    activateScene(sid);
    // 建左侧抽屉: 顶部 tabs 切 Inspector / Tweaks, body 区把两个 mount 都搬进来.
    // mount 是 React root 容器, 整体 reparent 不影响内部 reconciliation.
    if (!drawerEl) {
      drawerEl = document.createElement('div');
      drawerEl.className = 'em-drawer';
      drawerEl.dataset.activeTab = 'inspector';
      const tabsEl = document.createElement('div');
      tabsEl.className = 'em-drawer-tabs';
      for (const [name, label] of [['inspector', 'Inspector'], ['tweaks', 'Tweaks']]) {
        const b = document.createElement('button');
        b.type = 'button'; b.dataset.tab = name; b.textContent = label;
        if (name === 'inspector') b.classList.add('active');
        b.addEventListener('click', () => setDrawerTab(name));
        tabsEl.appendChild(b);
      }
      drawerEl.appendChild(tabsEl);
      const bodyEl = document.createElement('div');
      bodyEl.className = 'em-drawer-body';
      drawerEl.appendChild(bodyEl);
      document.body.appendChild(drawerEl);
      const t = document.getElementById('__tweaks_mount');
      const i = document.getElementById('inspector-mount');
      if (t) bodyEl.appendChild(t);
      if (i) bodyEl.appendChild(i);
    }
    toast('编辑模式  ·  E 退出  ·  [ ] 切场景  ·  方向键微调', 2200);
  }

  function setDrawerTab(name) {
    if (!drawerEl) return;
    drawerEl.dataset.activeTab = name;
    drawerEl.querySelectorAll('.em-drawer-tabs button').forEach((b) => {
      b.classList.toggle('active', b.dataset.tab === name);
    });
  }

  function exit() {
    if (!active) return;
    if (dirty.size > 0) {
      const ok = confirm(`有 ${dirty.size} 项未保存改动，确认退出并丢弃？`);
      if (!ok) return;
      dirty.clear();
    }
    active = false;
    selection = null;
    document.body.classList.remove('edit-mode');
    // 还原 panel mount 回 body, 拆抽屉.
    if (drawerEl) {
      const t = document.getElementById('__tweaks_mount');
      const i = document.getElementById('inspector-mount');
      if (t) document.body.appendChild(t);
      if (i) document.body.appendChild(i);
      drawerEl.remove();
      drawerEl = null;
    }
    // 把当前 scene 的 overlay DOM 从 edit 模式（直显 / 无 data-at-match / 无入场 class）
    // 重渲到正常播放模式 —— at.match / motion 改动才能在 player 继续播放时按预期触发。
    // 必须自己重渲一次：showBeat 内部判断 sceneWasActive=true 就跳过 renderInto，
    // 而当前 scene 一直挂着 .active，所以光靠 showBeat 不会触发重建。
    if (currentSceneId && window.__overlays) {
      const sceneEl = sceneElOf(currentSceneId);
      const def = EP.scenes[currentSceneId];
      if (sceneEl && def) {
        window.__overlays.renderInto(sceneEl, def.overlays || []);
      }
    }
    // 让 player 重新按 onBeat 流程恢复当前 scene
    if (window.__player && window.__player.showBeat) {
      const beats = window.__player.beats || [];
      let i = beats.findIndex((b) => b.scene === currentSceneId);
      if (i < 0) i = 0;
      window.__player.showBeat(i);
    }
    notify();
  }

  // ── 保存 ─────────────────────────────────────
  async function save(opts) {
    const silent = !!(opts && opts.silent);
    if (dirty.size === 0) { if (!silent) toast('没有改动'); return { touched: 0 }; }
    const patches = [];
    dirty.forEach((patch, key) => {
      const [sceneId, idxStr] = key.split('#');
      patches.push({ scene: sceneId, index: Number(idxStr), patch });
    });
    const slug = EP.__slug || (EP.meta && EP.meta.slug);
    const res = await fetch('/__save_overlays', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slug, patches }),
    });
    if (!res.ok) {
      const txt = await res.text().catch(() => res.statusText);
      throw new Error(`HTTP ${res.status}: ${txt}`);
    }
    const body = await res.json();
    // 把 dirty 落到 EP 内存里，清 dirty buffer，重渲当前 scene
    dirty.forEach((patch, key) => {
      const [sceneId, idxStr] = key.split('#');
      const idx = Number(idxStr);
      const ov = EP.scenes[sceneId].overlays[idx];
      EP.scenes[sceneId].overlays[idx] = deepMerge(ov, patch);
    });
    dirty.clear();
    if (currentSceneId) renderSceneEdit(currentSceneId);
    notify();
    if (!silent) toast(`已保存 ${body.touched} 项`);
    return body;
  }

  // 新增一个 overlay：POST /__add_overlay → 服务端 append + 写盘，返回新 index
  // 客户端把同样的 overlay 推进 EP 内存，重渲当前 scene 并选中新项
  // 失败回退（不动 EP），用户看到错误 toast 即可
  async function addOverlay(sceneId, overlay) {
    sceneId = sceneId || currentSceneId;
    if (!sceneId) return null;
    const o = overlay || { text: '新文字', pos: { x: 50, y: 50 } };
    const slug = EP.__slug || (EP.meta && EP.meta.slug);
    try {
      const res = await fetch('/__add_overlay', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slug, scene: sceneId, overlay: o }),
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => res.statusText);
        throw new Error(`HTTP ${res.status}: ${txt}`);
      }
      const body = await res.json();
      const idx = body.index;
      // 同步 EP 内存
      if (!Array.isArray(EP.scenes[sceneId].overlays)) EP.scenes[sceneId].overlays = [];
      EP.scenes[sceneId].overlays.push(JSON.parse(JSON.stringify(o)));
      // 重渲 + 选中
      if (currentSceneId === sceneId) renderSceneEdit(sceneId);
      else activateScene(sceneId);
      select(sceneId, idx);
      return idx;
    } catch (e) {
      toast('新增失败：' + e.message, 2400);
      console.warn('[edit-mode] addOverlay failed:', e);
      return null;
    }
  }

  // 删除一个 overlay: 服务端 pop + 客户端同步 EP + 清掉该 scene 在 dirty buffer 里
  // 的所有 patch (避免后续 save 时 index 错位).
  async function deleteOverlay(sceneId, index) {
    if (!sceneId || !Number.isInteger(index)) return false;
    const slug = EP.__slug || (EP.meta && EP.meta.slug);
    try {
      const res = await fetch('/__del_overlay', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slug, scene: sceneId, index }),
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => res.statusText);
        throw new Error(`HTTP ${res.status}: ${txt}`);
      }
      // 同步 EP 内存
      EP.scenes[sceneId].overlays.splice(index, 1);
      // 清掉该 scene 所有 dirty patch (index 已经平移, 旧 patch 没法对齐)
      for (const k of Array.from(dirty.keys())) {
        if (k.startsWith(sceneId + '#')) dirty.delete(k);
      }
      // 反选 + 重渲
      select(null);
      if (currentSceneId === sceneId) renderSceneEdit(sceneId);
      toast('已删除 overlay #' + index);
      return true;
    } catch (e) {
      toast('删除失败：' + e.message, 2400);
      console.warn('[edit-mode] deleteOverlay failed:', e);
      return false;
    }
  }

  // ── 暴露给 inspector ─────────────────────────────────
  window.__editMode = {
    isActive: () => active,
    enter, exit,
    getCurrentSceneId: () => currentSceneId,
    setSceneId: activateScene,
    getSceneIdsWithOverlays: sceneIdsWithOverlays,
    getSelected: () => {
      if (!selection) return null;
      const merged = mergedOverlay(selection.sceneId, selection.index);
      const base = (EP.scenes[selection.sceneId].overlays || [])[selection.index];
      return { sceneId: selection.sceneId, index: selection.index, def: base, merged };
    },
    select,
    selectScene,
    deleteOverlay,
    previewMotion,
    getFocusMode: () => focusMode,
    deselect: () => select(null),
    apply,
    addOverlay,
    getDirty: () => {
      const out = [];
      dirty.forEach((patch, key) => {
        const [sceneId, idxStr] = key.split('#');
        out.push({ scene: sceneId, index: Number(idxStr), patch });
      });
      return out;
    },
    getDirtyCount: () => dirty.size,
    isOverlayDirty: (sceneId, index) => dirty.has(sceneId + '#' + index),
    resetSelectedToDisk,
    save,
    onChange: (cb) => { listeners.add(cb); return () => listeners.delete(cb); },
    // 给 inspector 拿 beat 上下文（at.match 候选 / 文字预览）
    getBeatsForScene: (sceneId) => ((window.__player && window.__player.beats) || []).filter((b) => b.scene === sceneId),
  };
})();
// reload test ping
