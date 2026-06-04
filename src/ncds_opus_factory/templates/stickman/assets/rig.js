/* rig.js — RigSystem 引擎（可复用核心）
 *
 * 把「固定骨骼规范 + 角色库 + 动作库」组装成可运行的动画角色。对外 API：
 *
 *   RigSystem.build(charId)               -> SVGElement（已挂好旋转中心、含 root 包裹层）
 *   RigSystem.mount(container, charId)    -> handle { el, bones, charId }
 *   RigSystem.play(handle, motionIds, opts) -> controller（pause/play/cancel/setSpeed...）
 *   RigSystem.renderScene(config, mountEl)  -> { el, controller }  ← Agent 的 JSON → 成片版式
 *   RigSystem.listCharacters() / listMotions()
 *
 * opts: { intensity=1（幅度，等比缩放 rotate/translate）, speed=1（playbackRate）, loop=true }
 *
 * 设计要点：动作命中到角色没有的骨头时静默跳过；keyframes 里的角度/位移按 intensity
 * 缩放，但 scale() 不缩放（避免呼吸幅度被异常放大）。
 */
(function () {
  const SPEC = window.RIG_SPEC;
  const CHARS = window.RIG_CHARACTERS;
  const MOTIONS = window.RIG_MOTIONS;
  const SVG_NS = 'http://www.w3.org/2000/svg';

  // 把 transform 字符串里的 rotate/translate 数值按 k 等比缩放；scale() 原样保留。
  function scaleTransform(transform, k) {
    if (!transform || k == null || k === 1) return transform;
    return transform.replace(/(rotate|translateX|translateY|translate)\(([^)]*)\)/g, (_, fn, args) => {
      const scaled = args.replace(/-?\d*\.?\d+/g, (n) => String(+(parseFloat(n) * k).toFixed(3)));
      return fn + '(' + scaled + ')';
    });
  }

  function scaleKeyframes(keyframes, k) {
    return keyframes.map((kf) => {
      const out = Object.assign({}, kf);
      if (out.transform) out.transform = scaleTransform(out.transform, k);
      return out;
    });
  }

  // 构建一个角色 SVG：root 包裹层 + 角色 markup，并给每根骨头设好绝对旋转中心。
  function build(charId) {
    const char = CHARS[charId];
    if (!char) throw new Error('RigSystem: unknown character "' + charId + '"');
    const src =
      '<svg xmlns="' + SVG_NS + '" viewBox="' + SPEC.VIEWBOX + '" class="rig" ' +
      'preserveAspectRatio="xMidYMax meet" aria-label="' + char.name + '">' +
      '<g class="bone root">' + char.svg + '</g></svg>';
    const parsed = new DOMParser().parseFromString(src, 'image/svg+xml');
    const err = parsed.querySelector('parsererror');
    if (err) throw new Error('RigSystem: SVG parse error in "' + charId + '": ' + err.textContent);
    const svg = document.importNode(parsed.documentElement, true);

    // 给每根骨头设旋转中心（绝对用户单位）。transform-box 由 styles.css 统一设 view-box。
    svg.querySelectorAll('.bone').forEach((g) => {
      const name = SPEC.BONE_NAMES.find((b) => g.classList.contains(b));
      if (!name) return;
      const origin = SPEC.originFor(name);
      if (origin) g.style.transformOrigin = origin;
      g.dataset.bone = name;
    });
    return svg;
  }

  function collectBones(svg) {
    const map = {};
    svg.querySelectorAll('.bone').forEach((g) => {
      if (g.dataset.bone) map[g.dataset.bone] = g;
    });
    return map;
  }

  function mount(container, charId) {
    const svg = build(charId);
    container.appendChild(svg);
    return { el: svg, bones: collectBones(svg), charId, __anims: [] };
  }

  function play(handle, motionIds, opts) {
    opts = opts || {};
    const intensity = opts.intensity == null ? 1 : opts.intensity;
    const speed = opts.speed == null ? 1 : opts.speed;
    const ids = Array.isArray(motionIds) ? motionIds : [motionIds];

    // 清掉上一轮
    handle.__anims.forEach((a) => a.cancel());
    handle.__anims = [];
    const missing = new Set();
    const unknown = new Set();

    ids.filter(Boolean).forEach((mid) => {
      const motion = MOTIONS[mid];
      if (!motion) { console.warn('RigSystem: unknown motion "' + mid + '"'); unknown.add(mid); return; }
      motion.tracks.forEach((track) => {
        const el = handle.bones[track.bone];
        if (!el) { missing.add(track.bone); return; } // 角色没有这根骨头 → 静默跳过
        const options = Object.assign({}, track.options);
        if (opts.loop === false) options.iterations = 1;
        const anim = el.animate(scaleKeyframes(track.keyframes, intensity), options);
        anim.playbackRate = speed;
        handle.__anims.push(anim);
      });
    });
    if (missing.size) {
      console.info('RigSystem: 角色「' + handle.charId + '」缺少骨头 [' +
        [...missing].join(', ') + ']，相关动作轨道已跳过。');
    }

    const ctrl = {
      get animations() { return handle.__anims; },         // getter：cancel 后也反映当前实况
      unknownMotions: [...unknown],                          // 让 Agent 能检测拼错的 motion id
      missingBones: [...missing],                            // 角色缺失、被跳过的骨头
      pause() { handle.__anims.forEach((a) => a.pause()); return ctrl; },
      play() { handle.__anims.forEach((a) => a.play()); return ctrl; },
      cancel() { handle.__anims.forEach((a) => a.cancel()); handle.__anims = []; return ctrl; },
      setSpeed(s) { handle.__anims.forEach((a) => { a.playbackRate = s; }); return ctrl; },
      // 只等一次性动画；无限循环动画的 .finished 永不 settle，过滤掉以免 await 卡死
      finished: Promise.all(handle.__anims
        .filter((a) => a.effect && a.effect.getTiming().iterations !== Infinity)
        .map((a) => a.finished.catch(() => {}))),
    };
    return ctrl;
  }

  // —— Agent 契约：把结构化 JSON 场景渲染成完整版式卡片 ——
  // config = { character, motion:[...], intensity?, speed?, loop?, title?, tag?,
  //            subtitleZh?, subtitleEn?, theme?('paper'|'dark') }
  function renderScene(config, mountEl) {
    if (!config || !config.character) {
      throw new Error('RigSystem.renderScene: config.character is required');
    }
    // 先在「脱离文档」的 scene 上把角色挂好、动画起好——build() 抛错也不会污染 mountEl
    const scene = document.createElement('div');
    scene.className = 'rig-scene theme-' + (config.theme || 'paper');
    scene.innerHTML =
      '<div class="rig-scene-title"></div>' +
      (config.tag ? '<div class="rig-scene-tag"></div>' : '') +
      '<div class="rig-scene-stage"><div class="rig-scene-character"></div></div>' +
      '<div class="rig-scene-subtitle"><div class="zh"></div><div class="en"></div></div>';

    scene.querySelector('.rig-scene-title').textContent = config.title || '';
    if (config.tag) scene.querySelector('.rig-scene-tag').textContent = config.tag;
    scene.querySelector('.zh').textContent = config.subtitleZh || '';
    scene.querySelector('.en').textContent = config.subtitleEn || '';

    const charBox = scene.querySelector('.rig-scene-character');
    const handle = mount(charBox, config.character);
    const controller = play(handle, config.motion || [], {
      intensity: config.intensity, speed: config.speed, loop: config.loop,
    });

    // 全部成功后才接入 mountEl（失败时上面已抛出，mountEl 保持原样）
    if (mountEl) { mountEl.innerHTML = ''; mountEl.appendChild(scene); }
    return { el: scene, handle, controller };
  }

  // —— 套路 / 连贯动作：把多招串成一条连续动画 ——
  // 每根骨头 = 一条按 offset 排布的 keyframe 轨道（rest → 各招 pose → rest）。
  // 返回的 controller.currentMove() 报出当前是第几招，供 UI 实时显示招式名。
  function playSequence(handle, seqId, opts) {
    opts = opts || {};
    const seq = (window.RIG_SEQUENCES || {})[seqId];
    if (!seq) throw new Error('RigSystem: unknown sequence "' + seqId + '"');
    const speed = opts.speed == null ? 1 : opts.speed;
    const loop = opts.loop !== false;
    const moves = seq.moves;
    const returnDur = seq.returnDuration == null ? 800 : seq.returnDuration;

    const durs = moves.map((m) => m.duration || 800);
    const total = durs.reduce((a, b) => a + b, 0) + returnDur;
    const ends = []; let acc = 0;
    durs.forEach((d) => { acc += d; ends.push(acc / total); });

    const bonesUsed = new Set();
    moves.forEach((m) => Object.keys(m.pose || {}).forEach((b) => bonesUsed.add(b)));

    handle.__anims.forEach((a) => a.cancel());
    handle.__anims = [];
    const missing = new Set();

    bonesUsed.forEach((bone) => {
      const el = handle.bones[bone];
      if (!el) { missing.add(bone); return; }
      const frames = [{ offset: 0, transform: 'rotate(0deg)', easing: moves[0].easing || 'ease-in-out' }];
      let cur = 'rotate(0deg)';
      moves.forEach((m, i) => {
        if (m.pose && m.pose[bone] != null) cur = m.pose[bone];      // 未写到的骨头：保持上一招姿态
        const nextEasing = (moves[i + 1] && moves[i + 1].easing) || m.easing || 'ease-in-out';
        frames.push({ offset: ends[i], transform: cur, easing: nextEasing });
      });
      frames.push({ offset: 1, transform: 'rotate(0deg)' });          // 收势归预备
      const anim = el.animate(frames, { duration: total, iterations: loop ? Infinity : 1, fill: 'both' });
      anim.playbackRate = speed;
      handle.__anims.push(anim);
    });

    const ctrl = {
      get animations() { return handle.__anims; },
      sequence: seq, moves, total, missingBones: [...missing],
      pause() { handle.__anims.forEach((a) => a.pause()); return ctrl; },
      play() { handle.__anims.forEach((a) => a.play()); return ctrl; },
      cancel() { handle.__anims.forEach((a) => a.cancel()); handle.__anims = []; return ctrl; },
      setSpeed(s) { handle.__anims.forEach((a) => { a.playbackRate = s; }); return ctrl; },
      // 由领头动画的 currentTime 推出当前招式索引（处于收势尾段返回 -1）
      currentMove() {
        const a = handle.__anims[0];
        if (!a || a.currentTime == null) return -1;
        const off = ((a.currentTime % total) + total) % total / total;
        for (let i = 0; i < ends.length; i++) if (off <= ends[i]) return i;
        return -1;
      },
    };
    return ctrl;
  }

  window.RigSystem = {
    build, mount, play, playSequence, renderScene, scaleTransform,
    listCharacters: () => window.RIG_CHARACTER_LIST,
    listMotions: () => window.RIG_MOTION_LIST,
    listSequences: () => window.RIG_SEQUENCE_LIST || [],
    spec: SPEC,
  };
})();
