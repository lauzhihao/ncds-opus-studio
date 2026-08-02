// 画布的 agent 表现层配置（止损重构核心）。
//
// 画布底层仍是一条 final_preview recipe 的 job（input→asr→rw→lines→storyboard→image→tts→
// preview→render→download），这里把这些 engine 节点**按 agent 分组重新呈现**为 6 个有序
// agent。agent 节点是纯前端分组：run/cancel/select 仍打到底层 engine 节点
// （/jobs/{id}/nodes/{node}/run），真实依赖顺序由后端 recipe 定义。
//
// 鬼谷子是唯一没有 engine 步的 agent（virtual member），v1 是薄选题 gate：
// 沈括(asr) 完成 → 鬼谷子录入/确认选题角度 → 让柳永(rw) 出稿。

import {
  Crown,
  Lightbulb,
  Music,
  Palette,
  PenLine,
  Radar,
  type LucideIcon,
} from 'lucide-react';

import type { NodeState, NodeStatus } from '../api/types';
import { friendlyProgressText } from '../utils/progress';

export type AgentId = 'shenkuo' | 'guiguzi' | 'liuyong' | 'wudaozi' | 'boya' | 'render';

export interface AgentMember {
  // 底层 job 节点名；virtual 成员（鬼谷子）没有 engine 节点，node 仅作占位 key。
  node: string;
  // 成员步骤展示名（agent 内的子阶段）。
  label: string;
  // true = 无 engine 节点的纯前端 gate（鬼谷子选题）。
  virtual?: boolean;
}

export interface AgentDef {
  id: AgentId;
  name: string; // 沈括 / 鬼谷子 ...
  role: string; // 采集/转写入库 ...
  description: string;
  icon: LucideIcon;
  // 隐藏前置步骤：归属本 agent 的底层节点，但不作为抽屉 tab 暴露给用户。
  preflight?: AgentMember[];
  members: AgentMember[];
}

// 6 个有序 agent。顺序即产线：沈括→鬼谷子→柳永→吴道子→伯牙→卧龙。
export const AGENTS: AgentDef[] = [
  {
    id: 'shenkuo',
    name: '沈括',
    role: '采集供料',
    description: '采集对标作品的文案/评论/音轨/数据，作为创作素材。',
    icon: Radar,
    // 采集源不再是抽屉标签页：进画布即自动采集（后端有采集缓存），沈括只展示采集成果。
    members: [
      { node: 'asr', label: '采集成果' },
    ],
  },
  {
    id: 'guiguzi',
    name: '鬼谷子',
    role: '选题',
    description: '基于素材确定衍生作品的选题与角度。',
    icon: Lightbulb,
    members: [{ node: 'guiguzi', label: '选题', virtual: true }],
  },
  {
    id: 'liuyong',
    name: '柳永',
    role: '编剧成稿',
    description: '多模型改写并质检口播稿，定稿后交给吴道子做画面。',
    icon: PenLine,
    members: [
      { node: 'rw', label: '改写' },
    ],
  },
  {
    id: 'wudaozi',
    name: '吴道子',
    role: '画面',
    description: '负责视觉方案、全片背景、前景素材设计与画面资产检查。',
    icon: Palette,
    // lines 是 storyboard 所需的隐藏视觉准备，不再作为用户主入口暴露。
    preflight: [{ node: 'lines', label: '视觉准备' }],
    members: [
      { node: 'storyboard', label: '视觉方案' },
      { node: 'image', label: '画面资产' },
    ],
  },
  {
    id: 'boya',
    name: '伯牙',
    role: '声音',
    description: '负责配音合成、试听、重试与音频下载。',
    icon: Music,
    members: [{ node: 'tts', label: '声音结果' }],
  },
  {
    id: 'render',
    name: '卧龙',
    role: '统筹出片',
    description: '统筹成片检查、最终渲染与 MP4 导出。',
    icon: Crown,
    members: [
      { node: 'preview', label: '成片检查' },
      { node: 'render', label: '渲染出片' },
    ],
  },
];

// —— 节点 → agent 反查 / 推进链 ——
// 画布按用户顺序（沈括→鬼谷子→柳永→吴道子→伯牙→卧龙）展示，但**推进由引擎真实
// 数据依赖驱动**：final_preview 链是 storyboard→image→tts，所以吴道子先补齐画面资产，
// 再交给伯牙配音。

