// 画布的 agent 表现层配置（止损重构核心）。
//
// 画布底层仍是一条 015 recipe 的 job（input→asr→rw→lines→storyboard→tts→image→
// preview→render→download），这里把这些 engine 节点**按 agent 分组重新呈现**为 6 个有序
// agent + 卧龙总览。agent 节点是纯前端分组：run/cancel/select 仍打到底层 engine 节点
// （/jobs/{id}/nodes/{node}/run），即"agent 调现成 performer"，后端零改动。
//
// 鬼谷子是唯一没有 engine 步的 agent（virtual member），v1 是薄选题 gate：
// 沈括(asr) 完成 → 鬼谷子录入/确认选题角度 → 让柳永(rw) 出稿。

import {
  Clapperboard,
  Crown,
  Lightbulb,
  Music,
  Palette,
  PenLine,
  Radar,
  type LucideIcon,
} from 'lucide-react';

import type { NodeState, NodeStatus } from '../api/types';

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
  members: AgentMember[];
}

// 6 个有序 agent。顺序即产线：沈括→鬼谷子→柳永→吴道子→伯牙→渲染出片。
// 卧龙不在产线顺序里，作任务级总览（见 WOLONG）。
export const AGENTS: AgentDef[] = [
  {
    id: 'shenkuo',
    name: '沈括',
    role: '采集 · 转写入库',
    description: '抓取源作品并转写成文，作为创作素材。',
    icon: Radar,
    members: [
      { node: 'input', label: '采集源' },
      { node: 'asr', label: '转写入库' },
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
    role: '出稿',
    description: '多模型改写出稿，并切分为台词稿。',
    icon: PenLine,
    members: [
      { node: 'rw', label: '改写' },
      { node: 'lines', label: '台词' },
    ],
  },
  {
    id: 'wudaozi',
    name: '吴道子',
    role: '美术',
    description: '分镜设计与画面生成。',
    icon: Palette,
    members: [
      { node: 'storyboard', label: '分镜' },
      { node: 'image', label: '画面' },
    ],
  },
  {
    id: 'boya',
    name: '伯牙',
    role: '声音',
    description: '台词配音合成。',
    icon: Music,
    members: [{ node: 'tts', label: '配音' }],
  },
  {
    id: 'render',
    name: '渲染出片',
    role: '合成出片',
    description: '审片、渲染并导出成片。',
    icon: Clapperboard,
    members: [
      { node: 'preview', label: '审片' },
      { node: 'render', label: '渲染' },
      { node: 'download', label: '下载' },
    ],
  },
];

// 卧龙：任务级总览（顶部条），统领全局，不占产线顺序位。
export const WOLONG = {
  name: '卧龙',
  role: '统领全局',
  icon: Crown,
} as const;

// —— 节点 → agent 反查 / 推进链 ——
// 画布按用户顺序（沈括→鬼谷子→柳永→吴道子→伯牙→渲染）展示，但**推进由引擎真实
// 数据依赖驱动**：015 链是 storyboard→tts→image，所以 storyboard 完成→去伯牙(tts)、
// tts 完成→回吴道子(image)。这是引擎 deps 的忠实反映（image deps=[tts]），不改后端。

// 底层 job 节点 → 所属 agentId（含 virtual 的 guiguzi）。
export const AGENT_BY_NODE: Record<string, AgentId> = (() => {
  const m: Record<string, AgentId> = {};
  for (const a of AGENTS) for (const mem of a.members) m[mem.node] = a.id;
  return m;
})();

// 引擎真实 NEXT 链（鬼谷子 gate 插在 asr 与 rw 之间）。末步 download 无 next。
export const NODE_NEXT: Record<string, string | null> = {
  input: 'asr',
  asr: 'guiguzi',
  guiguzi: 'rw',
  rw: 'lines',
  lines: 'storyboard',
  storyboard: 'tts',
  tts: 'image',
  image: 'preview',
  preview: 'render',
  render: 'download',
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
  return agent.members.map((m) => {
    if (m.virtual) return ctx.angleConfirmed ? 'done' : 'idle';
    return jobNodes?.[m.node]?.status ?? 'idle';
  });
}

// agent 整体状态（聚合 + virtual 处理）。
export function agentStatus(
  agent: AgentDef,
  jobNodes: Record<string, NodeState> | undefined,
  ctx: { angleConfirmed?: boolean } = {},
): NodeStatus {
  return aggregateAgentStatus(agentMemberStatuses(agent, jobNodes, ctx));
}

// agent 卡上浮的进度文本：取首个 running 成员的 progress，没有就空串。
export function agentProgressText(
  agent: AgentDef,
  jobNodes: Record<string, NodeState> | undefined,
): string {
  for (const m of agent.members) {
    if (m.virtual) continue;
    const ns = jobNodes?.[m.node];
    if (ns && ns.status === 'running') return ns.progress || '执行中…';
  }
  return '';
}
