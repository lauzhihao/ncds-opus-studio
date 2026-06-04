/* build_icons.mjs — 从 Tabler Icons 批量生成 icons.js(吴道子自由图标库)
 *
 * 为什么不直接 npm 依赖 xicons/@vicons:那是 Vue/React 组件库 + 聚合多集风格杂,
 * 和无框架 puppeteer 录屏管线不搭。这里走「构建期转换」:锁定 Tabler 单集(统一线性圆角),
 * fetch 需要的 svg -> 规范化(去占位 path / 去多余属性 / 统一外壳 currentColor + 粗线圆角)
 * -> 生成纯数据 icons.js。产物零运行时依赖,风格和小黑人同源。
 *
 * 跑法(需联网): node build_icons.mjs   -> 覆盖同目录 icons.js
 * 加图标:在 MANIFEST 加一项(tabler 名 + 中文 name/cat/keywords/enter)再跑。
 * keywords 是中文语义触发词,供吴道子 agent 按柳永脚本关键词自动选图(Step3)。
 */
import { writeFile, readFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';

const TABLER_VER = '3.44.0';
const BASE = `https://unpkg.com/@tabler/icons@${TABLER_VER}/icons/outline/`;
const STROKE = 2;            // 统一描边宽度(Tabler 原生 2/24 比例,接近手绘强调线条)
const CACHE = '/tmp/tabler-cache';   // 原始 svg 缓存,避免反复联网(不进 repo)
const OUT = new URL('./icons.js', import.meta.url);

// 图标清单:tabler=Tabler outline 文件名;id=库内引用名(默认同 tabler);
// keywords=中文语义触发词(认知口播/职场/成长场景);enter=入场动效。
const MANIFEST = [
  // —— 时间 / 节奏 ——
  { tabler: 'clock', name: '时钟', cat: '时间', enter: 'pop', keywords: ['时间', '来不及', '紧迫', '拖延', '效率', '赶时间'] },
  { tabler: 'hourglass', name: '沙漏', cat: '时间', enter: 'pop', keywords: ['等待', '流逝', '拖延', '耗时间', '熬'] },
  { tabler: 'bolt', name: '闪电', cat: '时间', enter: 'pop', keywords: ['快', '高效', '爆发', '立刻', '瞬间', '提速'] },
  { tabler: 'calendar-event', name: '日程', cat: '时间', enter: 'pop', keywords: ['计划', '日程', '安排', 'deadline', '排期'] },

  // —— 能量 / 状态 ——
  { tabler: 'battery-2', name: '低电量', cat: '能量', enter: 'pop', keywords: ['精力', '内耗', '累', '耗尽', '没电', '疲惫'] },
  { tabler: 'flame', name: '火焰', cat: '能量', enter: 'rise', keywords: ['动力', '热情', '燃', '爆', '热度', '冲劲'] },
  { tabler: 'mood-sad', name: '低落', cat: '能量', enter: 'pop', keywords: ['难过', '沮丧', '焦虑', '崩溃', '情绪低'] },
  { tabler: 'mood-smile', name: '开心', cat: '能量', enter: 'pop', keywords: ['开心', '轻松', '满足', '爽', '舒服'] },

  // —— 趋势 / 成长 ——
  { tabler: 'arrow-up', name: '上升', cat: '趋势', enter: 'rise', keywords: ['成长', '提升', '上涨', '进步', '变好'] },
  { tabler: 'arrow-down', name: '下降', cat: '趋势', enter: 'pop', keywords: ['下跌', '衰退', '退步', '减少', '变差'] },
  { tabler: 'trending-up', name: '上升趋势', cat: '趋势', enter: 'rise', keywords: ['增长', '走高', '复利', '越来越好', '曲线上扬'] },
  { tabler: 'trending-down', name: '下降趋势', cat: '趋势', enter: 'pop', keywords: ['下滑', '走低', '亏', '下行', '颓势'] },
  { tabler: 'rocket', name: '火箭', cat: '趋势', enter: 'rise', keywords: ['起飞', '突破', '爆发增长', '逆袭', '快速成长'] },
  { tabler: 'chart-line', name: '增长曲线', cat: '趋势', enter: 'rise', keywords: ['数据', '增长', '复盘', '业绩', '曲线'] },
  { tabler: 'stairs-up', name: '进阶', cat: '趋势', enter: 'rise', keywords: ['台阶', '进阶', '一步步', '升级', '层层'] },

  // —— 认知 / 思维 ——
  { tabler: 'bulb', name: '灯泡', cat: '认知', enter: 'pop', keywords: ['灵感', '顿悟', '想法', '点子', '开窍', '明白'] },
  { tabler: 'brain', name: '大脑', cat: '认知', enter: 'pop', keywords: ['认知', '思维', '想太多', '脑子', '理性', '心智'] },
  { tabler: 'eye', name: '眼睛', cat: '认知', enter: 'draw', keywords: ['看见', '洞察', '被看见', '观察', '关注', '发现'] },
  { tabler: 'puzzle', name: '拼图', cat: '认知', enter: 'pop', keywords: ['想通', '解决', '关键一块', '拼上', '逻辑'] },
  { tabler: 'book', name: '书', cat: '认知', enter: 'pop', keywords: ['学习', '知识', '读书', '方法论', '原理'] },
  { tabler: 'bookmark', name: '书签', cat: '认知', enter: 'pop', keywords: ['记住', '收藏', '重点', '标记', '划重点'] },
  { tabler: 'magnet', name: '磁铁', cat: '认知', enter: 'pop', keywords: ['吸引', '聚焦', '抓住', '磁场', '注意力'] },
  { tabler: 'zoom-in', name: '放大镜', cat: '认知', enter: 'pop', keywords: ['看清', '深究', '细节', '深挖', '聚焦'] },

  // —— 判断 / 对错 ——
  { tabler: 'check', name: '对勾', cat: '判断', enter: 'draw', keywords: ['正确', '认同', '该这样', '搞定', '完成', '没问题'] },
  { tabler: 'x', name: '红叉', cat: '判断', enter: 'pop', keywords: ['错误', '否定', '别这样', '不对', '禁止', '失败'] },
  { tabler: 'alert-triangle', name: '警告', cat: '判断', enter: 'pop', keywords: ['警惕', '注意', '坑', '危险', '小心', '风险'] },
  { tabler: 'alert-circle', name: '提醒', cat: '判断', enter: 'pop', keywords: ['注意', '提醒', '留意', '重点', '别忽略'] },
  { tabler: 'ban', name: '禁止', cat: '判断', enter: 'pop', keywords: ['别', '不要', '禁止', '杜绝', '停止', '戒'] },
  { tabler: 'thumb-up', name: '点赞', cat: '判断', enter: 'pop', keywords: ['赞同', '好', '认可', '支持', '推荐'] },
  { tabler: 'thumb-down', name: '反对', cat: '判断', enter: 'pop', keywords: ['反对', '差', '不行', '否决', '踩'] },
  { tabler: 'shield', name: '盾牌', cat: '判断', enter: 'pop', keywords: ['保护', '防御', '安全', '底线', '边界'] },
  { tabler: 'question-mark', name: '问号', cat: '判断', enter: 'pop', keywords: ['疑问', '反问', '为什么', '真的吗', '凭什么'] },

  // —— 目标 / 成就 ——
  { tabler: 'target', name: '靶心', cat: '目标', enter: 'pop', keywords: ['目标', '聚焦', '精准', '重点', '命中', '方向'] },
  { tabler: 'flag', name: '旗帜', cat: '目标', enter: 'rise', keywords: ['里程碑', '目标达成', '小目标', '插旗', '阶段'] },
  { tabler: 'trophy', name: '奖杯', cat: '目标', enter: 'rise', keywords: ['成就', '赢', '冠军', '成功', '拿下'] },
  { tabler: 'medal', name: '奖牌', cat: '目标', enter: 'rise', keywords: ['荣誉', '认可', '名次', '上榜', '获奖'] },
  { tabler: 'crown', name: '王冠', cat: '目标', enter: 'rise', keywords: ['第一', '顶尖', '王者', '头部', '领先'] },
  { tabler: 'star', name: '星', cat: '目标', enter: 'pop', keywords: ['重点', '高光', '优秀', '加分', '关键', '亮点'] },
  { tabler: 'mountain', name: '高山', cat: '目标', enter: 'rise', keywords: ['挑战', '难关', '攀登', '大目标', '高峰'] },

  // —— 价值 / 钱 ——
  { tabler: 'currency-yen', name: '钱', cat: '价值', enter: 'pop', keywords: ['钱', '收入', '价值', '工资', '涨薪', '回报'] },
  { tabler: 'coin', name: '硬币', cat: '价值', enter: 'pop', keywords: ['赚钱', '积累', '本钱', '收益', '一分一毫'] },
  { tabler: 'gift', name: '礼物', cat: '价值', enter: 'pop', keywords: ['回报', '红利', '惊喜', '福利', '给予'] },
  { tabler: 'diamond', name: '钻石', cat: '价值', enter: 'pop', keywords: ['价值', '稀缺', '高价值', '硬通货', '珍贵'] },

  // —— 沟通 / 关系 ——
  { tabler: 'message-circle', name: '对话气泡', cat: '沟通', enter: 'rise', keywords: ['沟通', '话术', '回应', '反击', '表达', '回怼'] },
  { tabler: 'users', name: '人群', cat: '沟通', enter: 'pop', keywords: ['社交', '团队', '人脉', '关系', '大家', '群体'] },
  { tabler: 'heart', name: '心', cat: '沟通', enter: 'pop', keywords: ['情绪', '在意', '关系', '喜欢', '用心', '感受'] },
  { tabler: 'speakerphone', name: '喇叭', cat: '沟通', enter: 'pop', keywords: ['宣传', '喊话', '发声', '表态', '主张', 'announce'] },

  // —— 受限 / 突破 ——
  { tabler: 'lock', name: '锁', cat: '突破', enter: 'pop', keywords: ['受限', '卡住', '锁死', '困住', '限制', '封闭'] },
  { tabler: 'key', name: '钥匙', cat: '突破', enter: 'pop', keywords: ['解法', '关键', '突破口', '答案', '钥匙', '破局'] },
  { tabler: 'door', name: '门', cat: '突破', enter: 'pop', keywords: ['机会', '出口', '入口', '选择', '一扇门'] },
  { tabler: 'bomb', name: '炸弹', cat: '突破', enter: 'pop', keywords: ['危机', '踩坑', '雷', '隐患', '爆雷', '风险'] },

  // —— 工具 / 机制 ——
  { tabler: 'tool', name: '工具', cat: '工具', enter: 'pop', keywords: ['工具', '方法', '手段', '招式', '套路'] },
  { tabler: 'settings', name: '齿轮', cat: '工具', enter: 'pop', keywords: ['机制', '系统', '底层逻辑', '原理', '运转'] },
  { tabler: 'route', name: '路线', cat: '工具', enter: 'draw', keywords: ['路径', '方法', '步骤', '路线图', '怎么走'] },
];

// 入场动效(WAAPI one-shot),与 motions.js 数据驱动哲学一致。
const ENTERS_SRC = `{
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
    // draw:真正的描边「手画」由 player 给 path 设 pathLength + stroke-dashoffset 驱动;此处淡入兜底。
    draw: { name: '描边手画',
      keyframes: [{ opacity: 0 }, { opacity: 1 }],
      options: { duration: 600, easing: 'ease-out', fill: 'both' } },
  }`;

// 规范化:抽出 <svg> 内层、去 Tabler 透明占位 path、压缩空白。外壳由 W() 统一套。
function normalize(raw) {
  let inner = raw.replace(/[\s\S]*?<svg[^>]*>/, '').replace(/<\/svg>[\s\S]*/, '');
  inner = inner.replace(/<path[^>]*M0 0h24v24H0z[^>]*\/>/g, ''); // 透明背景占位
  inner = inner.replace(/>\s+</g, '><').replace(/\s{2,}/g, ' ').trim();
  return inner;
}

async function getSvg(name) {
  const cacheFile = `${CACHE}/${name}.svg`;
  if (existsSync(cacheFile)) return readFile(cacheFile, 'utf8');
  const res = await fetch(BASE + name + '.svg');
  if (!res.ok) return null;
  const raw = await res.text();
  await writeFile(cacheFile, raw);
  return raw;
}

async function main() {
  if (!existsSync(CACHE)) await mkdir(CACHE, { recursive: true });
  const rows = [];
  const fails = [];
  for (const it of MANIFEST) {
    const raw = await getSvg(it.tabler);
    if (!raw) { fails.push(it.tabler); continue; }
    const inner = normalize(raw);
    if (!inner) { fails.push(it.tabler + '(empty)'); continue; }
    const id = it.id || it.tabler;
    rows.push(
      `    ${JSON.stringify(id)}: { name: ${JSON.stringify(it.name)}, cat: ${JSON.stringify(it.cat)}, ` +
      `enter: ${JSON.stringify(it.enter)}, keywords: ${JSON.stringify(it.keywords)}, svg: W(${JSON.stringify(inner)}) },`
    );
  }

  const out =
`/* icons.js — 自由图标库(吴道子 · 与 motions.js 平级的「画面层」)
 *
 * AUTO-GENERATED by build_icons.mjs from Tabler Icons v${TABLER_VER}(MIT)。勿手改;改 MANIFEST 后重跑。
 * icon 独立于角色、漂浮在舞台、为强调关键词飞入(对标号的电池/时钟/箭头那一层);
 * 不是 rig-spec 的 prop/bubble(那是角色手持、跟着人走的)。
 * 契约:ICONS[id]={name,cat,keywords:[中文],enter,svg};Agent 只输出 icon id。
 */
(function () {
  const W = (inner) =>
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${STROKE}"' +
    ' stroke-linecap="round" stroke-linejoin="round">' + inner + '</svg>';

  const ICONS = {
${rows.join('\n')}
  };

  const ENTERS = ${ENTERS_SRC};

  window.RIG_ICONS = ICONS;
  window.RIG_ICON_ENTERS = ENTERS;
  window.RIG_ICON_LIST = Object.keys(ICONS).map((id) => ({ id, ...ICONS[id] }));
})();
`;

  await writeFile(OUT, out);
  console.log(`[build_icons] wrote ${rows.length} icons -> icons.js (Tabler v${TABLER_VER})`);
  if (fails.length) console.log(`[build_icons] FAILED ${fails.length}: ${fails.join(', ')}`);
  else console.log('[build_icons] all icons fetched OK');
}

main().catch((e) => { console.error('[build_icons] error:', e.message); process.exit(1); });