// 底层 job 节点 → 所属 agentId（含 virtual 的 guiguzi）。
export const AGENT_BY_NODE: Record<string, AgentId> = (() => {
  const m: Record<string, AgentId> = {};
  for (const a of AGENTS) {
    for (const mem of [...(a.preflight ?? []), ...a.members]) m[mem.node] = a.id;
  }
  // input 不再是沈括的抽屉成员（采集源 tab 已移除、改为进画布自动采集），但它仍是
  // 沈括名下的采集源持有节点：保留映射，使首页进度灯 / NODE_ORDER 的归属与之前一致。
  m.input = 'shenkuo';
  return m;
})();

// 引擎真实 NEXT 链（鬼谷子 gate 插在 asr 与 rw 之间）。末步 download 无 next。
// 产品上 lines 是吴道子的隐藏 preflight。
export const NODE_NEXT: Record<string, string | null> = {
  input: 'asr',
  asr: 'guiguzi',
  guiguzi: 'rw',
  rw: 'lines',
  lines: 'storyboard',
  storyboard: 'image',
  image: 'tts',
  tts: 'preview',
  preview: 'render',
  // 下载不再单独占页面；渲染完成后在 render 面板直接暴露 MP4 下载。
  render: null,
  download: null,
};

export function agentIndex(id: AgentId): number {
  return AGENTS.findIndex((a) => a.id === id);
}

// 鬼谷子选题 angle 的前端持久化 key（v1 用 localStorage，不落后端；
// 真持久化 + 注入柳永 rewrite 随 guiguzi.run 接引擎后续做）。
export function angleStorageKey(jobId: string): string {
  return `nof:angle:${jobId}`;
}

// 沈括面板「备选题评论」的前端持久化 key：用户在沈括选中的高赞评论（连同所属作品提取文案）
// 存这里（GuiguziItem[]），鬼谷子面板读它出选题。沈括与鬼谷子是不同 agent 抽屉、各自挂载，
// 用 localStorage 跨面板传递（同 angle 的既有做法），解耦且抗刷新/重挂载。
export function guiguziItemsStorageKey(jobId: string): string {
  return `nof:guiguzi:items:${jobId}`;
}

// 最多可选作选题参考的评论数。
export const GUIGUZI_MAX_ITEMS = 5;

// 鬼谷子双栏选题结果里「N 选 1」选定的那个选题（GuiguziTopic）。选定即交柳永出稿，
// 同样走 localStorage（按 jobId）持久化，供柳永后续读取注入。
export function guiguziChosenStorageKey(jobId: string): string {
  return `nof:guiguzi:chosen:${jobId}`;
}

// —— 状态聚合 ——
// 把一组成员状态收敛成 agent 的整体状态，供节点卡徽章 / 连线动画 / 卧龙总览复用。
// 规则：任一 failed→failed；任一活跃(running/queued)→running；全部 done→done；
// 部分 done（已起步但未跑完，且当前无活跃）→running（继续中）；否则 idle。
export function aggregateAgentStatus(statuses: NodeStatus[]): NodeStatus {
  if (statuses.length === 0) return 'idle';
  if (statuses.some((s) => s === 'failed')) return 'failed';
  if (statuses.some((s) => s === 'running' || s === 'queued')) return 'running';
  if (statuses.every((s) => s === 'done')) return 'done';
  if (statuses.some((s) => s === 'done')) return 'running';
  return 'idle';
}

// 取某 agent 各成员当前状态（virtual 鬼谷子：选题已确认→done，否则 idle）。
export function agentMemberStatuses(
  agent: AgentDef,
  jobNodes: Record<string, NodeState> | undefined,
  ctx: { angleConfirmed?: boolean } = {},
): NodeStatus[] {
  if (agent.id === 'render') {
    const previewStatus = jobNodes?.preview?.status ?? 'idle';
    const renderStatus = jobNodes?.render?.status ?? 'idle';
    return agent.members.map((m) => {
      if (m.node === 'preview') return previewStatus === 'done' || renderStatus !== 'idle' ? 'done' : previewStatus;
      if (m.node === 'render') return renderStatus;
      return jobNodes?.[m.node]?.status ?? 'idle';
    });
  }
  return agent.members.map((m) => {
    if (m.virtual) return ctx.angleConfirmed ? 'done' : 'idle';
    return jobNodes?.[m.node]?.status ?? 'idle';
  });
}

