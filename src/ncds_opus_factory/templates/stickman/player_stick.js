/* 吴道子 · 小人成片精简播放器(PoC)
 *
 * 复用 svg-rig 的 RigSystem.renderScene(自带 16:9 版式 + 字幕),
 * 按音频顺序逐句切换画面。对齐 paper_card_talk 的 render.mjs 录屏协议:
 *   - window.__player.startRecordingPlayback()  录屏器调它开播
 *   - 放完给 body 加 class 'ending'(render.mjs 监听它判断片子结束)
 *   - 每句音频 audio/NN.mp3(tts_gen 生成)
 */
(function () {
  const beats = window.BEATS || [];
  const stage = document.getElementById('stage');
  const PAD = Math.max(4, String(beats.length).length); // 对齐 tts_gen.py 的 4 位命名 0001.mp3

  const audios = beats.map((_, i) => {
    const a = new Audio();
    a.src = 'audio/' + String(i + 1).padStart(PAD, '0') + '.mp3';
    a.preload = 'auto';
    return a;
  });

  let controller = null;

  function renderBeat(i) {
    const b = beats[i];
    if (controller && controller.cancel) { try { controller.cancel(); } catch (e) {} }
    stage.innerHTML = '';
    const r = RigSystem.renderScene({
      character: b.character,
      motion: b.motion || ['idle-breathe'],
      subtitleZh: b.zh || '',
      subtitleEn: b.en || '',
      title: b.title || '',
      tag: b.tag || '',
      theme: b.theme || 'paper',
      intensity: 1,
      speed: 1,
    }, stage);
    controller = (r && r.controller) || null;
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
    // 兜底优先用「真实音频时长」(loadedmetadata 后)而非一上来拿 NaN 的 a.duration 猜;真拿不到才按字数估时。
    // onended 是主路径,fallback 只在它不触发时兜底(取真实时长 + 400ms buffer,避免提前切句)。
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
