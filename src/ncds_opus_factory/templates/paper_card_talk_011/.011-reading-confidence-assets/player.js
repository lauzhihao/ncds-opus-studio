/* ──────────────────────────────────────────────────────────────────
   播放引擎（音频驱动版）
   - 每条 beat 对应一个 audio/NNNN.mp3（由 tts_gen.py 生成）
   - 字幕推进由 audio 的 'ended' 事件触发，不再用字符数估时
   - 节奏滑杆改写 audio.playbackRate
   - Ken Burns 镜头时长 = 该 scene 全部 beat 音频时长之和（含 rate）
   - 若某条 audio 加载失败，回退到字符估时，保证片子能播完
   ────────────────────────────────────────────────────────────────── */

(function () {
  const EP = window.EPISODE || {};
  const ASSET_ROOT = EP.__assetsRoot || '.assets';
  const VER = EP.__ver || Math.floor(Date.now() / 1000);
  const $ = (id) => document.getElementById(id);
  const beats = EP.beats || [];
  const scenes = EP.scenes || {};

  // 给图片 / 音频 URL 加 cache-bust：ncds.cc 静态资源是 immutable+30d，
  // 重新生图 / 重录音频后必须 ?v= 不同才能让浏览器拉新文件。
  function bustedUrl(url) {
    return url + (url.indexOf('?') >= 0 ? '&' : '?') + 'v=' + VER;
  }

  // 字幕进场池：每条 beat 选一个，hash 确定性映射，headless 录制可复现
  const CAP_ENTERS = [
    'cap-enter-fly-up', 'cap-enter-fade-blur', 'cap-enter-zoom-soft',
    'cap-enter-mask-l-r', 'cap-enter-letter-spread', 'cap-enter-rise-glow',
    'cap-enter-fly-up', 'cap-enter-fade-blur',   // 重复温和款，提高它们的出现频率
  ];
  // 图片内层 Ken Burns 池：每个 scene 选一个，跨 beat 持续慢推不重启
  const IMG_KENS = [
    'mo-img-zoom-in', 'mo-img-zoom-out',
    'mo-img-pan-l', 'mo-img-pan-r', 'mo-img-pan-u', 'mo-img-pan-d',
    'mo-img-diag-br', 'mo-img-diag-tl',
    'mo-img-parallax', 'mo-img-wobble', 'mo-img-breathe',
  ];
  function _hash(s) {
    let h = 2166136261 >>> 0;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }
  function _pick(arr, seed) { return arr[_hash(seed) % arr.length]; }

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
  const PIC_DIR = ASSET_ROOT + '/pictures';
  function picSrcFor(sceneId, index) {
    const nn = String(index + 1).padStart(2, '0');
    return bustedUrl(PIC_DIR + '/' + nn + '-' + sceneId + '.webp');
  }

  // 应用 scene 级 motion 配置：加 mo-scene-* class + 写 duration/easing CSS var
  function applySceneMotion(el, motion) {
    if (!motion) return;
    if (motion.enter) el.classList.add('mo-scene-' + motion.enter);
    if (motion.duration) el.style.setProperty('--motion-scene-duration', motion.duration + 'ms');
    if (motion.easing) el.style.setProperty('--motion-scene-easing', motion.easing);
  }

  // 应用 chapter style：把 style.* 字段写成 CSS variable
  function applyChapterStyle(el, style) {
    if (!style) return;
    const setVar = (k, v, unit) => { if (v != null) el.style.setProperty(k, v + (unit || '')); };
    setVar('--chapter-num-size',          style.numSize, 'px');
    setVar('--chapter-num-font',          style.numFont ? '"' + style.numFont + '"' : null, '');
    setVar('--chapter-num-weight',        style.numWeight);
    setVar('--chapter-num-color',         style.numColor);
    setVar('--chapter-num-accent',        style.numAccentColor);
    setVar('--chapter-num-letter-spacing', style.numLetterSpacing);
    setVar('--chapter-text-size',         style.subtitleSize, 'px');
    setVar('--chapter-text-font',         style.subtitleFont ? '"' + style.subtitleFont + '"' : null, '');
    setVar('--chapter-text-weight',       style.subtitleWeight);
    setVar('--chapter-text-color',        style.subtitleColor);
    setVar('--chapter-text-line-height',  style.subtitleLineHeight);
    setVar('--chapter-text-align',        style.subtitleAlign);
    setVar('--chapter-text-max-width',    style.subtitleMaxWidth, 'px');
    const rule = style.rule || {};
    setVar('--chapter-rule-width',  rule.width,  'px');
    setVar('--chapter-rule-height', rule.height, 'px');
    setVar('--chapter-rule-color',  rule.color);
  }

  const stack = $('sceneStack');
  const capZh = $('capZh');
  const capEn = $('capEn');
  const progress = $('progress');
  const band = $('band');

  const sceneNodes = {};
  sceneOrder.forEach((id, i) => {
    const def = scenes[id] || { prompt: '(未定义)' };
    const el = document.createElement('div');
    el.className = 'scene';
    const src = picSrcFor(id, i);

    // motion class（场景过渡）
    applySceneMotion(el, def.motion);

    // 图片内层 Ken Burns / Pan / Parallax：仅图片型 scene 应用，
    // 章节卡走 chapter-card 渲染、没有 image-slot，所以不挂。
    // 优先 episode.json scenes[id].motion.image（去前缀写法可省略 'mo-img-'），
    // 缺省走确定性 hash 在 IMG_KENS 池里轮换 ⇒ 同一稿子录制结果稳定。
    if (def.type !== 'chapter') {
      let kenClass = null;
      const ki = (def.motion && def.motion.image) ? def.motion.image : null;
      if (typeof ki === 'string' && ki !== 'auto') {
        kenClass = (ki.indexOf('mo-img-') === 0) ? ki : ('mo-img-' + ki);
      } else if (ki !== 'none') {
        kenClass = _pick(IMG_KENS, 'ken:' + id + ':' + i);
      }
      if (kenClass) el.classList.add(kenClass);
    }

    if (def.type === 'chapter') {
      // 章节卡纯 CSS 渲染，不挂 image-slot 也不显示 placeholder；
      // 背景就是 var(--card) 暖纸底色，章节卡覆盖在上面。
      // 当前实现：只渲染 chapter-text 一行居中（num/rule 已从视觉中撤掉）
      el.classList.add('is-chapter');
      applyChapterStyle(el, def.style);
      const subtitle = def.subtitle || '';
      el.innerHTML =
        '<div class="chapter-card">' +
        '  <div class="chapter-text">' + subtitle + '</div>' +
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
  const padWidth = Math.max(4, String(beats.length).length);
  const audioElements = beats.map((_, i) => {
    const a = new Audio();
    a.src = bustedUrl(`${ASSET_ROOT}/audio/${String(i + 1).padStart(padWidth, '0')}.mp3`);
    a.preload = 'auto';
    return a;
  });

  // ── 当前 beat 状态 ─────────────────────────────────────────────
  let cur = 0;
  let playing = false;
  let pendingTimer = null;
  let advanceToken = 0;

  function estimateMs(zh) {
    const n = (zh || '').replace(/\s/g, '').length;
    return Math.max(1000, n * 200 + 700);
  }

  function beatMs(i, rate) {
    const a = audioElements[i];
    const dur = a && isFinite(a.duration) ? a.duration * 1000 : estimateMs(beats[i].zh);
    return dur / (rate || 1);
  }

  function computeSceneRunMs(startIdx) {
    const rate = parseFloat($('rate').value) || 1;
    const sc = beats[startIdx].scene;
    let total = 0;
    for (let j = startIdx; j < beats.length && beats[j].scene === sc; j++) {
      total += beatMs(j, rate);
    }
    return Math.max(800, total + 80);
  }

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

  function showBeat(i) {
    if (i < 0 || i >= beats.length) return;
    const b = beats[i];
    cur = i;

    // 每条 beat 重放字幕入场：先剥掉旧的 cap-enter-* 类，写新文本，强制 reflow，再贴新类。
    // 这样即使前后两条 beat 选中相同动效，DOM 也会重启 keyframes。
    const capEnter = (b.cap && b.cap.enter)
      ? (b.cap.enter.indexOf('cap-enter-') === 0 ? b.cap.enter : 'cap-enter-' + b.cap.enter)
      : _pick(CAP_ENTERS, 'cap:' + i + ':' + (b.zh || ''));
    for (const el of [capZh, capEn]) {
      const toDrop = [];
      for (const c of el.classList) { if (c.indexOf('cap-enter-') === 0) toDrop.push(c); }
      for (const c of toDrop) el.classList.remove(c);
    }
    capZh.textContent = b.zh;
    capEn.textContent = b.en;
    void capZh.offsetWidth; // reflow，使下面 add() 必然重启动画
    capZh.classList.add(capEnter);
    capEn.classList.add(capEnter);

    const newSceneId = b.scene;
    const sceneEl = sceneNodes[newSceneId];
    const sceneWasActive = sceneEl.classList.contains('active');
    for (const id of sceneOrder) {
      sceneNodes[id].classList.toggle('active', id === newSceneId);
    }

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

  function silenceOthers(keep) {
    for (let j = 0; j < audioElements.length; j++) {
      if (j === keep) continue;
      const a = audioElements[j];
      if (!a) continue;
      a.onended = null;
      if (!a.paused) a.pause();
    }
  }

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
        if (myToken !== advanceToken || !playing) return;
        pendingTimer = setTimeout(() => {
          if (myToken !== advanceToken || !playing) return;
          playFrom(i + 1);
        }, beatMs(i, rate));
      });
    }
  }

  function endRun() {
    document.body.classList.add('ending');
    setTimeout(() => {
      document.body.classList.remove('ending');
      stop();
      capZh.textContent = '';
      capEn.textContent = '';
    }, 1500);
  }

  function enableMotion() {
    document.body.classList.add('motion-enabled');
    // 当前 scene 已激活但因守门没动效，需要"重新激活"才能触发 keyframes
    const sceneId = beats[cur] && beats[cur].scene;
    const sceneEl = sceneId && sceneNodes[sceneId];
    if (sceneEl && sceneEl.classList.contains('active')) {
      sceneEl.classList.remove('active');
      void sceneEl.offsetWidth;
      sceneEl.classList.add('active');
      if (window.__overlays) {
        const def = scenes[sceneId] || {};
        window.__overlays.renderInto(sceneEl, def.overlays);
      }
    }
  }

  function play() {
    if (playing) return;
    enableMotion();
    playing = true;
    $('playBtn').textContent = '⏸ 暂停';
    const audio = audioElements[cur];
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

  $('playBtn').addEventListener('click', () => playing ? pause() : play());

  $('restartBtn').addEventListener('click', () => {
    pause();
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

  $('rate').addEventListener('input', () => {
    const r = parseFloat($('rate').value) || 1;
    const a = audioElements[cur];
    if (a) a.playbackRate = r;
  });

  const recFlash = $('recFlash');
  $('recBtn').addEventListener('click', () => {
    enterRecording();
  });

  function enterRecording() {
    pause();
    enableMotion();
    cur = 0;
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

  function startRecordingPlayback(opts) {
    opts = opts || {};
    pause();
    enableMotion();
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
      const rate = 1;
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

  window.__player = { play, pause, showBeat, enterRecording, exitRecording, startRecordingPlayback, beats };
})();
