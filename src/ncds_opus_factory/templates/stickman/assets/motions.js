/* motions.js — 动作模板库
 *
 * 每个动作 = 一组 Web Animations API 轨道（track）：
 *   { bone, keyframes:[{transform,...}], options:{duration,easing,iterations,direction} }
 * rig.js 把每条轨道用 element.animate() 挂到角色对应骨头上。
 *
 * 为什么用 WAAPI 而不是写死的 CSS keyframes：
 *   - Agent 只输出动作 id，引擎按数据生成动画，不必为每个动作预写 CSS 类
 *   - 可叠加（多个动作命中不同骨头）、可整体调速（playbackRate）、可调幅度（intensity）
 *   - keyframes 里的 rotate/translate 数值会被 rig.js 按 intensity 等比缩放，scale 不缩放
 *
 * 约定：iterations 用 Infinity（循环预览）；想播一次就在 play() 时覆盖 options。
 * 同一根骨头被多个动作同时命中时，后挂的覆盖前面的（composite 默认 replace）。
 */
(function () {
  const LOOP = { iterations: Infinity, easing: 'ease-in-out' };

  const M = {
    'idle-breathe': {
      name: '呼吸', desc: '躯干轻微起伏 + 头部浮动。几乎所有静止角色都该叠一层，避免「画面卡住」。',
      // composite:'add' —— 作为通用叠加层，呼吸要「加」在其它头/躯干动作（点头/托腮/前倾）之上，
      // 而不是用默认 'replace' 把对方顶掉。这样 think-tilt + idle-breathe 时头部既倾斜又起伏。
      tracks: [
        { bone: 'torso', keyframes: [{ transform: 'translateY(0px) scaleY(1)' }, { transform: 'translateY(0px) scaleY(1.025)' }],
          options: { ...LOOP, duration: 2600, direction: 'alternate', composite: 'add' } },
        { bone: 'head', keyframes: [{ transform: 'translateY(0px)' }, { transform: 'translateY(-3px)' }],
          options: { ...LOOP, duration: 2600, direction: 'alternate', composite: 'add' } },
      ],
    },

    'raise-arms': {
      name: '举双手', desc: '双臂同时上举（对话里的「I am strong」动作）。',
      tracks: [
        { bone: 'arm-l', keyframes: [{ transform: 'rotate(0deg)' }, { transform: 'rotate(-52deg)' }],
          options: { ...LOOP, duration: 1200, direction: 'alternate' } },
        { bone: 'arm-r', keyframes: [{ transform: 'rotate(0deg)' }, { transform: 'rotate(52deg)' }],
          options: { ...LOOP, duration: 1200, direction: 'alternate' } },
      ],
    },

    wave: {
      name: '挥手', desc: '右臂举起并左右摆动打招呼。',
      tracks: [
        { bone: 'arm-r', keyframes: [
            { transform: 'rotate(-95deg)', offset: 0 },
            { transform: 'rotate(-120deg)', offset: 0.5 },
            { transform: 'rotate(-95deg)', offset: 1 },
          ], options: { iterations: Infinity, duration: 900, easing: 'ease-in-out' } },
      ],
    },

    nod: {
      name: '点头', desc: '头部前后点动，表示「认同 / 是」。',
      tracks: [
        { bone: 'head', keyframes: [{ transform: 'rotate(-5deg)' }, { transform: 'rotate(9deg)' }],
          options: { ...LOOP, duration: 620, direction: 'alternate' } },
      ],
    },

    'shake-head': {
      name: '摇头', desc: '头部左右快速摆动，表示「否定 / 不」。',
      tracks: [
        { bone: 'head', keyframes: [{ transform: 'translateX(-5px) rotate(-3deg)' }, { transform: 'translateX(5px) rotate(3deg)' }],
          options: { ...LOOP, duration: 300, direction: 'alternate' } },
      ],
    },

    walk: {
      name: '原地走', desc: '四肢交替摆动 + 身体小幅上下，模拟走路。',
      tracks: [
        { bone: 'leg-l', keyframes: [{ transform: 'rotate(-24deg)' }, { transform: 'rotate(24deg)' }],
          options: { ...LOOP, duration: 560, direction: 'alternate' } },
        { bone: 'leg-r', keyframes: [{ transform: 'rotate(24deg)' }, { transform: 'rotate(-24deg)' }],
          options: { ...LOOP, duration: 560, direction: 'alternate' } },
        { bone: 'arm-l', keyframes: [{ transform: 'rotate(18deg)' }, { transform: 'rotate(-18deg)' }],
          options: { ...LOOP, duration: 560, direction: 'alternate' } },
        { bone: 'arm-r', keyframes: [{ transform: 'rotate(-18deg)' }, { transform: 'rotate(18deg)' }],
          options: { ...LOOP, duration: 560, direction: 'alternate' } },
        { bone: 'root', keyframes: [{ transform: 'translateY(0px)' }, { transform: 'translateY(-4px)' }],
          options: { ...LOOP, duration: 280, direction: 'alternate' } },
      ],
    },

    jump: {
      name: '跳跃', desc: '整体下蹲蓄力后弹起，双腿收拢。',
      tracks: [
        { bone: 'root', keyframes: [
            { transform: 'translateY(0px)', offset: 0 },
            { transform: 'translateY(8px)', offset: 0.18 },
            { transform: 'translateY(-70px)', offset: 0.5 },
            { transform: 'translateY(8px)', offset: 0.82 },
            { transform: 'translateY(0px)', offset: 1 },
          ], options: { iterations: Infinity, duration: 1100, easing: 'ease-in-out' } },
        { bone: 'leg-l', keyframes: [
            { transform: 'rotate(0deg)', offset: 0 }, { transform: 'rotate(-12deg)', offset: 0.5 }, { transform: 'rotate(0deg)', offset: 1 },
          ], options: { iterations: Infinity, duration: 1100, easing: 'ease-in-out' } },
        { bone: 'leg-r', keyframes: [
            { transform: 'rotate(0deg)', offset: 0 }, { transform: 'rotate(12deg)', offset: 0.5 }, { transform: 'rotate(0deg)', offset: 1 },
          ], options: { iterations: Infinity, duration: 1100, easing: 'ease-in-out' } },
      ],
    },

    'point-right': {
      name: '右手指向', desc: '右臂抬起伸向画面右侧，强调要点。',
      tracks: [
        { bone: 'arm-r', keyframes: [
            { transform: 'rotate(0deg)', offset: 0 },
            { transform: 'rotate(-70deg)', offset: 0.55 },
            { transform: 'rotate(-62deg)', offset: 1 },
          ], options: { iterations: Infinity, duration: 1500, direction: 'alternate', easing: 'ease-out' } },
      ],
    },

    clap: {
      name: '鼓掌', desc: '双臂向身体中线快速开合，鼓掌。',
      tracks: [
        { bone: 'arm-l', keyframes: [{ transform: 'rotate(2deg)' }, { transform: 'rotate(34deg)' }],
          options: { ...LOOP, duration: 240, direction: 'alternate' } },
        { bone: 'arm-r', keyframes: [{ transform: 'rotate(-2deg)' }, { transform: 'rotate(-34deg)' }],
          options: { ...LOOP, duration: 240, direction: 'alternate' } },
      ],
    },

    'think-tilt': {
      name: '托腮思索', desc: '头部缓慢倾斜，配合思考角色。',
      tracks: [
        { bone: 'head', keyframes: [{ transform: 'rotate(-4deg)' }, { transform: 'rotate(7deg)' }],
          options: { ...LOOP, duration: 1900, direction: 'alternate' } },
      ],
    },

    'bubble-float': {
      name: '气泡浮动', desc: '想法气泡上下漂浮（需角色含 bubble 骨头）。',
      tracks: [
        { bone: 'bubble', keyframes: [{ transform: 'translateY(0px) scale(1)' }, { transform: 'translateY(-12px) scale(1.08)' }],
          options: { ...LOOP, duration: 2000, direction: 'alternate' } },
      ],
    },

    'prop-swing': {
      name: '道具摇摆', desc: '手持道具像钟摆一样左右晃（需角色含 prop 骨头）。',
      tracks: [
        { bone: 'prop', keyframes: [{ transform: 'rotate(-13deg)' }, { transform: 'rotate(13deg)' }],
          options: { ...LOOP, duration: 950, direction: 'alternate' } },
      ],
    },

    'lean-in': {
      name: '前倾', desc: '躯干微微前倾，带出「凑近 / 强调」的气势。',
      tracks: [
        { bone: 'torso', keyframes: [{ transform: 'rotate(0deg)' }, { transform: 'rotate(6deg)' }],
          options: { ...LOOP, duration: 1700, direction: 'alternate' } },
      ],
    },
  };

  window.RIG_MOTIONS = M;
  window.RIG_MOTION_LIST = Object.keys(M).map((id) => ({ id, ...M[id] }));
})();