function memberStatuses(
  members: AgentMember[],
  jobNodes: Record<string, NodeState> | undefined,
): NodeStatus[] {
  return members.map((m) => jobNodes?.[m.node]?.status ?? 'idle');
}

// agent 整体状态（聚合 + virtual 处理）。
export function agentStatus(
  agent: AgentDef,
  jobNodes: Record<string, NodeState> | undefined,
  ctx: { angleConfirmed?: boolean } = {},
): NodeStatus {
  if (agent.id === 'wudaozi') {
    return wudaoziStatus(jobNodes);
  }
  if (agent.id === 'render') {
    const preview = jobNodes?.preview?.status ?? 'idle';
    const render = jobNodes?.render?.status ?? 'idle';
    if (preview === 'failed') return 'failed';
    if (preview === 'running' || preview === 'queued') return 'running';
    if (render === 'failed') return 'failed';
    if (render === 'running' || render === 'queued') return 'running';
    if (render === 'done') return 'done';
    return 'idle';
  }
  return aggregateAgentStatus([
    ...memberStatuses(agent.preflight ?? [], jobNodes),
    ...agentMemberStatuses(agent, jobNodes, ctx),
  ]);
}

function wudaoziStatus(jobNodes: Record<string, NodeState> | undefined): NodeStatus {
  const rwNode = jobNodes?.rw;
  const linesNode = jobNodes?.lines;
  const lines = linesNode?.status ?? 'idle';
  const storyboard = jobNodes?.storyboard?.status ?? 'idle';
  const image = jobNodes?.image?.status ?? 'idle';
  const rwSelected =
    typeof rwNode?.outputs?.selected_model_id === 'string' &&
    rwNode.outputs.selected_model_id.trim().length > 0;
  const linesFailedBeforeDraftSelected =
    lines === 'failed' &&
    !rwSelected &&
    /draft\.md missing|选定稿模型|选模型/.test(String(linesNode?.error ?? ''));

  if (
    (!linesFailedBeforeDraftSelected && lines === 'failed') ||
    storyboard === 'failed' ||
    image === 'failed'
  ) return 'failed';
  if ([lines, storyboard, image].some((s) => s === 'running' || s === 'queued')) return 'running';
  if (image === 'done') return 'done';

  if (storyboard === 'done') return 'idle';
  if (lines === 'done') return 'idle';
  return 'idle';
}

// —— 作品列表的「设计进度灯」——
// 把底层节点 status 收敛成「当前 agent + 红黄绿灯」，供首页作品卡左下角显示
// （替代原先无信息量的 pipeline_id）。鬼谷子是 virtual gate、无后端节点，自然不参与；
// 等它补上后端步后会自动纳入。

export type ProgressLight = 'red' | 'yellow' | 'green';

export interface JobProgress {
  light: ProgressLight;
  agentName: string;
}

// 按引擎 NEXT 链得到真实节点的有序列表（跳过 virtual 成员，如鬼谷子）。
const NODE_ORDER: string[] = (() => {
  const order: string[] = [];
  const seen = new Set<string>();
  let cur: string | null = 'input';
  while (cur && !seen.has(cur)) {
    seen.add(cur);
    const aid = AGENT_BY_NODE[cur];
    const agent = AGENTS.find((a) => a.id === aid);
    const virtual = [...(agent?.preflight ?? []), ...(agent?.members ?? [])].find((m) => m.node === cur)?.virtual;
    if (!virtual) order.push(cur);
    cur = NODE_NEXT[cur] ?? null;
  }
  return order;
})();

function nodeAgentName(node: string): string {
  const aid = AGENT_BY_NODE[node];
  return AGENTS.find((a) => a.id === aid)?.name ?? '';
}

