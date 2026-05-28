/* ──────────────────────────────────────────────────────────────────
   播放引擎（音频驱动版，按 scene 整段合成）
   - 每个 scene 对应一个 audio/scene-<sid>.mp3，含该 scene 所有 beat
   - 每个 beat 在 episode.json 上带 audioFile / audioStart / audioEnd（ms）
   - 字幕推进：scripted 用 (audioEnd-audioStart) 时长；实时用 timeupdate 命中 audioEnd
   - 同 scene 内 beat 不暂停 audio，自然续播；跨 scene 切换 audio 并 seek
   - 节奏滑杆改写 audio.playbackRate
   ────────────────────────────────────────────────────────────────── */

(function () {
  const EP = window.EPISODE || {};
  const ASSET_ROOT = EP.__assetsRoot || '.assets';
  const VER = EP.__ver || Math.floor(Date.now() / 1000);
  const $ = (id) => document.getElementById(id);
  const beats = EP.beats || [];
  // <END> 截断：beat.zh 末尾出现 <END> 表示视频到此结束，后面所有 beat 全部丢弃。
  // 作者填多少播多少；不用手动同步 beats[].length 或删占位条。
  const END_RE = /\s*<END>\s*$/;
  const endIdx = beats.findIndex(b => b && typeof b.zh === 'string' && END_RE.test(b.zh));
  if (endIdx >= 0) {
    beats[endIdx].zh = beats[endIdx].zh.replace(END_RE, '');
    beats.length = endIdx + 1;
  }
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

  // 预生成图片路径：pictures/<scene-id>.webp，scene-id 本身就是索引（如 S1-001）
  const PIC_DIR = ASSET_ROOT + '/pictures';
  function picSrcFor(sceneId) {
    return bustedUrl(PIC_DIR + '/' + sceneId + '.webp');
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
  const progressInput = $('progressInput');
  const progressTotal = $('progressTotal');
  const band = $('band');

  const sceneNodes = {};
  // 计算并挂上 image-stage 用的 mo-img-* class（先清旧再加新；用于 init 和
   // inspector 改 motion.image 后的实时刷新）。chapter 类 scene 不挂图片动效。
  function applyImageKen(el, def, idx) {
    const toRemove = [];
    for (const c of el.classList) {
      if (c.indexOf('mo-img-') === 0) toRemove.push(c);
    }
    for (const c of toRemove) el.classList.remove(c);
    if (def.type === 'chapter') return;
    const ki = (def.motion && def.motion.image) ? def.motion.image : null;
    let kenClass = null;
    if (typeof ki === 'string' && ki !== 'auto' && ki !== 'none') {
      kenClass = (ki.indexOf('mo-img-') === 0) ? ki : ('mo-img-' + ki);
    } else if (ki !== 'none') {
      kenClass = _pick(IMG_KENS, 'ken:' + el.dataset.sceneId + ':' + idx);
    }
    if (kenClass) el.classList.add(kenClass);
  }
  // 重应用 mo-scene-* 入场 class（先清旧 mo-scene-* 再 applySceneMotion）
  function applySceneMotionFresh(el, def) {
    const toRemove = [];
    for (const c of el.classList) {
      if (c.indexOf('mo-scene-') === 0) toRemove.push(c);
    }
    for (const c of toRemove) el.classList.remove(c);
    applySceneMotion(el, def && def.motion);
  }

  sceneOrder.forEach((id, i) => {
    const def = scenes[id] || { prompt: '(未定义)' };
    const el = document.createElement('div');
    el.className = 'scene';
    el.dataset.sceneId = id;
    const src = picSrcFor(id);

    // motion class（场景过渡 + 图片 Ken Burns）。优先 episode.json
    // scenes[id].motion.{enter,image}，缺省按 hash 从 IMG_KENS 池轮换以保
    // 录制可复现。
    applySceneMotionFresh(el, def);
    applyImageKen(el, def, i);

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
      const fit = (def.imageFit === 'cover' || def.imageFit === 'fill') ? def.imageFit : 'contain';
      // image-stage：light DOM wrapper，专门承载 mo-img-* keyframe。
      // 原本规则挂在 image-slot::part(image)（shadow DOM 内的 img）—— chrome 对
      // 跨 shadow DOM 的 ::part + animation 不真正启动 animation 实例：computed
      // style 里能看到 animation-name，但 img.getAnimations() 是空、transform 全程
      // 不变。挂到 light DOM wrapper 上就 work。
      el.innerHTML =
        '<div class="image-stage">' +
        '  <image-slot id="slot-' + id + '" src="' + src + '" fit="' + fit + '" placeholder="拖入此场景的图（详见左侧）"></image-slot>' +
        '</div>' +
        '<div class="placeholder">' +
        '  <div class="ph-id">' + id + '</div>' +
        '  <div class="ph-prompt">' + (def.prompt || '') + '</div>' +
        '</div>';
    }
    stack.appendChild(el);
    sceneNodes[id] = el;
  });

  // ── 预加载所有 scene 音频 ───────────────────────────────────────
  // 按 unique audioFile 去重共享 Audio；beatAudio[i] = { audio, start, end } 指向其上区间
  const audioByFile = new Map();
  function _getAudio(file) {
    let a = audioByFile.get(file);
    if (!a) {
      a = new Audio();
      a.src = bustedUrl(`${ASSET_ROOT}/${file}`);
      a.preload = 'auto';
      audioByFile.set(file, a);
    }
    return a;
  }
  const beatAudio = beats.map((b) => ({
    audio: b.audioFile ? _getAudio(b.audioFile) : null,
    start: typeof b.audioStart === 'number' ? b.audioStart : 0,
    end:   typeof b.audioEnd   === 'number' ? b.audioEnd   : 0,
  }));
  // 仍暴露给老调用方/外部脚本 — render.mjs 等 waitForFunction 用
  const audioElements = Array.from(audioByFile.values());

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
    const ent = beatAudio[i];
    const dur = ent ? (ent.end - ent.start) : estimateMs(beats[i].zh);
    return (dur > 0 ? dur : estimateMs(beats[i].zh)) / (rate || 1);
  }

  // 进入 beat[i+1] 前是否需要"跨 scene"切换：
  // 不同 scene、或 audio 元素不同（理论上同 scene 一定同 audio）
  function _isSceneBoundary(i) {
    if (i + 1 >= beats.length) return false;
    return beats[i + 1].scene !== beats[i].scene
        || beatAudio[i + 1].audio !== beatAudio[i].audio;
  }
  // scene 间 80ms 静音，scene 内 beat 之间不停顿（音频天然连续）
  function _gapAfter(i) {
    return _isSceneBoundary(i) ? 80 : 0;
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

    // 每条 beat 都给 overlays 一次机会：at.match 命中时 overlay 才入场
    if (window.__overlays && window.__overlays.onBeat) {
      // 传 beatMs 让 overlays 按 keyword 字符位置占比算飞入时刻
      const ent = beatAudio[i];
      const beatMsForOverlay = ent ? (ent.end - ent.start) : estimateMs(b.zh);
      window.__overlays.onBeat(sceneEl, b, beatMsForOverlay);
    }

    if (!sceneWasActive && document.body.classList.contains('ken-burns')) {
      sceneEl.style.transition = 'none';
      void sceneEl.offsetWidth;
      const ms = computeSceneRunMs(i);
      sceneEl.style.transition = 'opacity 0.55s ease, transform ' + ms + 'ms ease-out';
    } else if (!document.body.classList.contains('ken-burns')) {
      sceneEl.style.transition = '';
    }

    setProgress(i + 1);
    fitBand();
  }

  function setProgress(n) {
    // 录制中、用户正在编辑输入框时，不去覆盖
    if (document.activeElement === progressInput) return;
    progressInput.value = String(n);
  }

  function silenceOthers(keepAudio) {
    for (const a of audioByFile.values()) {
      if (a === keepAudio) continue;
      a.onended = null;
      a.ontimeupdate = null;
      if (!a.paused) a.pause();
    }
  }

  function playFrom(i) {
    if (!playing) return;
    if (i >= beats.length) { endRun(); return; }
    showBeat(i);

    const ent = beatAudio[i];
    if (!ent || !ent.audio) {
      // 没音频：用估时兜底推进
      const rate = parseFloat($('rate').value) || 1;
      const myToken = ++advanceToken;
      pendingTimer = setTimeout(() => {
        if (myToken !== advanceToken || !playing) return;
        pendingTimer = setTimeout(() => playFrom(i + 1), _gapAfter(i));
      }, beatMs(i, rate));
      return;
    }
    const audio = ent.audio;
    silenceOthers(audio);

    const rate = parseFloat($('rate').value) || 1;
    const myToken = ++advanceToken;
    audio.playbackRate = rate;

    // 进入该 beat 时是否需要 seek：
    //   - 不在该 beat 区间内（用 50ms 容差）
    //   - 上一 beat 不是同 audio（跨 scene 切换）或当前 i === 0
    const startSec = ent.start / 1000;
    const endSec   = ent.end / 1000;
    const inRange = isFinite(audio.duration)
      && audio.currentTime >= startSec - 0.05
      && audio.currentTime <  endSec - 0.02;
    const prevSameAudio = i > 0 && beatAudio[i - 1] && beatAudio[i - 1].audio === audio;
    if (!inRange || !prevSameAudio) {
      try { audio.currentTime = startSec; } catch (_) {}
    }

    audio.onended = null;
    audio.ontimeupdate = null;

    // 推进：timeupdate 命中 endSec（带 20ms 容差）就切下一 beat
    const advance = () => {
      if (myToken !== advanceToken || !playing) return;
      audio.ontimeupdate = null;
      audio.onended = null;
      const sameNext = !_isSceneBoundary(i);
      if (!sameNext && !audio.paused) audio.pause();
      pendingTimer = setTimeout(() => playFrom(i + 1), _gapAfter(i));
    };
    audio.ontimeupdate = () => {
      if (myToken !== advanceToken || !playing) return;
      if (audio.currentTime >= endSec - 0.02) advance();
    };
    // 兜底：万一 timeupdate 漏掉（如文件末尾），onended 也触发
    audio.onended = advance;

    if (audio.paused) {
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
    { const b = $('playBtn'); b.dataset.state = 'playing'; b.dataset.label = '暂停'; }
    // 不再做 "断点续播 vs 重新播" 的细分：playFrom 内部会自动决定要不要 seek。
    playFrom(cur);
  }

  function pause() {
    playing = false;
    { const b = $('playBtn'); b.dataset.state = 'paused'; b.dataset.label = '播放'; }
    advanceToken++;
    if (pendingTimer) { clearTimeout(pendingTimer); pendingTimer = null; }
    const ent = beatAudio[cur];
    if (ent && ent.audio) {
      ent.audio.onended = null;
      ent.audio.ontimeupdate = null;
      if (!ent.audio.paused) ent.audio.pause();
    }
  }

  function stop() {
    pause();
  }

  $('playBtn').addEventListener('click', () => playing ? pause() : play());

  $('restartBtn').addEventListener('click', () => {
    pause();
    for (const a of audioByFile.values()) {
      try { a.currentTime = 0; } catch (_) { /* metadata not yet loaded */ }
    }
    cur = 0;
    showBeat(0);
  });

  function jumpTo(i) {
    const wasPlaying = playing;
    pause();
    // 跳转：把目标 beat 所在 audio seek 到 audioStart；其他 audio reset
    const target = Math.max(0, Math.min(beats.length - 1, i));
    const ent = beatAudio[target];
    if (ent && ent.audio) {
      try { ent.audio.currentTime = ent.start / 1000; } catch (_) {}
    }
    showBeat(target);
    if (wasPlaying) play();
  }

  $('prevBtn').addEventListener('click', () => jumpTo(cur - 1));
  $('nextBtn').addEventListener('click', () => jumpTo(cur + 1));
  $('progressTotal').addEventListener('click', () => jumpTo(beats.length - 1));

  // 可编辑 beat 计数框：回车 / 失焦 跳到指定 beat（1-based）；
  // ↑/↓ 走 input 自带步进；Esc 取消编辑。
  function commitProgressInput() {
    const v = parseInt(progressInput.value, 10);
    if (!isFinite(v)) { setProgress(cur + 1); return; }
    const clamped = Math.max(1, Math.min(beats.length, v));
    progressInput.value = String(clamped);
    if (clamped - 1 !== cur) jumpTo(clamped - 1);
  }
  progressInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); commitProgressInput(); progressInput.blur(); }
    else if (e.key === 'Escape') { setProgress(cur + 1); progressInput.blur(); }
  });
  progressInput.addEventListener('blur', commitProgressInput);

  $('rate').addEventListener('input', () => {
    const r = parseFloat($('rate').value) || 1;
    const ent = beatAudio[cur];
    if (ent && ent.audio) ent.audio.playbackRate = r;
  });

  const recFlash = $('recFlash');
  $('recBtn').addEventListener('click', () => {
    enterRecording();
  });

  // 编辑模式入口；edit-mode.js 在 player.js 之后才注入，所以这里只挂 click，
  // 真正调用时 window.__editMode 已就绪。ncds.cc 上 edit-mode 自禁用，按钮一并移除。
  if (/(?:^|\.)ncds\.cc$/i.test(location.hostname)) {
    $('editBtn').remove();
  } else {
    $('editBtn').addEventListener('click', () => {
      if (window.__editMode && window.__editMode.enter) window.__editMode.enter();
      else console.error('edit-mode.js 未加载');
    });
  }

  function enterRecording() {
    pause();
    enableMotion();
    cur = 0;
    for (const a of audioByFile.values()) {
      try { a.currentTime = 0; } catch (_) {}
    }
    document.body.classList.add('recording');

    for (const id of sceneOrder) {
      sceneNodes[id].classList.remove('active');
    }
    capZh.textContent = '';
    capEn.textContent = '';
    setProgress(1);

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
    // 输入框 / textarea 里别抢键（progress 跳转输入 + 编辑模式 inspector 都有 input）
    const tag = (e.target && e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || (e.target && e.target.isContentEditable)) return;
    if (e.key === ' ') {
      e.preventDefault();
      playing ? pause() : play();
    }
    if (e.key === 'ArrowRight') $('nextBtn').click();
    if (e.key === 'ArrowLeft')  $('prevBtn').click();
  });

  // ── 初始化 ─────────────────────────────────────────────────────
  progressInput.max = String(beats.length);
  progressTotal.textContent = String(beats.length);

  // URL ?p=N （1-based）→ 启动时跳到指定 beat；超界 clamp 进有效范围
  let startBeat = 0;
  try {
    const p = parseInt(new URLSearchParams(location.search).get('p') || '', 10);
    if (isFinite(p)) startBeat = Math.max(0, Math.min(beats.length - 1, p - 1));
  } catch (_) { /* ignore */ }
  showBeat(startBeat);

  function startRecordingPlayback(opts) {
    opts = opts || {};
    pause();
    enableMotion();
    cur = 0;
    for (const a of audioByFile.values()) {
      try { a.currentTime = 0; } catch (_) {}
    }
    document.body.classList.add('recording');
    for (const id of sceneOrder) sceneNodes[id].classList.remove('active');
    capZh.textContent = '';
    capEn.textContent = '';
    setProgress(1);

    if (opts.scripted) {
      playing = true;
      { const b = $('playBtn'); b.dataset.state = 'playing'; b.dataset.label = '暂停'; }
      const rate = 1;
      function scriptedNext(i) {
        if (!playing) return;
        if (i >= beats.length) { endRun(); return; }
        showBeat(i);
        const ent = beatAudio[i];
        const beatDurMs = ent ? (ent.end - ent.start) : 0;
        const durMs = (beatDurMs > 0 ? beatDurMs : estimateMs(beats[i].zh)) / rate;
        const myToken = ++advanceToken;
        pendingTimer = setTimeout(() => {
          if (myToken !== advanceToken || !playing) return;
          pendingTimer = setTimeout(() => scriptedNext(i + 1), _gapAfter(i));
        }, durMs);
      }
      scriptedNext(0);
    } else {
      play();
    }
  }

  // 给 inspector 改 motion.{enter,image} 后即时刷 sceneEl 的 mo-* class +
   // toggle active 触发 keyframe restart（编辑模式下 .image-stage 守门已豁免）。
  function refreshSceneMotion(sceneId) {
    const def = scenes[sceneId];
    const el = sceneNodes[sceneId];
    if (!def || !el) return;
    const idx = sceneOrder.indexOf(sceneId);
    applySceneMotionFresh(el, def);
    applyImageKen(el, def, idx);
    if (el.classList.contains('active')) {
      el.classList.remove('active');
      void el.offsetWidth;
      el.classList.add('active');
    }
  }

  window.__player = { play, pause, showBeat, enterRecording, exitRecording, startRecordingPlayback, refreshSceneMotion, beats, scenes, sceneNodes, sceneOrder, audioElements };
})();
