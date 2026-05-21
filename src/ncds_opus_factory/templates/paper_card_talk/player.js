/* ──────────────────────────────────────────────────────────────────
   播放引擎（音频驱动版）
   - 每条 beat 对应一个 audio/NNNN.mp3（由 tts_gen.py 生成）
   - 字幕推进由 audio 的 'ended' 事件触发，不再用字符数估时
   - 节奏滑杆改写 audio.playbackRate
   - Ken Burns 镜头时长 = 该 scene 全部 beat 音频时长之和（含 rate）
   - 若某条 audio 加载失败，回退到字符估时，保证片子能播完
   ────────────────────────────────────────────────────────────────── */

(function () {
  const ASSET_ROOT = '.009-paper-card-talk-assets';
  const $ = (id) => document.getElementById(id);
  const beats = window.BEATS || [];
  const scenes = window.SCENES || {};
  const stack = $('sceneStack');
  const capZh = $('capZh');
  const capEn = $('capEn');
  const progress = $('progress');
  const band = $('band');

  // ── 构造 scene 节点（每个 SCENES 项一个节点） ──────────────────────
  const sceneOrder = [];
  const sceneSeen = new Set();
  for (const b of beats) {
    if (!sceneSeen.has(b.scene)) {
      sceneSeen.add(b.scene);
      sceneOrder.push(b.scene);
    }
  }

  // 预生成图片路径：pictures/NN-<scene-id>.webp，NN 跟 SCENES 出场顺序对齐
  // image-slot 的 src 属性是 author-controlled，会 fallback 到空状态（不破坏页面），
  // 所以即使某张图还没生成也能正常播。
  const PIC_DIR = ASSET_ROOT + '/pictures';
  function picSrcFor(sceneId, index) {
    const nn = String(index + 1).padStart(2, '0');
    return PIC_DIR + '/' + nn + '-' + sceneId + '.webp';
  }

  const sceneNodes = {};
  sceneOrder.forEach((id, i) => {
    const def = scenes[id] || { prompt: '(未定义)', label: '' };
    const el = document.createElement('div');
    el.className = 'scene';
    const src = picSrcFor(id, i);
    if (id.startsWith('ch')) {
      el.classList.add('is-chapter');
      const num = ({ ch1: '一', ch2: '二', ch3: '三', ch4: '四', ch5: '五' })[id] || '';
      const firstBeat = beats.find(b => b.scene === id);
      const sub = firstBeat ? firstBeat.zh.replace(/^[一二三四五六七八九十]、/, '') : '';
      el.innerHTML =
        '<image-slot id="slot-' + id + '" src="' + src + '" fit="contain" placeholder="(可选) 章节背景图，留空则用纯色封面"></image-slot>' +
        '<div class="chapter-card">' +
        '  <div class="chapter-num"><em>' + num + '</em></div>' +
        '  <div class="chapter-rule" aria-hidden="true"></div>' +
        '  <div class="chapter-text">' + sub + '</div>' +
        '</div>';
    } else {
      el.innerHTML =
        '<image-slot id="slot-' + id + '" src="' + src + '" fit="contain" placeholder="拖入此场景的图（详见左侧）"></image-slot>' +
        '<div class="placeholder">' +
        '  <div class="ph-id">' + id + '</div>' +
        '  <div class="ph-prompt">' + (def.prompt || '') + '</div>' +
        '</div>';
    }
    stack.appendChild(el);
    sceneNodes[id] = el;
  });

  // ── 预加载所有 beat 的音频 ─────────────────────────────────────
  // 总体积 ~2.7MB（139 段），preload=auto 让浏览器尽早缓存，避免句间空白。
  const padWidth = Math.max(4, String(beats.length).length);
  const audioElements = beats.map((_, i) => {
    const a = new Audio();
    a.src = `${ASSET_ROOT}/audio/${String(i + 1).padStart(padWidth, '0')}.mp3`;
    a.preload = 'auto';
    return a;
  });

  // ── 当前 beat 状态 ─────────────────────────────────────────────
  let cur = 0;
  let playing = false;
  let pendingTimer = null;
  let advanceToken = 0;

  // ── 当前句子在音频不可用时的字符估时（fallback）─────────────────
  function estimateMs(zh) {
    const n = (zh || '').replace(/\s/g, '').length;
    return Math.max(1000, n * 200 + 700);
  }

  // ── 单条 beat 的"应该停留多久"（含 rate）：优先用音频时长 ─────
  function beatMs(i, rate) {
    const a = audioElements[i];
    const dur = a && isFinite(a.duration) ? a.duration * 1000 : estimateMs(beats[i].zh);
    return dur / (rate || 1);
  }

  // ── 计算某个 scene 从 startIdx 起的连续 beat 总时长（ms，含 rate） ───
  // 给 Ken Burns 的 transform 时长做参考，使镜头推进与 scene 实际播放对齐。
  function computeSceneRunMs(startIdx) {
    const rate = parseFloat($('rate').value) || 1;
    const sc = beats[startIdx].scene;
    let total = 0;
    for (let j = startIdx; j < beats.length && beats[j].scene === sc; j++) {
      total += beatMs(j, rate);
    }
    return Math.max(800, total + 80);
  }

  // ── 字幕带溢出保护：缩放 capZh / capEn 至 band 容下为止 ───────────
  function fitBand() {
    capZh.style.fontSize = '';
    capEn.style.fontSize = '';
    void band.offsetHeight;
    for (const el of [capZh, capEn]) {
      let tries = 10;
      while (tries-- > 0 && el.scrollWidth > el.clientWidth + 1) {
        const sz = parseFloat(getComputedStyle(el).fontSize);
        el.style.fontSize = (sz * 0.93) + 'px';
      }
    }
    let tries = 10;
    while (tries-- > 0 && band.scrollHeight > band.clientHeight + 1) {
      const sz = parseFloat(getComputedStyle(capZh).fontSize);
      capZh.style.fontSize = (sz * 0.92) + 'px';
    }
  }

  // ── 渲染当前 beat ──────────────────────────────────────────────
  function showBeat(i) {
    if (i < 0 || i >= beats.length) return;
    const b = beats[i];
    cur = i;
    capZh.textContent = b.zh;
    capEn.textContent = b.en;

    const newSceneId = b.scene;
    const sceneEl = sceneNodes[newSceneId];
    const sceneWasActive = sceneEl.classList.contains('active');
    for (const id of sceneOrder) {
      sceneNodes[id].classList.toggle('active', id === newSceneId);
    }

    // 新激活的 scene 重渲染 overlays（重建 DOM 强制重放 CSS 入场动效）
    if (!sceneWasActive && window.__overlays) {
      const def = scenes[newSceneId] || {};
      window.__overlays.renderInto(sceneEl, def.overlays);
    }

    if (!sceneWasActive && document.body.classList.contains('ken-burns')) {
      sceneEl.style.transition = 'none';
      void sceneEl.offsetWidth;
      const ms = computeSceneRunMs(i);
      sceneEl.style.transition = 'opacity 0.55s ease, transform ' + ms + 'ms ease-out';
    } else if (!document.body.classList.contains('ken-burns')) {
      sceneEl.style.transition = '';
    }

    progress.textContent = (i + 1) + ' / ' + beats.length;
    fitBand();
  }

  // ── 停掉除指定 index 外所有 audio 的播放（用于 prev/next/restart）─
  function silenceOthers(keep) {
    for (let j = 0; j < audioElements.length; j++) {
      if (j === keep) continue;
      const a = audioElements[j];
      if (!a) continue;
      a.onended = null;
      if (!a.paused) a.pause();
    }
  }

  // ── 主播放循环（音频驱动）──────────────────────────────────────
  // 每个 beat：showBeat → audio.play()；onended 触发下一句。
  // 80ms 喘息延迟保留，让 scene 切换的视觉过渡不被音频立刻盖掉。
  function playFrom(i) {
    if (!playing) return;
    if (i >= beats.length) { endRun(); return; }
    showBeat(i);
    silenceOthers(i);

    const audio = audioElements[i];
    const rate = parseFloat($('rate').value) || 1;
    const myToken = ++advanceToken;

    audio.onended = null;
    audio.currentTime = 0;
    audio.playbackRate = rate;

    const goNext = () => {
      if (myToken !== advanceToken || !playing) return;
      pendingTimer = setTimeout(() => playFrom(i + 1), 80);
    };

    audio.onended = goNext;

    const p = audio.play();
    if (p && typeof p.catch === 'function') {
      p.catch((err) => {
        console.warn('audio play failed at beat', i + 1, err);
        // 音频失败兜底：用字符估时推进，保证片子不卡死
        if (myToken !== advanceToken || !playing) return;
        pendingTimer = setTimeout(() => {
          if (myToken !== advanceToken || !playing) return;
          playFrom(i + 1);
        }, beatMs(i, rate));
      });
    }
  }

  // ── 片尾淡出 ──────────────────────────────────────────────────
  function endRun() {
    document.body.classList.add('ending');
    setTimeout(() => {
      document.body.classList.remove('ending');
      stop();
      capZh.textContent = '';
      capEn.textContent = '';
    }, 1500);
  }

  function play() {
    if (playing) return;
    playing = true;
    $('playBtn').textContent = '⏸ 暂停';
    const audio = audioElements[cur];
    // 若当前 beat 的音频处于"已开始但未结束"的中间态，直接续播；否则从头开始
    if (
      audio &&
      isFinite(audio.duration) &&
      audio.currentTime > 0.05 &&
      audio.currentTime < audio.duration - 0.05
    ) {
      const myToken = ++advanceToken;
      const rate = parseFloat($('rate').value) || 1;
      audio.playbackRate = rate;
      audio.onended = () => {
        if (myToken !== advanceToken || !playing) return;
        pendingTimer = setTimeout(() => playFrom(cur + 1), 80);
      };
      const p = audio.play();
      if (p && typeof p.catch === 'function') {
        p.catch((err) => console.warn('audio resume failed', err));
      }
    } else {
      playFrom(cur);
    }
  }

  function pause() {
    playing = false;
    $('playBtn').textContent = '▶ 播放';
    advanceToken++;
    if (pendingTimer) { clearTimeout(pendingTimer); pendingTimer = null; }
    const a = audioElements[cur];
    if (a) {
      a.onended = null;
      if (!a.paused) a.pause();
    }
  }

  function stop() {
    pause();
  }

  // ── 控件 ──────────────────────────────────────────────────────
  $('playBtn').addEventListener('click', () => playing ? pause() : play());

  $('restartBtn').addEventListener('click', () => {
    pause();
    // reset all audios so we can replay cleanly
    for (const a of audioElements) {
      try { a.currentTime = 0; } catch (_) { /* metadata not yet loaded */ }
    }
    cur = 0;
    showBeat(0);
  });

  function jumpTo(i) {
    const wasPlaying = playing;
    pause();
    const a = audioElements[cur];
    if (a) { try { a.currentTime = 0; } catch (_) {} }
    showBeat(Math.max(0, Math.min(beats.length - 1, i)));
    if (wasPlaying) play();
  }

  $('prevBtn').addEventListener('click', () => jumpTo(cur - 1));
  $('nextBtn').addEventListener('click', () => jumpTo(cur + 1));

  // 节奏滑杆：实时调整当前正在播的 audio.playbackRate
  $('rate').addEventListener('input', () => {
    const r = parseFloat($('rate').value) || 1;
    const a = audioElements[cur];
    if (a) a.playbackRate = r;
  });

  // 录制模式
  const recFlash = $('recFlash');
  $('recBtn').addEventListener('click', () => {
    enterRecording();
  });

  function enterRecording() {
    pause();
    cur = 0;
    // 录制前重置全部 audio.currentTime，避免上次播到一半的状态污染
    for (const a of audioElements) {
      try { a.currentTime = 0; } catch (_) {}
    }
    document.body.classList.add('recording');

    for (const id of sceneOrder) {
      sceneNodes[id].classList.remove('active');
    }
    capZh.textContent = '';
    capEn.textContent = '';
    progress.textContent = '1 / ' + beats.length;

    const countdown = $('recCountdown');
    let n = 3;
    countdown.textContent = String(n);
    countdown.classList.add('show');
    const tick = setInterval(() => {
      n--;
      if (n > 0) {
        countdown.textContent = String(n);
      } else {
        clearInterval(tick);
        countdown.classList.remove('show');
        if (document.body.classList.contains('recording')) play();
      }
    }, 1000);
  }

  function exitRecording() {
    document.body.classList.remove('recording');
    recFlash.classList.remove('show');
    const countdown = $('recCountdown');
    if (countdown) {
      countdown.classList.remove('show');
      countdown.textContent = '';
    }
    pause();
  }

  document.addEventListener('keydown', (e) => {
    const isRecording = document.body.classList.contains('recording');
    if (e.key === 'Escape') {
      if (isRecording) exitRecording();
      else pause();
      return;
    }
    if (isRecording) return;
    if (e.key === ' ') {
      e.preventDefault();
      playing ? pause() : play();
    }
    if (e.key === 'ArrowRight') $('nextBtn').click();
    if (e.key === 'ArrowLeft')  $('prevBtn').click();
  });

  // ── 初始化 ─────────────────────────────────────────────────────
  showBeat(0);

  // 离线渲染入口：跳过 3 秒倒数，直接进 recording 状态从头播。
  // render.mjs 用 puppeteer 拉起页面后调它，配合 CDP screencast 抓帧。
  //
  // opts.scripted=true 时改用 setTimeout(audio.duration) 驱动 beat 推进，
  //   不依赖 audio.onended。headless --mute-audio 下 onended 触发时刻跟
  //   实际 audio.duration 有 ms 级抖动，139 个 beat 累加会让视觉比离线拼
  //   的音轨短几百 ms（muxed 后音频显得落后字幕）。scripted 模式锁死时
  //   长 = 音轨长，保证音画对齐。
  function startRecordingPlayback(opts) {
    opts = opts || {};
    pause();
    cur = 0;
    for (const a of audioElements) {
      try { a.currentTime = 0; } catch (_) {}
    }
    document.body.classList.add('recording');
    for (const id of sceneOrder) sceneNodes[id].classList.remove('active');
    capZh.textContent = '';
    capEn.textContent = '';
    progress.textContent = '1 / ' + beats.length;

    if (opts.scripted) {
      playing = true;
      $('playBtn').textContent = '⏸ 暂停';
      const rate = 1; // scripted 不读 rate 滑杆，固定 1x
      function scriptedNext(i) {
        if (!playing) return;
        if (i >= beats.length) { endRun(); return; }
        showBeat(i);
        const a = audioElements[i];
        const durMs = (a && isFinite(a.duration) ? a.duration * 1000 : estimateMs(beats[i].zh)) / rate;
        const myToken = ++advanceToken;
        pendingTimer = setTimeout(() => {
          if (myToken !== advanceToken || !playing) return;
          pendingTimer = setTimeout(() => scriptedNext(i + 1), 80);
        }, durMs);
      }
      scriptedNext(0);
    } else {
      play();
    }
  }

  // 暴露给 Tweaks 和 render.mjs
  window.__player = { play, pause, showBeat, enterRecording, exitRecording, startRecordingPlayback, beats };
})();
