/* ──────────────────────────────────────────────────────────────────
   场景文字 overlay 引擎
   - 把 SCENES[id].overlays 渲染成绝对定位 div，叠在中央卡片上
   - 字体字号 = STYLES（os-* 前缀），入场动效 = ANIMS（oa-* 前缀）或 mo-ov-*
   - 切到新 scene 时整批重建 DOM，强制重放 CSS keyframes

   schema（两种格式都支持）：

   旧扁平格式：
     { text, xPct, yPct, style: "os-stamp", animation: "oa-fade",
       delay, rotate, size }

   新对象格式：
     { text,
       pos:   { x, y },
       style: { font, size, weight, color, rotation, letterSpacing,
                shadow, padding, background, border, borderRadius },
       motion:{ enter: "fly-in"|"fade"|"zoom-in"|"stamp"|"blur"
                       |"ink-bleed"|"typewriter"|"handwrite"|"slide-clip"
                       |"iris"|"glitch"|"bounce"|"drift-in"|"zoom-pop",
                from: "left"|"right"|"top"|"bottom",  // 仅 fly-in 用
                duration, easing, delay } }
   ────────────────────────────────────────────────────────────────── */
(function () {
  const STYLES = [
    'os-tag-pill', 'os-stamp', 'os-marker', 'os-handwrite',
    'os-typewriter', 'os-callout', 'os-callout-red', 'os-circle-mark',
  ];

  // 老库 (oa-*) + motion.css 新库 (mo-ov-*) 全部入池；
  // 拼接长度更大 ⇒ hash 取模后跨 scene 的视觉重复率显著下降。
  const ANIMS = [
    // styles.css 老库
    'oa-fly-top', 'oa-fly-bottom', 'oa-fly-left', 'oa-fly-right',
    'oa-fade', 'oa-zoom', 'oa-stamp-hit', 'oa-blur',
    // motion.css 第一批扩展
    'mo-ov-zoom-pop', 'mo-ov-ink-bleed', 'mo-ov-handwrite', 'mo-ov-slide-clip',
    'mo-ov-iris', 'mo-ov-bounce', 'mo-ov-drift-in', 'mo-ov-spin-in',
    'mo-ov-drop-in', 'mo-ov-unfold',
    // motion.css 第二批扩展（PPT 风：弹性 / 翻面 / 散光 / 浮起 / 高光扫光）
    'mo-ov-letter-spread', 'mo-ov-elastic-pop', 'mo-ov-tilt-in',
    'mo-ov-fold-down', 'mo-ov-blur-pulse', 'mo-ov-rise-glow',
    'mo-ov-shimmer-sweep',
  ];

  // motion.enter → CSS class 映射
  // oa-* 是 styles.css 已有的老库；mo-ov-* 是 motion.css 新库
  function motionToClass(motion) {
    if (!motion || !motion.enter) return null;
    const e = motion.enter;
    if (e === 'fly-in' && motion.from) {
      const dir = ({ left: 'left', right: 'right', top: 'top', up: 'top', bottom: 'bottom', down: 'bottom' })[motion.from] || 'right';
      return 'oa-fly-' + dir;
    }
    // 老库映射
    const oa = { fade: 'oa-fade', 'zoom-in': 'oa-zoom', stamp: 'oa-stamp-hit', blur: 'oa-blur' }[e];
    if (oa) return oa;
    // 否则视作新库
    return 'mo-ov-' + e;
  }

  function hasStyleKeys(s) {
    if (!s || typeof s !== 'object') return false;
    return ['font', 'size', 'weight', 'color', 'rotation', 'letterSpacing',
            'shadow', 'padding', 'background', 'border', 'borderRadius', 'textDecoration', 'whiteSpace'].some(k => s[k] != null);
  }

  function applyStyleObject(el, s) {
    if (s.font)         el.style.fontFamily = '"' + s.font + '", "Noto Sans SC", sans-serif';
    if (s.size != null) el.style.fontSize = s.size + 'px';
    if (s.weight)       el.style.fontWeight = s.weight;
    if (s.color)        el.style.color = s.color;
    if (s.letterSpacing != null) el.style.letterSpacing = s.letterSpacing + 'em';
    if (s.shadow)       el.style.textShadow = s.shadow;
    if (s.padding)      el.style.padding = s.padding;
    if (s.background)   el.style.background = s.background;
    if (s.border)       el.style.border = s.border;
    if (s.borderRadius != null) el.style.borderRadius = s.borderRadius + 'px';
    if (s.textDecoration) el.style.textDecoration = s.textDecoration;
    if (s.whiteSpace) el.style.whiteSpace = s.whiteSpace;  // 支持 pre-line 让 text 里的 \n 渲染换行
    if (s.rotation != null) el.style.setProperty('--os-rotate', s.rotation + 'deg');
  }

  // 简单确定性哈希：sceneId+index → 32-bit int
  function hash(s) {
    let h = 2166136261 >>> 0;
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }
  function pick(arr, seed) {
    return arr[hash(seed) % arr.length];
  }

  function renderInto(sceneEl, overlays) {
    sceneEl.querySelectorAll(':scope > .overlay-layer').forEach((e) => e.remove());
    if (!Array.isArray(overlays) || overlays.length === 0) return;

    const layer = document.createElement('div');
    layer.className = 'overlay-layer';
    sceneEl.appendChild(layer);

    const sceneId = (sceneEl.id || sceneEl.dataset.sceneId || 'scene').toString();

    overlays.forEach((o, i) => {
      const seedBase = sceneId + ':' + i + ':' + (o.text || '');

      // 位置：pos.{x,y} 优先；fallback 到 xPct/yPct；最终默认 50/50
      const x = (o.pos && o.pos.x != null) ? o.pos.x : (o.xPct != null ? o.xPct : 50);
      const y = (o.pos && o.pos.y != null) ? o.pos.y : (o.yPct != null ? o.yPct : 50);

      // style：字符串走旧 class；对象走 inline；缺省走随机 os-*
      const classNames = ['scene-overlay'];
      if (typeof o.style === 'string' && o.style !== 'auto') {
        classNames.push(o.style);
      } else if (!hasStyleKeys(o.style)) {
        classNames.push(pick(STYLES, seedBase + '#s'));
      }
      // 新对象 style 不加 os-* class，直接 inline 写在下方

      // motion：对象优先；fallback 到旧 animation 字符串；最终随机 oa-*
      let motionClass = null;
      if (o.motion && typeof o.motion === 'object') {
        motionClass = motionToClass(o.motion);
      } else if (typeof o.animation === 'string' && o.animation !== 'auto') {
        motionClass = o.animation;
      }
      if (!motionClass) motionClass = pick(ANIMS, seedBase + '#a');
      classNames.push(motionClass);

      const el = document.createElement('div');
      el.className = classNames.join(' ');
      el.textContent = o.text || '';
      el.style.left = x + '%';
      el.style.top  = y + '%';

      // 新 style 对象 → inline
      if (hasStyleKeys(o.style)) applyStyleObject(el, o.style);

      // 兼容旧扁平字段（仅在新对象未覆盖时生效）
      if (o.size != null && !el.style.fontSize) el.style.setProperty('--os-size', o.size + 'px');
      if (o.rotate != null && !el.style.getPropertyValue('--os-rotate')) {
        el.style.setProperty('--os-rotate', o.rotate + 'deg');
      }

      // delay（新优先；旧 fallback；最终按 index 阶梯）
      const delay = (o.motion && o.motion.delay != null)
                     ? o.motion.delay
                     : (o.delay != null ? o.delay : (i * 180));
      el.style.setProperty('--oa-delay', delay + 'ms');

      // duration / easing（新 motion 才有）
      if (o.motion) {
        if (o.motion.duration) el.style.setProperty('--motion-ov-duration', o.motion.duration + 'ms');
        if (o.motion.easing)   el.style.setProperty('--motion-ov-easing', o.motion.easing);
      }

      layer.appendChild(el);
    });

    void layer.offsetHeight;
  }

  function clear(sceneEl) {
    sceneEl.querySelectorAll(':scope > .overlay-layer').forEach((e) => e.remove());
  }

  window.__overlays = { renderInto, clear, STYLES, ANIMS };
})();
