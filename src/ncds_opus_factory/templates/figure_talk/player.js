/* 吴道子 · 剪影成片播放器
 *
 * 逐句切换:居中实心剪影(Ken Burns 缓动)+ 关键词图标飞入 + 字幕 + 配音。
 * 录屏协议对齐 render.mjs(照搬 stickman player_stick.js 的时序契约):
 *   - window.__player.startRecordingPlayback()  录屏器调它开播
 *   - 放完给 body 加 class 'ending'(render.mjs 监听它判断片子结束)
 *   - 每句音频 audio/NNNN.mp3(tts_gen 生成,4 位命名)
 *
 * beats 契约(吴道子产出):{ zh, figure?, icons?:[], motion?, title?, tag?, kind? }
 *   figure = 相对路径(figures/xxx.png);icons = icons.js 里的 id 数组;motion = Ken Burns 类型。
 */
(function () {
  const beats = window.BEATS || [];
  const ICONS = window.RIG_ICONS || {};        // 缺 icons.js 时为空,图标层 graceful 跳过
  const ENTERS = window.RIG_ICON_ENTERS || {};
  const stage = document.getElementById('stage');
  const PAD = Math.max(4, String(beats.length).length);  // 对齐 tts_gen.py 的 4 位命名 0001.mp3

  const audios = beats.map((_, i) => {
    const a = new Audio();
    a.src = 'audio/' + String(i + 1).padStart(PAD, '0') + '.mp3';
    a.preload = 'auto';
    return a;
  });

  // Ken Burns:按 motion 给主体剪影一段缓慢 transform(WAAPI)。值小、慢,避免廉价感。
  const KEN = {
    'zoom-in':   [{ transform: 'scale(1.0)' }, { transform: 'scale(1.08)' }],
    'zoom-out':  [{ transform: 'scale(1.08)' }, { transform: 'scale(1.0)' }],
    'pan-left':  [{ transform: 'translateX(2.5%) scale(1.04)' }, { transform: 'translateX(-2.5%) scale(1.04)' }],
    'pan-right': [{ transform: 'translateX(-2.5%) scale(1.04)' }, { transform: 'translateX(2.5%) scale(1.04)' }],
    'still':     [{ transform: 'scale(1.015)' }, { transform: 'scale(1.03)' }],
  };

  let figAnim = null;

  function renderBeat(i) {
    const b = beats[i];
    if (figAnim) { try { figAnim.cancel(); } catch (e) {} figAnim = null; }

    const sc = document.createElement('div');
    sc.className = 'fig-scene';
    sc.innerHTML =
      '<div class="fig-title"></div>' +
      (b.tag ? '<div class="fig-tag"></div>' : '') +
      '<div class="fig-stage">' + (b.figure ? '<img class="fig-main" alt="">' : '') + '</div>' +
      '<div class="fig-icons"></div>' +
      '<div class="fig-subtitle"><div class="zh"></div></div>';
    sc.querySelector('.fig-title').textContent = b.title || '';
    if (b.tag) sc.querySelector('.fig-tag').textContent = b.tag;
    sc.querySelector('.zh').textContent = b.zh || '';
    stage.innerHTML = '';
    stage.appendChild(sc);

    // 主体剪影 + Ken Burns
    const img = sc.querySelector('.fig-main');
    if (img && b.figure) {
      img.src = b.figure;
      const kf = KEN[b.motion] || KEN['zoom-in'];
      figAnim = img.animate(kf, { duration: 7000, easing: 'ease-out', fill: 'both' });
    }

    // 图标飞入层(依次入场)
    const iconWrap = sc.querySelector('.fig-icons');
    (b.icons || []).forEach((id, n) => {
      const def = ICONS[id];
      if (!def) return;
      const el = document.createElement('div');
      el.className = 'fig-icon';
      el.innerHTML = def.svg;
      iconWrap.appendChild(el);
      const ent = ENTERS[def.enter] || ENTERS.pop;
      if (ent) el.animate(ent.keyframes, { ...ent.options, delay: 200 + n * 240 });
    });
  }

  function estimateMs(zh) {
    return Math.max(1200, (zh || '').replace(/\s/g, '').length * 220 + 700);
  }

  // 切句先彻底停掉上一条音频(pause + 归零 + 摘 onended),杜绝估时偏短导致的叠音。
  function stopAudio(a) {
    if (!a) return;
    try { a.pause(); a.currentTime = 0; } catch (e) {}
    a.onended = null;
  }

  function playFrom(i) {
    if (i > 0) stopAudio(audios[i - 1]);
    if (i >= beats.length) { end(); return; }
    renderBeat(i);
    const a = audios[i];
    let advanced = false;
    let fallback = null;
    const go = () => { if (advanced) return; advanced = true; clearTimeout(fallback); setTimeout(() => playFrom(i + 1), 80); };
    const armFallback = (ms) => { clearTimeout(fallback); fallback = setTimeout(go, ms); };
    a.onended = go;
    // 兜底优先用真实音频时长(loadedmetadata 后);拿不到才按字数估时。onended 是主路径。
    const byMeta = () => {
      if (isFinite(a.duration) && a.duration > 0) armFallback(a.duration * 1000 + 400);
      else armFallback(estimateMs(beats[i].zh) + 600);
    };
    if (a.readyState >= 1) byMeta();
    else { a.addEventListener('loadedmetadata', byMeta, { once: true }); armFallback(estimateMs(beats[i].zh) + 1500); }
    a.play().catch(() => armFallback(estimateMs(beats[i].zh)));
  }

  function end() {
    document.body.classList.add('ending');
    setTimeout(() => document.body.classList.remove('ending'), 1500);
  }

  // 录屏器入口(对齐 render.mjs)
  window.__player = {
    startRecordingPlayback() {
      document.body.classList.remove('ending');
      playFrom(0);
    },
  };

  // 交互式预览:先渲染第 1 帧;点页面任意处开播
  window.addEventListener('load', () => {
    if (beats.length) renderBeat(0);
    document.body.addEventListener('click', () => window.__player.startRecordingPlayback(), { once: true });
  });
})();
