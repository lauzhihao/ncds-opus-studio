/*
 * 字幕节拍 + 中央图分镜数据
 *
 * BEATS：每条对应一行字幕 / 一段 TTS 朗读
 *   zh      中文字幕（朗读内容）
 *   en      英文字幕
 *   scene   引用 SCENES 中的图 id（连续相同 id 表示同一张图持续）
 *   chapter 可选：1-5，章节封面卡（特殊版式）
 *
 * SCENES：每张中央图
 *   prompt  给文生图工具的提示词（中文）
 *   label   可选，叠在图上的大标题（保留扩展）
 */

window.BEATS = [
  // ── 开场钩子 ──
  { zh: "你听过这句话吗", en: "Have you heard this before", scene: "hook" },
  { zh: ""读书有什么用，大老板好多没上过大学"", en: "\"What's the point of school? Many bosses never went to college\"", scene: "hook" },
  { zh: "这句话害了多少人，你知道吗", en: "Do you know how many people this hurt?", scene: "hook" },
  { zh: "它让无数年轻人心安理得地放弃自己", en: "It made countless young people give up guilt-free", scene: "hook" },
  { zh: "然后用十年二十年去后悔", en: "then spend decades regretting", scene: "hook" },
  { zh: "今天我想跟你聊聊，读书到底是在干什么", en: "Let's talk about what studying really means", scene: "hook" },

  // ── 一、读书不是为了考试 ──
  { zh: "一、读书不是为了考试", en: "1. Studying isn't about exams", scene: "ch1", chapter: 1 },

  { zh: "很多人一提到读书", en: "When people hear 'studying'", scene: "narrow-view" },
  { zh: "脑子里就是分数、排名、文凭", en: "they think grades, rankings, diplomas", scene: "narrow-view" },
  { zh: "太窄了", en: "Too narrow", scene: "narrow-view" },

  { zh: "读书真正的价值", en: "The real value of studying", scene: "armor" },
  { zh: "不是那张纸", en: "isn't the paper", scene: "armor" },
  { zh: "而是你练出来的本事", en: "is the ability you build", scene: "armor" },
  { zh: "能坐得住、能扛得住、能想得通", en: "to sit still, to endure, to understand", scene: "armor" },

  { zh: "那些能在社会上站稳的人", en: "Those who stand firm in society", scene: "endure" },
  { zh: "未必最聪明", en: "aren't always the smartest", scene: "endure" },
  { zh: "但一定是最能扛的", en: "but they're always the most resilient", scene: "endure" },
  { zh: "扛得住枯燥，扛得住压力", en: "They endure boredom and pressure", scene: "endure" },

  { zh: "你现在做的每一道题", en: "Every problem you solve now", scene: "forging" },
  { zh: "背的每一个单词", en: "every word you memorize", scene: "forging" },
  { zh: "都是在给自己锻造一副铠甲", en: "is forging armor for yourself", scene: "forging" },
  { zh: "等你走出校门那天，才真正派上用场", en: "It only matters after you leave school", scene: "forging" },

  // ── 二、没有退路的人有多难 ──
  { zh: "二、没有退路的人有多难", en: "2. How hard it is with no fallback", scene: "ch2", chapter: 2 },

  { zh: "十七八岁觉得读书苦", en: "At 17, studying feels hard", scene: "two-paths" },
  { zh: "二十多岁开始觉得生活苦", en: "At 25, life feels harder", scene: "two-paths" },
  { zh: "区别是什么", en: "What's the difference?", scene: "two-paths" },

  { zh: "读书的苦，是有终点的", en: "Studying has an end date", scene: "deadline" },
  { zh: "熬过高考，熬过大学，就结束了", en: "Survive college, it's over", scene: "deadline" },
  { zh: "生活的苦，没有终点", en: "Life's suffering has no end", scene: "deadline" },
  { zh: "你只能一天一天地熬", en: "You just endure day by day", scene: "deadline" },

  { zh: "我见过一个男生", en: "I knew a guy", scene: "factory-boy" },
  { zh: "高中没读完就出去打工", en: "who dropped out of high school", scene: "factory-boy" },
  { zh: "他去工厂，一天站十二小时", en: "Factory: 12 hours standing", scene: "factory-boy" },
  { zh: "他去送外卖，夏天晒脱皮", en: "Delivery: sunburned in summer", scene: "factory-boy" },
  { zh: "他去学手艺，师傅骂他笨", en: "Apprentice: called stupid by the master", scene: "factory-boy" },

  { zh: "他不是不努力", en: "He wasn't lazy", scene: "no-choice" },
  { zh: "他比很多人都努力", en: "He worked harder than most", scene: "no-choice" },
  { zh: "但他没有选择", en: "But he had no choice", scene: "no-choice" },

  { zh: "最后悔的不是没考上大学", en: "His biggest regret wasn't missing college", scene: "regret" },
  { zh: "而是当初可以再撑一下", en: "but not pushing through just a little more", scene: "regret" },
  { zh: "却选择了放弃", en: "and choosing to give up instead", scene: "regret" },

  // ── 三、不是学不会，是没用心 ──
  { zh: "三、不是学不会，是没用心", en: "3. It's not inability, it's effort", scene: "ch3", chapter: 3 },

  { zh: "你可能在想", en: "You might be thinking", scene: "self-doubt" },
  { zh: ""我就是学不会，脑子没别人好使"", en: "\"I just can't learn, I'm not smart enough\"", scene: "self-doubt" },
  { zh: "真的是这样吗", en: "Is that really true?", scene: "self-doubt" },

  { zh: "你所谓的学不会", en: "Your so-called 'can't learn'", scene: "attitude" },
  { zh: "到底是能力问题，还是态度问题", en: "Is it ability or attitude?", scene: "attitude" },
  { zh: "你上课是在听讲，还是在走神", en: "Are you listening or daydreaming?", scene: "attitude" },
  { zh: "你做作业是在思考，还是在应付", en: "Thinking or just going through motions?", scene: "attitude" },

  { zh: "很多时候不是你不行", en: "Often it's not that you can't", scene: "gap" },
  { zh: "是你没给自己机会去行", en: "you never gave yourself the chance", scene: "gap" },
  { zh: "那些你觉得聪明的同学", en: "Those 'smart' classmates", scene: "gap" },
  { zh: "只是比你多坚持了一会儿", en: "just persisted a bit longer", scene: "gap" },

  { zh: "这个差距不是老天定的", en: "This gap isn't fate", scene: "habits" },
  { zh: "是你自己一天一天攒出来的", en: "it's built day by day", scene: "habits" },
  { zh: "好消息是既然是你攒的", en: "Good news: since you built it", scene: "habits" },
  { zh: "你也能改，现在开始还来得及", en: "you can change it. It's not too late", scene: "habits" },

  // ── 四、父母为什么总催你 ──
  { zh: "四、父母为什么总催你", en: "4. Why parents always push you", scene: "ch4", chapter: 4 },

  { zh: "你有没有烦过", en: "Ever been annoyed?", scene: "parents-nag" },
  { zh: "为什么妈妈总催你写作业", en: "Why does mom always nag about homework?", scene: "parents-nag" },
  { zh: "为什么爸爸总问你考了多少分", en: "Why does dad always ask your scores?", scene: "parents-nag" },

  { zh: "我告诉你一个真相", en: "Let me tell you the truth", scene: "truth" },
  { zh: "那些看起来开明的父母", en: "Those 'open-minded' parents", scene: "truth" },
  { zh: "要么家里有矿输得起", en: "either have wealth to lose", scene: "truth" },
  { zh: "要么还没被生活教训过", en: "or haven't been taught by life yet", scene: "truth" },

  { zh: "而你的父母", en: "But your parents", scene: "parents-know" },
  { zh: "太清楚这个社会有多残酷", en: "know exactly how cruel society is", scene: "parents-know" },
  { zh: "他们见过学历不够被拒的人", en: "They've seen rejected for low degrees", scene: "parents-know" },
  { zh: "见过没有技能被淘汰的人", en: "seen eliminated for lack of skills", scene: "parents-know" },

  { zh: "所以他们宁愿你现在烦他们", en: "So they'd rather you hate them now", scene: "parents-love" },
  { zh: "也不愿你将来被生活为难", en: "than let life bully you later", scene: "parents-love" },
  { zh: "他们推着你往前走", en: "They push you forward", scene: "parents-love" },
  { zh: "不是不爱你，是太爱你", en: "not from lack of love, but too much love", scene: "parents-love" },
  { zh: "怕你还没学会飞就掉下去", en: "afraid you'll fall before learning to fly", scene: "parents-love" },

  // ── 五、你现在的努力在干什么 ──
  { zh: "五、你现在的努力在干什么", en: "5. What your effort is really doing", scene: "ch5", chapter: 5 },

  { zh: "你可能觉得每天学的都没用", en: "You might think what you learn is useless", scene: "useless?" },
  { zh: "函数有什么用", en: "What's the use of functions?", scene: "useless?" },
  { zh: "文言文有什么用", en: "What's the use of classical Chinese?", scene: "useless?" },

  { zh: "它们确实可能这辈子用不上", en: "You may never use them in life", scene: "real-gain" },
  { zh: "但你练出来的能力会跟你一辈子", en: "But the skills last forever", scene: "real-gain" },
  { zh: "专注力让你以后能沉下心", en: "Focus helps you stay calm later", scene: "real-gain" },
  { zh: "逻辑思维让你能想明白问题", en: "Logic helps you solve problems", scene: "real-gain" },
  { zh: "抗压能力让你被打趴下能爬起来", en: "Resilience helps you get back up", scene: "real-gain" },

  { zh: "它不是一张文凭", en: "It's not a diploma", scene: "os" },
  { zh: "而是一套操作系统", en: "it's an operating system", scene: "os" },
  { zh: "装在你脑子里走到哪带着", en: "installed in your mind, goes everywhere", scene: "os" },
  { zh: "谁也拿不走", en: "No one can take it", scene: "os" },

  // ── 结尾 ──
  { zh: "别再觉得读书是替父母读的", en: "Stop thinking you study for your parents", scene: "for-you" },
  { zh: "你是替自己读的", en: "You study for yourself", scene: "for-you" },

  { zh: "是为了将来多留一条路", en: "To leave yourself another path", scene: "future" },
  { zh: "是为了面对不喜欢的工作时", en: "So when facing a job you hate", scene: "future" },
  { zh: "有底气说不", en: "you have the courage to say no", scene: "future" },

  { zh: "是为了想保护的人有能力去保护", en: "To protect those you love", scene: "future" },
  { zh: "是为了回头看今天", en: "So looking back at today", scene: "future" },
  { zh: "不会说当初为什么不再撑一下", en: "you won't say 'why didn't I hold on'", scene: "future" },

  { zh: "这个世界很公平", en: "This world is fair", scene: "fair" },
  { zh: "你把时间花在哪里收获就在哪里", en: "You reap what you sow", scene: "fair" },
  { zh: "它只看你愿不愿意坐下来", en: "It only asks if you're willing to sit down", scene: "fair" },
  { zh: "把该做的事情做好", en: "and do what needs to be done", scene: "fair" },
  { zh: "如果你愿意，时间一定不会亏待你", en: "If you are, time won't let you down", scene: "fair" },
];

