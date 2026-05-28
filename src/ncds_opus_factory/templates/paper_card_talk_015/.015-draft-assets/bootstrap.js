/* bootstrap.js — episode metadata loader + script injector
 *
 * 这是 HTML 唯一直接引入的脚本（除自适应缩放小片段外）。
 * 每一集的 HTML 都引用此脚本，无 per-episode 业务字段；
 * 引擎层（player / overlays / tweaks-*）由此脚本按依赖顺序动态注入。
 *
 * 职责：
 *   1. 推算自身 assets 目录（dirname of currentScript.src）
 *   2. fetch episode.json → 暴露 window.EPISODE
 *   3. 把 meta/visual 落到 DOM：<title> / brand-title / disclaimer / body.ken-burns
 *   4. 依赖顺序加载 image-slot.js → overlays.js → player.js → tweaks-panel.jsx → tweaks.jsx
 */
(function () {
  const me = document.currentScript;
  // 绝对 URL 的 dirname，用于 fetch episode.json 与同目录其它脚本
  const dirAbs = me.src.replace(/\/[^\/]+$/, '');

  // Cache-bust 版本号：ncds.cc 的 nginx vhost 给 .css/.js 设了 immutable+30d，
  // 预览阶段无法让用户每次都硬刷。bootstrap.js 给所有子资源 URL 附加
  // ?v=<秒级时间戳>，确保每次加载都跨过 immutable cache 拿新版。
  // 唯一例外：bootstrap.js 自己（被 HTML 静态引用）和 HTML，仍受 immutable
  // 影响——但只要它们不变（罕见），其余 css/js/jsx/json/font 都新鲜。
  const VER = Math.floor(Date.now() / 1000);
  function busted(url) {
    return url + (url.indexOf('?') >= 0 ? '&' : '?') + 'v=' + VER;
  }

  function slugFromUrl(u) {
    const m = u.match(/\.([^\/]+)-assets$/);
    return m ? m[1] : null;
  }

  function applyStaticDom(ep) {
    const meta = ep.meta || {};
    const visual = ep.visual || {};
    if (meta.title) document.title = meta.title;
    const brand = document.getElementById('brandTitle');
    if (brand) brand.textContent = meta.brandTitle || meta.title || '';
    const disc = document.querySelector('.disclaimer');
    if (disc) disc.textContent = meta.disclaimer || '';
    if (visual.kenBurns) document.body.classList.add('ken-burns');
  }

  function ensureMotionCss(dirAbs) {
    if (document.querySelector('link[data-motion-css]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = busted(dirAbs + '/motion.css');
    link.dataset.motionCss = 'true';
    document.head.appendChild(link);
  }

  // 自定义字体注入：从 episode.json.fonts[] 生成 @font-face；
  // 路径相对 .{slug}-assets 目录（如 "fonts/chapter.woff2"），也可填绝对 URL
  function injectFontFaces(fonts, dirAbs) {
    if (!Array.isArray(fonts) || fonts.length === 0) return;
    const css = fonts.map((f) => {
      if (!f || !f.family || !f.src) return '';
      // 字体也带 bust：woff2 改了名 / 内容变了 都能立刻生效（绝对 URL 不动）
      const rawUrl = /^https?:|^\/|^data:/.test(f.src) ? f.src : (dirAbs + '/' + f.src);
      const url = /^https?:|^data:/.test(rawUrl) ? rawUrl : busted(rawUrl);
      const fmt = f.format || 'woff2';
      return [
        '@font-face {',
        '  font-family: "' + f.family + '";',
        '  src: url("' + url + '") format("' + fmt + '");',
        '  font-weight: ' + (f.weight || 'normal') + ';',
        '  font-style: '  + (f.style  || 'normal') + ';',
        '  font-display: ' + (f.display || 'swap') + ';',
        '}',
      ].join('\n');
    }).filter(Boolean).join('\n\n');
    if (!css) return;
    const style = document.createElement('style');
    style.dataset.fontFaces = 'true';
    style.textContent = css;
    document.head.appendChild(style);
  }

  function injectScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = busted(src);
      s.onload = () => resolve();
      s.onerror = () => reject(new Error('inject failed: ' + src));
      document.body.appendChild(s);
    });
  }

  // type="text/babel" 的脚本若由 createElement 动态注入，浏览器会忽略且 Babel
  // standalone 默认只在 DOMContentLoaded 扫描一次。所以我们手动 fetch + 转译 + 内联挂载。
  async function injectBabel(src) {
    const code = await fetch(busted(src), { cache: 'no-cache' }).then(r => r.text());
    if (!window.Babel) throw new Error('Babel standalone not loaded');
    const out = window.Babel.transform(code, {
      presets: ['react'],
      sourceMaps: 'inline',
      filename: src,
    }).code;
    const s = document.createElement('script');
    s.textContent = out;
    document.body.appendChild(s);
  }

  // 探测 edit-server 是否在本机/反代后可达：可达 → 启用 Tweaks/Inspector/编辑模式 +
  // 落盘 + 热重载；不可达（线上 ncds.cc 静态托管 / 公网纯只读）→ 下游全部静默 no-op。
  // 替代了原先按 hostname 白名单（localhost/127.0.0.1）的判断 —— 那条规则在
  // 本机 nginx 反代到 3000 + 自定义 host 的情况下会误伤。
  async function pingEditServer() {
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 1500);
      const res = await fetch('/__ping', { signal: ctrl.signal, cache: 'no-cache' });
      clearTimeout(timer);
      return res.ok;
    } catch (_) {
      return false;
    }
  }

  async function boot() {
    let ep, editServerOk;
    try {
      const [epRes, pingOk] = await Promise.all([
        fetch(busted(dirAbs + '/episode.json'), { cache: 'no-cache' }),
        pingEditServer(),
      ]);
      if (!epRes.ok) throw new Error('HTTP ' + epRes.status);
      ep = await epRes.json();
      editServerOk = pingOk;
    } catch (err) {
      console.error('bootstrap: fetch episode.json failed', err);
      return;
    }
    window.__editServerOk = editServerOk;

    window.EPISODE = ep;
    // URL 推出的 slug 是磁盘实际目录名（唯一可信来源 — picture/audio 都在这下面）。
    // episode.meta.slug 仅作 fallback：如果两者冲突，meta.slug 输，因为听 meta.slug
    // 会让 picture/audio 路径指向不存在的目录、全员 404，错得无声无息。
    const urlSlug = slugFromUrl(dirAbs);
    const metaSlug = ep.meta && ep.meta.slug;
    if (urlSlug && metaSlug && urlSlug !== metaSlug) {
      console.warn(
        'bootstrap: slug mismatch — directory says "' + urlSlug +
        '", episode.meta.slug says "' + metaSlug +
        '". Using URL-derived "' + urlSlug + '". 把 meta.slug 改成 "' + urlSlug + '" 消除告警。'
      );
    }
    const slug = urlSlug || metaSlug;
    // __assetsRoot 给 player.js 拼 audio/picture 路径用；保留相对路径以兼容 render.mjs 与 puppeteer base
    ep.__assetsRoot = '.' + slug + '-assets';
    ep.__slug = slug;
    ep.__ver = VER;          // 暴露给 player.js / overlays.js 给 audio/picture URL 加 cache-bust

    injectFontFaces(ep.fonts, dirAbs);
    applyStaticDom(ep);
    ensureMotionCss(dirAbs);

    try {
      // 开发期热重载（仅 127.0.0.1/localhost 下生效，线上 ncds.cc 直接 no-op）。
      // 优先于其它脚本注入，这样后续编译失败也能在改完代码后自动 reload 看到结果。
      await injectScript(dirAbs + '/dev-reload.js').catch(() => {});
      await injectScript(dirAbs + '/image-slot.js');
      await injectScript(dirAbs + '/overlays.js');
      await injectScript(dirAbs + '/player.js');
      await injectBabel(dirAbs + '/tweaks-panel.jsx');
      await injectBabel(dirAbs + '/tweaks.jsx');
      // 编辑模式：vanilla JS 控制器 + React inspector 面板。
      // 必须在 player + tweaks-panel 之后注入：编辑模式要读 __player.sceneNodes，
      // inspector 要用 tweaks-panel 暴露的 TweaksPanel/TweakSection/... 全局组件。
      await injectScript(dirAbs + '/edit-mode.js');
      await injectBabel(dirAbs + '/inspector.jsx');
    } catch (err) {
      console.error('bootstrap: script inject failed', err);
    }
  }

  boot();
})();
