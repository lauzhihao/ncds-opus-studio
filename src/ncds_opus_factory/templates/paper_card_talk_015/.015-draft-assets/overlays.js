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
                duration, easing, delay },
       countdown:{ from: "03:00", interval: 1000, ticks: null, startDelay: 0 },
                // 入场动效结束 startDelay ms 后，textContent 每 interval ms 减 1 秒。
                // ticks=null/缺省 → 一直跳到 0；ticks=N → 只跳 N 次后停。
                // 切 scene 时 layer 被销毁，isConnected 守卫自动停 interval。
                // from 支持 "MM:SS" 或纯整数秒；解析失败则跳过。
       at:{ match: "仅需两千", delay: 200 } }
                // 把 overlay 飞入时机锁到字幕关键词上：当 player 播到某条 beat
                // 且 beat.zh 包含 match 字符串时，overlay 才入场（再过 delay ms）。
                // 没配 at → 沿用 motion.delay 在 scene 切入时直接播。
                // 同一 scene 内只触发一次（首触发后打 data-at-fired），重进 scene
                // 走 renderInto 重建 DOM 自动清状态。
                // player.js 在 showBeat 末尾调用 window.__overlays.onBeat(sceneEl, b)。
   ────────────────────────────────────────────────────────────────── */
(function () {
  const STYLES = [
    // 第一批：风格基础款
    'os-tag-pill', 'os-stamp', 'os-marker', 'os-handwrite',
    'os-typewriter', 'os-callout', 'os-callout-red', 'os-circle-mark',
    // 第二批：印章/标签 + 题字 + 高光 + 气泡（styles.css "第二批 preset"）
    'os-stamp-blue', 'os-sticker-yellow', 'os-bookmark', 'os-tag-square',
    'os-seal-square', 'os-brush-title', 'os-title-stamp',
    'os-neon', 'os-outline-glow', 'os-highlight-yellow',
    'os-quote-pull', 'os-bubble',
  ];

  // 老库 (oa-*) + motion.css 新库 (mo-ov-*) 全部入池；
  // 拼接长度更大 ⇒ hash 取模后跨 scene 的视觉重复率显著下降。
  const ANIMS = [
    // styles.css 老库
    'oa-fly-top', 'oa-fly-bottom', 'oa-fly-left', 'oa-fly-right',
    'oa-fade', 'oa-zoom', 'oa-stamp-hit', 'oa-blur',
    // motion.css 第一批扩展
    'mo-ov-zoom-pop', 'mo-ov-ink-bleed', 'mo-ov-handwrite', 'mo-ov-slide-clip',
    // mo-ov-iris (clip-path circle 遮罩) 在部分浏览器渲染会白屏，已从池子剔除
    'mo-ov-bounce', 'mo-ov-drift-in', 'mo-ov-spin-in',
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

  const STYLE_INLINE_KEYS = ['font', 'size', 'weight', 'color', 'rotation', 'letterSpacing',
            'shadow', 'padding', 'background', 'border', 'borderRadius', 'textDecoration',
            'fontStyle', 'whiteSpace'];

  function hasStyleKeys(s) {
    if (!s || typeof s !== 'object') return false;
    return STYLE_INLINE_KEYS.some(k => s[k] != null);
  }

  // 写 --os-* CSS 变量（而不是直接 el.style.fontFamily=）。preset class 里
  // 也是 --os-* 列表 + base .scene-overlay 用 var() 引用。这样 inline 覆盖单字段
  // 不会清掉 preset 的 padding/background 等结构性视觉。
  function applyStyleObject(el, s) {
    if (s.font)         el.style.setProperty('--os-font', '"' + s.font + '", "Noto Sans SC", sans-serif');
    if (s.size != null) el.style.setProperty('--os-size', s.size + 'px');
    if (s.weight)       el.style.setProperty('--os-weight', String(s.weight));
    if (s.color)        el.style.setProperty('--os-color', s.color);
    if (s.letterSpacing != null) el.style.setProperty('--os-letter-spacing', s.letterSpacing + 'em');
    if (s.shadow)       el.style.setProperty('--os-text-shadow', s.shadow);
    if (s.padding)      el.style.setProperty('--os-padding', s.padding);
    if (s.background)   el.style.setProperty('--os-bg', s.background);
    if (s.border)       el.style.setProperty('--os-border', s.border);
    if (s.borderRadius != null) el.style.setProperty('--os-border-radius', s.borderRadius + 'px');
    if (s.textDecoration) el.style.setProperty('--os-text-decoration', s.textDecoration);
    if (s.fontStyle)    el.style.setProperty('--os-font-style', s.fontStyle);
    if (s.whiteSpace)   el.style.setProperty('--os-white-space', s.whiteSpace);
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

  // edit 模式：跳过 at.match 延后入场（直接显示）+ 跳过 enter 动效（直接落到终态）。
  // 为了让编辑器能拖拽 / 高亮 / 写回，每个 overlay DOM 还会带上
  // data-scene-id + data-overlay-index，定位回 episode.json 的 scenes[id].overlays[index]。
  function renderInto(sceneEl, overlays, opts) {
    const edit = !!(opts && opts.edit);
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

      // style 三种形态：
      //   1) 字符串（"os-marker"）→ 老 preset 走 class
      //   2) {preset:"os-marker", color:"#...", ...} → preset 走 class，其余 inline 覆盖
      //   3) {color, font, size, ...} 纯 inline → 不加 preset class
      //   4) 缺省 / "auto" → 按 seed 哈希随机 preset class
      const classNames = ['scene-overlay'];
      const styleIsObject = o.style && typeof o.style === 'object';
      const presetFromObj = styleIsObject && typeof o.style.preset === 'string' ? o.style.preset : null;
      if (typeof o.style === 'string' && o.style !== 'auto') {
        classNames.push(o.style);
      } else if (presetFromObj) {
        classNames.push(presetFromObj);
      } else if (!hasStyleKeys(o.style)) {
        classNames.push(pick(STYLES, seedBase + '#s'));
      }

      // motion：对象优先；fallback 到旧 animation 字符串；最终随机 oa-*
      let motionClass = null;
      if (o.motion && typeof o.motion === 'object') {
        motionClass = motionToClass(o.motion);
      } else if (typeof o.animation === 'string' && o.animation !== 'auto') {
        motionClass = o.animation;
      }
      if (!motionClass) motionClass = pick(ANIMS, seedBase + '#a');
      // motion class 总是挂上, 让 edit 模式下用 .em-replay 预览能拿到 animation-name.
      // 静默由 CSS 'body.edit-mode .scene-overlay:not(.em-replay) { animation: none !important }' 保证.
      classNames.push(motionClass);

      const el = document.createElement('div');
      el.className = classNames.join(' ');
      el.textContent = o.text || '';
      el.style.left = x + '%';
      el.style.top  = y + '%';
      el.dataset.sceneId = sceneId;
      el.dataset.overlayIndex = String(i);

      // 新 style 对象 → inline（preset 字段会被 applyStyleObject 忽略，只写已知 key）
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

      // at：把入场时机绑到字幕关键词。把 motion class 先摘掉藏到 dataset，
      // 元素 display:none；onBeat 命中 match 时还原 + reflow + 启动 countdown。
      // edit 模式跳过整段，编辑器需要看到所有 overlay 的终态。
      if (!edit && o.at && o.at.match) {
        el.dataset.atMatch = String(o.at.match);
        el.dataset.atDelay = String(o.at.delay || 0);
        el.dataset.pendingMotion = motionClass;
        el.classList.remove(motionClass);
        if (o.countdown && o.countdown.from != null) {
          el.dataset.pendingCountdown = JSON.stringify(o.countdown);
          el.dataset.pendingMotionDur = String((o.motion && o.motion.duration) || 0);
        }
        el.style.display = 'none';
      } else if (!edit && o.countdown && o.countdown.from != null) {
        // 非 at：原行为，入场动效结束后立即倒计时
        const motionDur = (o.motion && o.motion.duration) || 0;
        startCountdown(el, o.countdown, delay + motionDur);
      }
    });

    void layer.offsetHeight;
  }

  // player 在 showBeat 末尾调用：扫该 scene 内所有未触发的 at-overlay，beat.zh 含 match 则激活
  // beatMs（可选）= 当前 beat TTS 时长。提供时按 keyword 在 zh 里的字符位置自动算
  // overlay 入场 delay，让 overlay 跟 TTS 读到关键词那一刻同步飞入。
  function onBeat(sceneEl, beat, beatMs) {
    if (!sceneEl || !beat || !beat.zh) return;
    const els = sceneEl.querySelectorAll('[data-at-match]:not([data-at-fired])');
    els.forEach((el) => {
      const m = el.dataset.atMatch;
      if (!m) return;
      const kpos = beat.zh.indexOf(m);
      if (kpos < 0) return;
      el.dataset.atFired = '1';
      // 自动 delay：(关键词起始位置 / 字符总数) × beat 时长 - lead（提前量，看着像同步起飞）
      // 再叠加显式 at.delay 做微调
      const explicit = Number(el.dataset.atDelay || 0);
      const lead = 200;
      let autoDelay = 0;
      if (beatMs && beat.zh.length > 0) {
        autoDelay = Math.max(0, (kpos / beat.zh.length) * beatMs - lead);
      }
      const delay = Math.round(autoDelay + explicit);
      const motionClass = el.dataset.pendingMotion;
      el.style.display = '';
      el.style.setProperty('--oa-delay', delay + 'ms');
      void el.offsetHeight; // reflow 强制重启 keyframes
      if (motionClass) el.classList.add(motionClass);
      if (el.dataset.pendingCountdown) {
        try {
          const cfg = JSON.parse(el.dataset.pendingCountdown);
          const motionDur = Number(el.dataset.pendingMotionDur || 0);
          startCountdown(el, cfg, delay + motionDur);
        } catch (_) { /* ignore */ }
      }
    });
  }

  // 解析 "MM:SS" 或纯整数秒；返回 { total, format(sec) }，解析失败返回 null
  function parseTimer(from) {
    const str = String(from);
    if (str.indexOf(':') >= 0) {
      const parts = str.split(':').map((n) => parseInt(n, 10));
      if (parts.some(isNaN)) return null;
      const total = parts[0] * 60 + parts[1];
      return {
        total,
        format: (sec) => String(Math.floor(sec / 60)).padStart(2, '0') + ':' + String(sec % 60).padStart(2, '0'),
      };
    }
    const n = parseInt(str, 10);
    if (isNaN(n)) return null;
    return { total: n, format: (sec) => String(sec) };
  }

  function startCountdown(el, cfg, baseDelay) {
    const t = parseTimer(cfg.from);
    if (!t) return;
    // ticks 缺省（null/undefined）→ 一直跳到 remaining 归零；显式给数字才限步
    const ticks = (cfg.ticks == null) ? Infinity : Math.max(1, Number(cfg.ticks));
    const interval = Math.max(60, Number(cfg.interval || 500));
    const startDelay = Math.max(0, Number(cfg.startDelay || 0));

    let remaining = t.total;
    el.textContent = t.format(remaining);

    setTimeout(() => {
      if (!el.isConnected) return;
      let n = 0;
      const id = setInterval(() => {
        if (!el.isConnected) { clearInterval(id); return; }
        remaining = Math.max(0, remaining - 1);
        el.textContent = t.format(remaining);
        n += 1;
        if (n >= ticks || remaining <= 0) clearInterval(id);
      }, interval);
    }, baseDelay + startDelay);
  }

  function clear(sceneEl) {
    sceneEl.querySelectorAll(':scope > .overlay-layer').forEach((e) => e.remove());
  }

  // 编辑模式 60fps 拖动用：按 data-* 选中一个已渲染的 overlay 节点，原地改 pos / text / style，
  // 不动 class / motion，不重建 DOM。patch 形态与 episode.json 里的 overlay 子集一致。
  function updateLive(sceneEl, index, patch) {
    if (!sceneEl || !patch) return;
    const el = sceneEl.querySelector(
      ':scope > .overlay-layer > .scene-overlay[data-overlay-index="' + index + '"]'
    );
    if (!el) return;
    if (patch.pos) {
      if (patch.pos.x != null) el.style.left = patch.pos.x + '%';
      if (patch.pos.y != null) el.style.top  = patch.pos.y + '%';
    }
    if (patch.text != null) el.textContent = patch.text;
    if (patch.style && typeof patch.style === 'object') applyStyleObject(el, patch.style);
  }

  window.__overlays = { renderInto, onBeat, clear, updateLive, STYLES, ANIMS };
})();
