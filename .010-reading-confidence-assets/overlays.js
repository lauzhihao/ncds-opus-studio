/* ──────────────────────────────────────────────────────────────────
   场景文字 overlay 引擎
   - 把 SCENES[id].overlays 渲染成绝对定位 div，叠在中央卡片上
   - 字体字号 = STYLES（os-* 前缀），入场动效 = ANIMS（oa-* 前缀）
   - 每个 overlay 可显式指定 style / animation，未指定则按 sceneId+index
     哈希到一个固定挑选（同一场景每次刷新视觉一致）
   - overlay 是 .scene 的子节点，跟随父级 opacity / Ken Burns transform
   - 切到新 scene 时整批重建 DOM，强制重放 CSS keyframes

   数据形状（在 beats.js 的 SCENES[id] 上加 overlays: [...]）：
     {
       text: "稳定",          // 必填
       xPct: 50,              // 横向位置，相对 .scene 0-100；默认 50
       yPct: 30,              // 纵向位置，相对 .scene 0-100；默认 50
       style: "auto"|"<class>", // 字体字号风格，见 STYLES；默认 auto
       animation: "auto"|"<class>", // 入场动效，见 ANIMS；默认 auto
       delay: 200,            // 入场延迟 ms；默认按 index 阶梯（i × 180）
       size: 56,              // 可选，字号 px；不填走 style 自带
       rotate: -3,            // 可选，整体旋转 deg
     }
   ────────────────────────────────────────────────────────────────── */
(function () {
  const STYLES = [
    'os-tag-pill',     // 小号白底圆角徽章 + 黑色衬线字
    'os-stamp',        // 红色印章感：圆角矩形 + 红色粗字 + 轻微旋转
    'os-marker',       // 黑色字 + 红色 highlighter 横条（marker 划重点感）
    'os-handwrite',    // Noto Serif SC + 轻微倾斜，像手写卡片
    'os-typewriter',   // JetBrains Mono + 灰底便利贴
    'os-callout',      // 大号衬线 + 细下划线
    'os-callout-red',  // 同上但红墨水
    'os-circle-mark',  // 红色 marker 圈出来（::before 椭圆边框）
  ];

  const ANIMS = [
    'oa-fly-top',      // 从上方掉下
    'oa-fly-bottom',   // 从下方升上来
    'oa-fly-left',     // 从左飞入
    'oa-fly-right',    // 从右飞入
    'oa-fade',         // 纯渐入
    'oa-zoom',         // 缩放 0.6 → 1 + 渐入
    'oa-stamp-hit',    // 缩放 1.4→1 + 反向旋转，"啪"地盖章感
    'oa-blur',         // translateY + blur 渐入
  ];

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
    // 先清掉旧的（同一 scene 多次激活时强制重放动效）
    sceneEl.querySelectorAll(':scope > .overlay-layer').forEach((e) => e.remove());
    if (!Array.isArray(overlays) || overlays.length === 0) return;

    const layer = document.createElement('div');
    layer.className = 'overlay-layer';
    sceneEl.appendChild(layer);

    const sceneId = (sceneEl.id || sceneEl.dataset.sceneId || 'scene').toString();

    overlays.forEach((o, i) => {
      const seedBase = sceneId + ':' + i + ':' + (o.text || '');
      const style = (o.style && o.style !== 'auto') ? o.style : pick(STYLES, seedBase + '#s');
      const anim  = (o.animation && o.animation !== 'auto') ? o.animation : pick(ANIMS, seedBase + '#a');

      const el = document.createElement('div');
      el.className = 'scene-overlay ' + style + ' ' + anim;
      el.textContent = o.text || '';
      el.style.left = (o.xPct != null ? o.xPct : 50) + '%';
      el.style.top  = (o.yPct != null ? o.yPct : 50) + '%';

      const delay = (o.delay != null) ? o.delay : (i * 180);
      el.style.setProperty('--oa-delay', delay + 'ms');

      if (o.size != null) el.style.setProperty('--os-size', o.size + 'px');
      if (o.rotate != null) el.style.setProperty('--os-rotate', o.rotate + 'deg');

      layer.appendChild(el);
    });

    // 强制 reflow 让新元素的入场动效从头播
    void layer.offsetHeight;
  }

  function clear(sceneEl) {
    sceneEl.querySelectorAll(':scope > .overlay-layer').forEach((e) => e.remove());
  }

  window.__overlays = {
    renderInto,
    clear,
    STYLES,
    ANIMS,
  };
})();
