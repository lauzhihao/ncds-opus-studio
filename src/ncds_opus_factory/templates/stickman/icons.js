/* icons.js — 自由图标 / 道具库（吴道子 · 与 motions.js 平级的「画面层」）
 *
 * 和 characters/motions 的「人物层」正交:icon 不挂在角色骨头上,而是独立漂浮在
 * 舞台坐标里、为强调关键词飞入(对标号的电池/时钟/箭头/红叉那一层)。
 * 注意:rig-spec 的 prop/bubble 骨头是「角色手持 / 头顶、跟着人走」的,不是这个。
 *
 * 契约(对齐 motions.js 的数据驱动哲学,Agent 只输出 icon id):
 *   ICONS[id] = { name, cat, keywords:[中文], enter:入场动效id, svg:黑剪影矢量 }
 *   ENTERS[id] = { name, keyframes, options }   // WAAPI one-shot 入场动效
 * 视觉语言:统一 viewBox 100x100、stroke=currentColor(默认黑)、粗线圆角,与小黑人同源。
 * keywords 供未来吴道子 agent 按柳永脚本关键词自动选图(见 README「已知最大缺口」Step3)。
 */
(function () {
  // 统一外壳:粗线条 + 圆角端点。需要实心的局部元素自带 fill。
  const W = (inner) =>
    '<svg viewBox="0 0 100 100" fill="none" stroke="currentColor" stroke-width="7"' +
    ' stroke-linecap="round" stroke-linejoin="round">' + inner + '</svg>';

  const SOLID = 'fill="currentColor" stroke="none"';

  const ICONS = {
    clock: { name: '时钟', cat: '时间', enter: 'pop',
      keywords: ['时间', '来不及', '紧迫', '拖延', '效率', '赶时间'],
      svg: W('<circle cx="50" cy="50" r="36"/><line x1="50" y1="50" x2="50" y2="28"/><line x1="50" y1="50" x2="66" y2="56"/>') },

    battery: { name: '低电量', cat: '能量', enter: 'pop',
      keywords: ['精力', '内耗', '累', '耗尽', '没电', '疲惫'],
      svg: W('<rect x="18" y="36" width="54" height="28" rx="5"/><rect x="74" y="44" width="7" height="12" rx="2" ' + SOLID + '/><rect x="24" y="42" width="11" height="16" rx="2" ' + SOLID + '/>') },

    'arrow-up': { name: '上升', cat: '趋势', enter: 'rise',
      keywords: ['成长', '提升', '上涨', '进步', '增长', '变好'],
      svg: W('<line x1="50" y1="78" x2="50" y2="30"/><polyline points="32,48 50,28 68,48"/>') },

    'arrow-down': { name: '下降', cat: '趋势', enter: 'pop',
      keywords: ['下跌', '衰退', '退步', '减少', '变差', '下滑'],
      svg: W('<line x1="50" y1="22" x2="50" y2="70"/><polyline points="32,52 50,72 68,52"/>') },

    lightbulb: { name: '灯泡', cat: '认知', enter: 'pop',
      keywords: ['灵感', '顿悟', '想法', '点子', '开窍', '明白'],
      svg: W('<circle cx="50" cy="40" r="20"/><line x1="41" y1="64" x2="59" y2="64"/><line x1="44" y1="70" x2="56" y2="70"/>') },

    warning: { name: '警告', cat: '提醒', enter: 'pop',
      keywords: ['警惕', '注意', '坑', '危险', '小心', '风险'],
      svg: W('<polygon points="50,20 80,72 20,72"/><line x1="50" y1="38" x2="50" y2="56"/><circle cx="50" cy="64" r="2.5" ' + SOLID + '/>') },

    x: { name: '红叉', cat: '判断', enter: 'pop',
      keywords: ['错误', '否定', '别这样', '不对', '禁止', '失败'],
      svg: W('<line x1="32" y1="32" x2="68" y2="68"/><line x1="68" y1="32" x2="32" y2="68"/>') },

    check: { name: '对勾', cat: '判断', enter: 'draw',
      keywords: ['正确', '认同', '该这样', '搞定', '完成', '没问题'],
      svg: W('<polyline points="26,52 44,70 74,32"/>') },

    lock: { name: '锁', cat: '状态', enter: 'pop',
      keywords: ['受限', '卡住', '突破', '锁死', '困住', '解锁'],
      svg: W('<rect x="28" y="46" width="44" height="34" rx="6"/><path d="M37 46 V38 a13 13 0 0 1 26 0 V46"/><line x1="50" y1="58" x2="50" y2="68"/>') },

    target: { name: '靶心', cat: '目标', enter: 'pop',
      keywords: ['目标', '聚焦', '精准', '重点', '命中', '方向'],
      svg: W('<circle cx="50" cy="50" r="34"/><circle cx="50" cy="50" r="20"/><circle cx="50" cy="50" r="7" ' + SOLID + '/>') },

    chat: { name: '对话气泡', cat: '沟通', enter: 'rise',
      keywords: ['沟通', '话术', '回应', '反击', '表达', '回怼'],
      svg: W('<rect x="20" y="26" width="60" height="40" rx="11"/><polygon points="36,64 33,80 52,64" ' + SOLID + '/>') },

    money: { name: '钱', cat: '价值', enter: 'pop',
      keywords: ['钱', '收入', '价值', '工资', '涨薪', '回报'],
      svg: W('<circle cx="50" cy="50" r="34"/><polyline points="40,34 50,48 60,34"/><line x1="50" y1="48" x2="50" y2="66"/><line x1="40" y1="53" x2="60" y2="53"/><line x1="40" y1="60" x2="60" y2="60"/>') },

    heart: { name: '心', cat: '情绪', enter: 'pop',
      keywords: ['情绪', '在意', '关系', '喜欢', '用心', '感受'],
      svg: W('<path d="M50 72 C 22 52 28 28 50 42 C 72 28 78 52 50 72 Z"/>') },

    flag: { name: '旗帜', cat: '目标', enter: 'rise',
      keywords: ['里程碑', '目标达成', '小目标', '插旗', '阶段', '终点'],
      svg: W('<line x1="32" y1="22" x2="32" y2="82"/><polygon points="32,26 70,32 32,48"/>') },

    eye: { name: '眼睛', cat: '认知', enter: 'draw',
      keywords: ['看见', '洞察', '被看见', '观察', '关注', '发现'],
      svg: W('<path d="M16 50 C 32 30 68 30 84 50 C 68 70 32 70 16 50 Z"/><circle cx="50" cy="50" r="10"/><circle cx="50" cy="50" r="3.5" ' + SOLID + '/>') },

    star: { name: '星', cat: '强调', enter: 'pop',
      keywords: ['重点', '高光', '优秀', '加分', '关键', '亮点'],
      svg: W('<polygon points="50,12 61,38 89,38 66,56 75,84 50,67 25,84 34,56 11,38 39,38"/>') },
  };

  const ENTERS = {
    pop: { name: '弹入',
      keyframes: [
        { transform: 'scale(0.2)', opacity: 0, offset: 0 },
        { transform: 'scale(1.12)', opacity: 1, offset: 0.7 },
        { transform: 'scale(1)', opacity: 1, offset: 1 },
      ],
      options: { duration: 420, easing: 'cubic-bezier(.34,1.56,.64,1)', fill: 'both' } },
    rise: { name: '上浮',
      keyframes: [
        { transform: 'translateY(24px)', opacity: 0 },
        { transform: 'translateY(0px)', opacity: 1 },
      ],
      options: { duration: 460, easing: 'cubic-bezier(.2,.8,.3,1)', fill: 'both' } },
    // draw:真正的「手画」描边由 player 给每条 path 设 pathLength + stroke-dashoffset 动画驱动;
    // 此处 keyframes 是引擎不支持时的淡入兜底。
    draw: { name: '描边手画',
      keyframes: [{ opacity: 0 }, { opacity: 1 }],
      options: { duration: 600, easing: 'ease-out', fill: 'both' } },
  };

  window.RIG_ICONS = ICONS;
  window.RIG_ICON_ENTERS = ENTERS;
  window.RIG_ICON_LIST = Object.keys(ICONS).map((id) => ({ id, ...ICONS[id] }));
})();
