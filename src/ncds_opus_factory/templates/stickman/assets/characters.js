/* characters.js — 角色素材库
 *
 * 每个角色 = 一段内联 SVG，所有可动部件包在 <g class="bone NAME"> 里，
 * NAME 必须是 rig-spec.js 里登记的骨头名。角色全部画在同一套 VIEWBOX(300×380)
 * + 同一套关节锚点上，所以「任意角色 × 任意动作」都能组合。
 *
 * 视觉：黑色剪影 —— 头/躯干用实心圆角块（.solid），四肢用圆头粗线（.limb），
 * 贴近上游对话里那两张「黑色剪影小人」素材的风格。静态布景（椅子等）用不带
 * `bone` 的 class，不会被动作驱动。
 *
 * bones[] 显式列出该角色拥有的骨头：rig.js 只给这些骨头设旋转中心，
 * 动作命中到角色没有的骨头时静默跳过（graceful skip）。
 */
(function () {
  // 公共：四肢粗线（圆头）、实心块。集中在这里，角色 markup 只管几何。
  const C = {
    standing: {
      id: 'standing',
      name: '站立',
      desc: '中性站姿，双臂自然下垂。最通用的底座角色。',
      bones: ['root', 'head', 'torso', 'arm-l', 'arm-r', 'leg-l', 'leg-r'],
      svg: `
        <g class="bone leg-l"><path class="limb leg" d="M135 212 L126 348"/></g>
        <g class="bone leg-r"><path class="limb leg" d="M165 212 L174 348"/></g>
        <g class="bone torso"><rect class="solid" x="122" y="96" width="56" height="122" rx="27"/></g>
        <g class="bone arm-l"><path class="limb" d="M124 124 L94 212"/></g>
        <g class="bone arm-r"><path class="limb" d="M176 124 L206 212"/></g>
        <g class="bone head"><circle class="solid" cx="150" cy="62" r="35"/></g>`,
    },

    cheer: {
      id: 'cheer',
      name: '欢呼',
      desc: '双臂上举成 V 字，适合「成功 / 强大 / 庆祝」的情绪。',
      bones: ['root', 'head', 'torso', 'arm-l', 'arm-r', 'leg-l', 'leg-r'],
      svg: `
        <g class="bone leg-l"><path class="limb leg" d="M135 212 L122 348"/></g>
        <g class="bone leg-r"><path class="limb leg" d="M165 212 L178 348"/></g>
        <g class="bone torso"><rect class="solid" x="122" y="96" width="56" height="122" rx="27"/></g>
        <g class="bone arm-l"><path class="limb" d="M124 124 L82 56"/></g>
        <g class="bone arm-r"><path class="limb" d="M176 124 L218 56"/></g>
        <g class="bone head"><circle class="solid" cx="150" cy="62" r="35"/></g>`,
    },

    thinking: {
      id: 'thinking',
      name: '思考',
      desc: '右手托腮，头顶一串想法气泡。适合「疑问 / 思索」的转场。',
      bones: ['root', 'head', 'torso', 'arm-l', 'arm-r', 'leg-l', 'leg-r', 'bubble'],
      svg: `
        <g class="bone leg-l"><path class="limb leg" d="M135 212 L126 348"/></g>
        <g class="bone leg-r"><path class="limb leg" d="M165 212 L174 348"/></g>
        <g class="bone torso"><rect class="solid" x="122" y="96" width="56" height="122" rx="27"/></g>
        <g class="bone arm-l"><path class="limb" d="M124 124 L102 210"/></g>
        <g class="bone arm-r"><path class="limb" d="M176 124 L150 104"/></g>
        <g class="bone head"><circle class="solid" cx="150" cy="62" r="35"/></g>
        <g class="bone bubble">
          <circle class="bub" cx="196" cy="58" r="6"/>
          <circle class="bub" cx="214" cy="42" r="9"/>
          <circle class="bub" cx="236" cy="22" r="13"/>
        </g>`,
    },

    present: {
      id: 'present',
      name: '讲解',
      desc: '右臂前伸、手持圆牌。适合「指向 / 展示要点」，prop 可单独摇摆。',
      bones: ['root', 'head', 'torso', 'arm-l', 'arm-r', 'leg-l', 'leg-r', 'prop'],
      svg: `
        <g class="bone leg-l"><path class="limb leg" d="M135 212 L126 348"/></g>
        <g class="bone leg-r"><path class="limb leg" d="M165 212 L174 348"/></g>
        <g class="bone torso"><rect class="solid" x="122" y="96" width="56" height="122" rx="27"/></g>
        <g class="bone arm-l"><path class="limb" d="M124 124 L102 210"/></g>
        <g class="bone arm-r"><path class="limb" d="M176 124 L226 150"/></g>
        <g class="bone prop">
          <circle class="solid" cx="244" cy="150" r="20"/>
          <path class="bub-line" d="M244 138 L244 162 M232 150 L256 150"/>
        </g>
        <g class="bone head"><circle class="solid" cx="150" cy="62" r="35"/></g>`,
    },
    wushu: {
      id: 'wushu',
      name: '武者',
      desc: '二段肢体（肘 + 膝关节），默认跨立预备式。用于八极拳「八大招」连贯套路。',
      bones: [
        'root', 'head', 'torso',
        'arm-l-upper', 'arm-l-lower', 'arm-r-upper', 'arm-r-lower',
        'leg-l-upper', 'leg-l-lower', 'leg-r-upper', 'leg-r-lower',
      ],
      // 跨立预备式：两脚开立、膝微屈，双臂自然下垂略带肘弯，握拳。
      // 嵌套：前臂在上臂内、小腿在大腿内 → 上段转动带下段（正向运动学）。
      svg: `
        <g class="bone leg-l-upper">
          <path class="limb leg" d="M135 212 L124 280"/>
          <g class="bone leg-l-lower"><path class="limb leg" d="M124 280 L116 348"/></g>
        </g>
        <g class="bone leg-r-upper">
          <path class="limb leg" d="M165 212 L176 280"/>
          <g class="bone leg-r-lower"><path class="limb leg" d="M176 280 L184 348"/></g>
        </g>
        <g class="bone torso">
          <rect class="solid" x="122" y="96" width="56" height="122" rx="27"/>
          <rect class="sash" x="120" y="188" width="60" height="13" rx="6"/>
        </g>
        <g class="bone arm-l-upper">
          <path class="limb" d="M123 122 L112 168"/>
          <g class="bone arm-l-lower">
            <path class="limb" d="M112 168 L108 208"/>
            <circle class="fist" cx="108" cy="210" r="13"/>
          </g>
        </g>
        <g class="bone arm-r-upper">
          <path class="limb" d="M177 122 L188 168"/>
          <g class="bone arm-r-lower">
            <path class="limb" d="M188 168 L192 208"/>
            <circle class="fist" cx="192" cy="210" r="13"/>
          </g>
        </g>
        <g class="bone head">
          <circle class="solid" cx="150" cy="62" r="35"/>
          <rect class="band" x="113" y="44" width="74" height="11" rx="5"/>
        </g>`,
    },
  };

  window.RIG_CHARACTERS = C;
  window.RIG_CHARACTER_LIST = Object.values(C);
})();
