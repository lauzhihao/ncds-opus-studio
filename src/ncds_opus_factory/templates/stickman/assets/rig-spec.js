/* rig-spec.js — 固定 SVG 骨骼规范（唯一真源 / single source of truth）
 *
 * 整个角色系统的「契约」。characters.js / motions.js / rig.js 全部以本文件为准：
 *   - 所有角色画在同一套画布 + 同一套关节锚点上（VIEWBOX + JOINTS）
 *   - 所有动作只能引用 BONES 里列出的骨头名
 *   - 这样就保证「任意角色 × 任意动作」都能组合，Agent 不需要关心具体角色长什么样
 *
 * 关键技术点：旋转中心（transform-origin）用「画布用户单位的绝对坐标」而不是
 * 元素自身 bbox 的百分比。配合 `transform-box: view-box`，rotate 永远绕真实关节转，
 * 不受肢体绘制方向 / bbox 形状影响 —— 比 `fill-box + 50% 0%` 那种写法稳得多。
 */
(function () {
  // 所有角色共用的画布。坐标系即「骨骼坐标系」。
  const VIEWBOX = '0 0 300 380';
  const WIDTH = 300;
  const HEIGHT = 380;

  // 规范骨骼表。pivot = 该骨头旋转/缩放的锚点（画布用户单位绝对坐标）。
  // role 仅作语义说明；optional=true 的骨头不是每个角色都有，动作命中不到时静默跳过。
  const BONES = {
    root:    { pivot: [150, 200], role: '整体（位移 / 跳跃 / 缩放）' },
    head:    { pivot: [150, 98],  role: '头（点头 / 摇头，绕脖子转）' },
    torso:   { pivot: [150, 212], role: '躯干（呼吸 / 前后倾，绕胯转）' },
    'arm-l': { pivot: [123, 122], role: '左臂 · 单段（绕左肩转）' },
    'arm-r': { pivot: [177, 122], role: '右臂 · 单段（绕右肩转）' },
    'leg-l': { pivot: [135, 212], role: '左腿 · 单段（绕左胯转）' },
    'leg-r': { pivot: [165, 212], role: '右腿 · 单段（绕右胯转）' },

    // —— 二段肢体：上臂/大腿为父，前臂/小腿嵌套为子，构成肘/膝关节 ——
    // 嵌套结构 + 绝对关节锚点 = 正向运动学：上臂转动带着前臂走，前臂再绕肘单独转。
    // 只有「武者」这类需要肘膝的角色用它们；单段角色仍用上面的 arm-l/leg-l。
    'arm-l-upper': { pivot: [123, 122], role: '左上臂（绕左肩）', optional: true, segmented: true },
    'arm-l-lower': { pivot: [112, 168], role: '左前臂（绕左肘）', optional: true, segmented: true },
    'arm-r-upper': { pivot: [177, 122], role: '右上臂（绕右肩）', optional: true, segmented: true },
    'arm-r-lower': { pivot: [188, 168], role: '右前臂（绕右肘）', optional: true, segmented: true },
    'leg-l-upper': { pivot: [135, 212], role: '左大腿（绕左胯）', optional: true, segmented: true },
    'leg-l-lower': { pivot: [124, 280], role: '左小腿（绕左膝）', optional: true, segmented: true },
    'leg-r-upper': { pivot: [165, 212], role: '右大腿（绕右胯）', optional: true, segmented: true },
    'leg-r-lower': { pivot: [176, 280], role: '右小腿（绕右膝）', optional: true, segmented: true },

    // —— 其它可选骨头 ——
    prop:    { pivot: [232, 150], role: '道具（手持物 / 吊饰，可摇摆）', optional: true },
    bubble:  { pivot: [150, 60],  role: '气泡 / 想法（浮动）', optional: true },
  };

  const BONE_NAMES = Object.keys(BONES);

  // 给定一根骨头，返回它的 transform-origin CSS 值（绝对用户单位）。
  function originFor(bone) {
    const b = BONES[bone];
    if (!b) return null;
    return b.pivot[0] + 'px ' + b.pivot[1] + 'px';
  }

  window.RIG_SPEC = {
    VIEWBOX, WIDTH, HEIGHT, BONES, BONE_NAMES, originFor,
    // 推荐 Agent 输出场景时引用的骨头子集（核心 6 + root），覆盖绝大多数动作。
    CORE_BONES: ['root', 'head', 'torso', 'arm-l', 'arm-r', 'leg-l', 'leg-r'],
    // 二段肢体骨头（肘/膝），供武术等需要关节细节的角色与套路使用。
    SEGMENTED_BONES: [
      'arm-l-upper', 'arm-l-lower', 'arm-r-upper', 'arm-r-lower',
      'leg-l-upper', 'leg-l-lower', 'leg-r-upper', 'leg-r-lower',
    ],
  };
})();