// ─────────────────────────────────────────────────────────────────────────
// 中央图配置：每张图的中文文生图 prompt，方便你用即梦/豆包/midjourney 生成。
// 风格统一：暖色纸质感、简约扁平插画、留白多、主体居中、不要写实照片。
// 你也可以全部换成手绘 icon、3D 黏土、写实卡通等任意风格——只要风格统一即可。
// ─────────────────────────────────────────────────────────────────────────
window.SCENES = {
  hook: {
    prompt: "扁平插画。画面中央一个年轻人背对观众，面前是一扇巨大的门，门上挂着一把锁。门两侧是高墙，墙外隐约可见开阔的天空和道路。年轻人脚下散落着几本书。米黄底色，主体黑色简约线条，门和锁用红色点缀。",
    label: "",
    overlays: [
      { text: "读书无用？", xPct: 62, yPct: 28, style: "os-callout-red", animation: "oa-fly-right" },
    ]
  },
  ch1: { prompt: "章节封面卡：米黄底，居中大字 \"一\" 用毛笔感衬线字体，下方一行小字\"读书不是为了考试\"，极简留白。", label: "" },
  "narrow-view": {
    prompt: "扁平插画。画面中央一个小人被三个圆形气泡包围，气泡里分别写着\"分数\"\"排名\"\"文凭\"（用空白方框代替文字），小人表情困惑。画面上方大面积留白。米黄底色，气泡浅灰色，小人黑色。",
    label: ""
  },
  armor: {
    prompt: "扁平插画。画面中央一个小人正在穿上一副铠甲，铠甲由书本、铅笔、尺子等学习工具拼接而成。小人身后有一道微光。画面四周留白。米黄底色，铠甲深棕色，工具黑色。",
    label: "",
    overlays: [
      { text: "铠甲", xPct: 50, yPct: 35, style: "os-stamp", animation: "oa-stamp-hit" },
    ]
  },
  endure: {
    prompt: "扁平插画。画面左侧一个小人在暴风雨中站立，身体微微前倾但不倒。风雨用斜线条表示。画面右侧大面积留白。米黄底色，小人黑色，风雨灰色。",
    label: ""
  },
  forging: {
    prompt: "扁平插画。画面中央一个铁砧，上面放着一本打开的书，一只锤子正在敲打书本，敲打出星星点点的火花。画面四周留白。米黄底色，铁砧深灰，书本白色，锤子黑色，火花红橙色。",
    label: ""
  },
  ch2: { prompt: "章节封面卡：米黄底，居中大字 \"二\"，下方小字 \"没有退路的人有多难\"。极简留白。", label: "" },
  "two-paths": {
    prompt: "扁平插画。画面分两半：左边是一个学生坐在课桌前，头顶有问号；右边是一个成年人背着沉重的行李在爬坡。中间用一条竖线分隔。米黄底色，左半浅色调，右半深色调。",
    label: ""
  },
  deadline: {
    prompt: "扁平插画。画面中央两个沙漏并排：左边一个沙漏上方标着\"读书\"（用空白框代替），沙子快流完；右边一个沙漏上方标着\"生活\"，沙子还有很多。米黄底色，沙漏黑色线条。",
    label: "",
    overlays: [
      { text: "有终点", xPct: 28, yPct: 65, style: "os-tag-pill", delay: 0 },
      { text: "没终点", xPct: 72, yPct: 65, style: "os-callout-red", delay: 200 },
    ]
  },
  "factory-boy": {
    prompt: "扁平插画。画面中央一个小人站在三条路的交叉口：一条通向工厂（烟囱冒烟），一条通向外卖箱，一条通向工具箱。小人背影，表情看不到。米黄底色，各元素黑色线条，烟囱烟灰色。",
    label: ""
  },
  "no-choice": {
    prompt: "扁平插画。画面中央一个小人被几根绳子拉着，绳子另一端分别连着\"房租\"\"账单\"\"生活\"（用空白标签代替）。小人想往左走，但被拉向右边。米黄底色，绳子灰色，小人黑色。",
    label: ""
  },
  regret: {
    prompt: "扁平插画。画面中央一面破碎的镜子，镜子里映出一个年轻人的半张脸，另一半是空白。镜子裂缝用红色线条强调。画面四周留白。米黄底色，镜框黑色。",
    label: "",
    overlays: [
      { text: "当初为什么不再撑一下", xPct: 50, yPct: 70, style: "os-handwrite", animation: "oa-fade", delay: 300 },
    ]
  },
  ch3: { prompt: "章节封面卡：米黄底，居中大字 \"三\"，下方小字 \"不是学不会，是没用心\"。极简留白。", label: "" },
  "self-doubt": {
    prompt: "扁平插画。画面中央一个小人坐在书桌前，头顶有一个巨大的问号气泡。书桌上堆着书本和试卷。小人双手抱头，表情沮丧。米黄底色，问号红色，其他黑色。",
    label: ""
  },
  attitude: {
    prompt: "扁平插画。画面分左右两半：左边一个小人趴在桌上睡觉，书本打开着；右边一个小人坐得笔直，认真做笔记。中间用竖线分隔。米黄底色，左边灰色调，右边暖色调。",
    label: ""
  },
  gap: {
    prompt: "扁平插画。画面中央两个小人并排站立，左边矮一点，右边高一点。两人脚下各有一堆积木，右边的积木明显多几块。积木代表\"坚持\"（用空白方块）。米黄底色，积木深棕色，小人黑色。",
    label: ""
  },
  habits: {
    prompt: "扁平插画。画面中央一条向上的螺旋阶梯，每一级台阶上有一个小图标：闹钟、书本、铅笔、时钟。阶梯顶端有一扇门透出光亮。米黄底色，阶梯深棕色，图标黑色。",
    label: "",
    overlays: [
      { text: "习惯", xPct: 50, yPct: 30, style: "os-stamp", animation: "oa-stamp-hit" },
    ]
  },
  ch4: { prompt: "章节封面卡：米黄底，居中大字 \"四\"，下方小字 \"父母为什么总催你\"。极简留白。", label: "" },
  "parents-nag": {
    prompt: "扁平插画。画面中央一个小人坐在书桌前，身后站着两个大人（父母），父母头顶各有一个对话气泡。小人双手捂耳朵。米黄底色，父母灰色，小人黑色。",
    label: ""
  },
  truth: {
    prompt: "扁平插画。画面中央一扇门半开着，门缝透出光线。门上贴着一张纸条（空白）。门外面站着一个好奇的小人。米黄底色，门深棕色，光线金黄色。",
    label: ""
  },
  "parents-know": {
    prompt: "扁平插画。画面左侧两个大人（父母）手牵手站立，表情坚定。他们身后是一片风雨场景（灰色天空、雨滴）。画面右侧留白。米黄底色，父母黑色线条，风雨灰色。",
    label: ""
  },
  "parents-love": {
    prompt: "扁平插画。画面中央一个小人站在悬崖边学飞，身后一只大手（父母）托着他的后背。小人张开双臂，面朝天空。米黄底色，手灰色，小人黑色，天空浅蓝。",
    label: "",
    overlays: [
      { text: "爱", xPct: 30, yPct: 40, style: "os-callout-red", animation: "oa-zoom", delay: 200 },
    ]
  },
  ch5: { prompt: "章节封面卡：米黄底，居中大字 \"五\"，下方小字 \"你现在的努力在干什么\"。极简留白。", label: "" },
  "useless?": {
    prompt: "扁平插画。画面中央一个小人坐在课桌前，桌上摆着数学书、语文书、英语书。小人头顶有三个问号气泡。书本上各有一个\"?\"图标。米黄底色，书本不同颜色，小人黑色。",
    label: ""
  },
  "real-gain": {
    prompt: "扁平插画。画面中央一个小人正在攀登一座山，山坡上散落着四个发光的宝石，分别标着\"专注\"\"逻辑\"\"抗压\"\"自律\"（用空白方框代替文字）。山顶有光芒。米黄底色，宝石金色，山灰色。",
    label: ""
  },
  "os": {
    prompt: "扁平插画。画面中央一个大脑的轮廓，大脑内部是一个精密的齿轮系统和电路板纹路。大脑周围有四个小图标连接着：眼睛、灯泡、齿轮、盾牌。米黄底色，大脑轮廓黑色，内部深蓝色。",
    label: "",
    overlays: [
      { text: "操作系统", xPct: 50, yPct: 25, style: "os-stamp", animation: "oa-stamp-hit" },
    ]
  },
  "for-you": {
    prompt: "扁平插画。画面中央一个小人站在镜子前，镜子里的自己穿着毕业服、手持证书。小人和镜中人四目相对。画面四周留白。米黄底色，镜框金色，人物黑色。",
    label: ""
  },
  future: {
    prompt: "扁平插画。画面中央一条笔直的道路通向远方，路两旁是路灯。路尽头是一扇敞开的门，门内透出温暖的光。路上有一个小人在行走。米黄底色，道路灰色，灯光金黄色。",
    label: "",
    overlays: [
      { text: "路", xPct: 50, yPct: 45, style: "os-callout", animation: "oa-fade", delay: 200 },
    ]
  },
  fair: {
    prompt: "扁平插画。画面中央一个天平，左边盘子里放着沙漏（代表时间），右边盘子里放着一堆金币和证书。天平基本平衡。画面四周大面积留白。米黄底色，天平黑色线条，金币金黄色。",
    label: "",
    overlays: [
      { text: "时间", xPct: 28, yPct: 50, style: "os-tag-pill", delay: 0 },
      { text: "收获", xPct: 72, yPct: 50, style: "os-tag-pill", delay: 200 },
    ]
  },
};