// 灯色 + 当前 agent 判定优先级：failed(红) > running/queued(黄) > 全 done(绿·已出片)
// > 首个未完成真实节点所属 agent(绿) > 全 idle 则首个 agent(绿)。
export function jobProgress(
  nodeStatus: Record<string, NodeStatus> | undefined,
  domain?: string | null,
): JobProgress {
  const ns = nodeStatus ?? {};
  if (String(domain ?? '').trim().toLowerCase() === 'film') {
    if (ns.asr === 'failed') return { light: 'red', agentName: '沈括' };
    if (ns.asr === 'running' || ns.asr === 'queued') {
      return { light: 'yellow', agentName: '沈括' };
    }
    if (ns.asr === 'done') return { light: 'green', agentName: '解说稿已提取' };
    return { light: 'green', agentName: '沈括' };
  }
  if (ns.render === 'done') return { light: 'green', agentName: '已出片' };
  const failed = NODE_ORDER.find((n) => n !== 'preview' && ns[n] === 'failed');
  if (failed) return { light: 'red', agentName: nodeAgentName(failed) };
  const active = NODE_ORDER.find((n) => n !== 'preview' && (ns[n] === 'running' || ns[n] === 'queued'));
  if (active) return { light: 'yellow', agentName: nodeAgentName(active) };
  const allDone = NODE_ORDER.length > 0 && NODE_ORDER.every((n) => n === 'preview' || ns[n] === 'done');
  if (allDone) return { light: 'green', agentName: '已出片' };
  const next = NODE_ORDER.find((n) => ns[n] !== 'done') ?? NODE_ORDER[0] ?? '';
  return { light: 'green', agentName: nodeAgentName(next) };
}

// agent 卡上浮的进度文本：取首个 running 成员的 progress，没有就空串。
export function agentProgressText(
  agent: AgentDef,
  jobNodes: Record<string, NodeState> | undefined,
): string {
  if (agent.id === 'wudaozi') {
    const rwSelected =
      typeof jobNodes?.rw?.outputs?.selected_model_id === 'string' &&
      jobNodes.rw.outputs.selected_model_id.trim().length > 0;
    const storyboard = jobNodes?.storyboard?.status ?? 'idle';
    const image = jobNodes?.image?.status ?? 'idle';
    if ((jobNodes?.rw?.status ?? 'idle') === 'done' && !rwSelected) return '待柳永定稿';
    if (storyboard === 'done' && image === 'idle') return '待生成画面资产';
  }

  for (const m of [...(agent.preflight ?? []), ...agent.members]) {
    if (m.virtual) continue;
    const ns = jobNodes?.[m.node];
    if (ns && ns.status === 'running') {
      if (agent.id === 'wudaozi' && m.node === 'lines') {
        return wudaoziPreflightProgress(ns.progress);
      }
      if (agent.id === 'wudaozi' && m.node === 'storyboard') {
        return wudaoziStoryboardProgress(ns.progress);
      }
      if (agent.id === 'wudaozi' && m.node === 'image') {
        return friendlyProgressText('image', ns.progress) || '生成画面资产中...';
      }
      return ns.progress || '执行中…';
    }
  }
  return '';
}

function wudaoziPreflightProgress(progress: string | null | undefined): string {
  const msg = (progress ?? '').trim();
  if (!msg) return '正在准备视觉方案...';
  if (/结构化失败|尝试下一个模型|切换备用通道/.test(msg)) return '正在切换备用通道...';
  if (/结构化完成|条 beats|scenes 待分镜|视觉方案准备完成/.test(msg)) return '视觉方案准备完成';
  if (/台词结构|结构化为 beats|beats|Opus|opus|AGY|DeepSeek|SCodex|模型/.test(msg)) {
    return '正在准备视觉方案...';
  }
  return msg;
}

function wudaoziStoryboardProgress(progress: string | null | undefined): string {
  const msg = (progress ?? '').trim();
  if (!msg) return '正在生成视觉方案...';
  if (/切换备用通道/.test(msg)) return '正在切换备用通道...';
  if (/视觉方案生成完成/.test(msg)) return '视觉方案生成完成';
  if (/director agent|分镜|beats|Opus|opus|AGY|DeepSeek|SCodex|模型/.test(msg)) {
    return '正在生成视觉方案...';
  }
  return msg;
}
