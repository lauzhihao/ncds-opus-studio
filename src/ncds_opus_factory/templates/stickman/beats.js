/* PoC beats:取柳永「同事甩锅/阴阳」成稿的开头 3 句,验证
 * "小人角色 + 动作 + 字幕"逐句切换 + 配音 + 录屏整条链路。
 * character: standing/present/thinking/cheer  (见 assets/characters.js)
 * motion:    idle-breathe/point-right/think-tilt/lean-in/bubble-float/...(见 assets/motions.js)
 * 注意:zh 必须用双引号(tts_gen 的正则只认双引号包裹的 zh 字段)。
 */
window.BEATS = [
  {
    character: "present",
    motion: ["point-right", "idle-breathe"],
    zh: "职场里总被同事阴阳的人，先听我一句",
    title: "同事阴阳你，别急着怼",
    tag: "职场 · 认知",
  },
  {
    character: "thinking",
    motion: ["think-tilt", "bubble-float"],
    zh: "你最大的弱点，就是太急着证明自己没生气",
  },
  {
    character: "present",
    motion: ["lean-in"],
    zh: "别急着怼，你只问一句：你是在建议，还是评价？",
  },
];
