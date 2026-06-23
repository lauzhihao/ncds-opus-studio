/* bootstrap.js — episode metadata loader + script injector
 *
 * 这是 HTML 唯一直接引入的脚本（除自适应缩放小片段外）。
 * 每一集的 HTML 都引用此脚本，无 per-episode 业务字段；
 * 引擎层（player / overlays）由此脚本按依赖顺序动态注入。
 *
 * 职责：
 *   1. 推算自身 assets 目录（dirname of currentScript.src）
 *   2. fetch episode.json → 暴露 window.EPISODE
 *   3. 把 meta/visual 落到 DOM：<title> / brand-title / disclaimer / body.ken-burns
 *   4. 依赖顺序加载 image-slot.js → overlays.js → player.js
 */
(function () {
  const me = document.currentScript;
  // 绝对 URL 的 dirname，用于 fetch episode.json 与同目录其它脚本
  const dirAbs = me.src.replace(/\/[^\/]+$/, '');
  const studioPreviewAssetsMatch = dirAbs.match(/\/preview\/([^\/]+)\/\.final-preview-assets$/);
  const previewApiBase = studioPreviewAssetsMatch
    ? dirAbs.replace(/\/\.final-preview-assets$/, '')
    : '';
  const previewJobId = studioPreviewAssetsMatch ? decodeURIComponent(studioPreviewAssetsMatch[1]) : '';

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
    const cfg = visual.finalPreview || {};
    if (meta.title) document.title = meta.title;
    const brand = document.getElementById('brandTitle');
    if (brand) brand.textContent = meta.brandTitle || meta.title || '';
    const disc = document.querySelector('.disclaimer');
    if (disc) disc.textContent = meta.disclaimer || '';
    if (visual.kenBurns) document.body.classList.add('ken-burns');
    applyFinalPreviewDom(meta, cfg, dirAbs);
    applyFinalPreviewVars(cfg);
  }

  function toPx(value, fallback) {
    const n = Number(value);
    return Number.isFinite(n) ? n + 'px' : fallback;
  }

  function toUnit(value, fallback) {
    const n = Number(value);
    return Number.isFinite(n) ? String(n) : fallback;
  }

  function setRootVar(name, value) {
    if (value != null && value !== '') document.documentElement.style.setProperty(name, String(value));
  }

  function resolveAssetUrl(raw, dirAbs) {
    if (!raw) return '';
    const text = String(raw);
    if (/^https?:|^\/|^data:/.test(text)) return text;
    return busted(dirAbs + '/' + text.replace(/^\/+/, ''));
  }

  function applyFinalPreviewDom(meta, cfg, dirAbs) {
    const logo = cfg.logo || {};
    const mark = document.querySelector('.brand-mark');
    if (mark) {
      if (!mark.dataset.defaultLogo) mark.dataset.defaultLogo = mark.innerHTML;
      mark.style.display = logo.hidden ? 'none' : '';
      const logoUrl = logo.url || logo.imageFile || meta.logoUrl || '';
      if (logoUrl) {
        mark.innerHTML = '<img alt="" src="' + resolveAssetUrl(logoUrl, dirAbs) + '">';
      } else {
        mark.innerHTML = mark.dataset.defaultLogo;
      }
    }
  }

  function applyFinalPreviewVars(cfg) {
    cfg = cfg || {};
    const title = cfg.title || {};
    const logo = cfg.logo || {};
    const disclaimer = cfg.disclaimer || {};
    const subtitle = cfg.subtitle || {};
    const assets = cfg.assets || {};
    const text = cfg.text || {};

    setRootVar('--brand-x', toPx(title.x, '0px'));
    setRootVar('--brand-y', toPx(title.y, '0px'));
    setRootVar('--logo-size', toPx(logo.size, '60px'));
    setRootVar('--disclaimer-x', toPx(disclaimer.x, '0px'));
    setRootVar('--disclaimer-y', toPx(disclaimer.y, '0px'));
    setRootVar('--asset-x', toPx(assets.x, '0px'));
    setRootVar('--asset-y', toPx(assets.y, '0px'));
    setRootVar('--asset-scale', toUnit(assets.scale, '1'));
    setRootVar('--text-x', toPx(text.x, '0px'));
    setRootVar('--text-y', toPx(text.y, '0px'));
    setRootVar('--text-scale', toUnit(text.scale, '1'));
    setRootVar('--text-size', toPx(text.size, '48px'));
    setRootVar('--text-color', text.color || 'var(--ink)');
    setRootVar('--band-h', toPx(subtitle.height, '220px'));
    setRootVar('--type-cap-zh', toPx(subtitle.size, '78px'));
    setRootVar('--band', 'transparent');
    setRootVar('--band-text', subtitle.color || 'var(--ink)');
    setRootVar('--band-sub', subtitle.subColor || 'var(--ink-soft)');

    document.body.classList.remove('band-dark');
    document.body.classList.add('band-paper');
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

  async function boot() {
    let ep;
    try {
      const epRes = await fetch(busted(dirAbs + '/episode.json'), { cache: 'no-cache' });
      if (!epRes.ok) throw new Error('HTTP ' + epRes.status);
      ep = await epRes.json();
    } catch (err) {
      console.error('bootstrap: fetch episode.json failed', err);
      return;
    }
    window.__previewApiBase = previewApiBase;
    window.__previewJobId = previewJobId;

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
    // __assetsRoot 给 player.js 拼 audio/picture 路径用；相对路径由 HTML 所在目录解析。
    ep.__assetsRoot = '.' + slug + '-assets';
    ep.__slug = slug;
    ep.__ver = VER;          // 暴露给 player.js / overlays.js 给 audio/picture URL 加 cache-bust
    window.__finalPreviewApply = function (nextEp) {
      window.EPISODE = nextEp || window.EPISODE;
      applyStaticDom(window.EPISODE || {});
      if (window.__player && window.__player.refreshVisual) window.__player.refreshVisual();
    };

    injectFontFaces(ep.fonts, dirAbs);
    applyStaticDom(ep);
    ensureMotionCss(dirAbs);

    try {
      await injectScript(dirAbs + '/image-slot.js');
      await injectScript(dirAbs + '/overlays.js');
      await injectScript(dirAbs + '/player.js');
      await injectScript(dirAbs + '/edit-mode.js');
    } catch (err) {
      console.error('bootstrap: script inject failed', err);
    }
  }

  boot();
})();
