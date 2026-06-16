// 画布页（agent 视角重构）：6 个有序 agent 节点 + 卧龙总览条 + agent 抽屉 + SSE 实时状态。
//
// 底层仍是一条 015 recipe 的 job（input→asr→rw→lines→storyboard→tts→image→preview→
// render→download）；这里把这些 engine 节点按 agent 分组重新呈现为 6 个有序 agent。
// agent 节点是纯前端分组，操作仍打到底层 engine 节点（见 components/AgentDrawer）。

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeMouseHandler,
  type ReactFlowInstance,
} from 'reactflow';
import { ArrowLeft, Crown, Hash, Loader2 } from 'lucide-react';

import { api } from '../api/client';
import { useToast } from '../components/Toast';
import type { JobState, NodeStatus, PipelineDef } from '../api/types';
import { useJobStream } from '../hooks/useJobStream';
import { AgentCard, type AgentCardData } from '../components/AgentCard';
import { AgentDrawer } from '../components/AgentDrawer';
import { PulseEdge } from '../components/PulseEdge';
import { ThemeSwitcher } from '../components/ThemeSwitcher';
import { AGENTS, agentIndex, agentProgressText, agentStatus, type AgentId } from '../config/agents';

const NODE_TYPES = { card: AgentCard };
const EDGE_TYPES = { pulse: PulseEdge };

// 6 个 agent 的 zigzag 两列错开布局（沿用旧画布的 ROW/COL）。
const ROW = 200;
const COL_OFFSET = 160;
function computeAgentLayout(): Record<string, { x: number; y: number }> {
  const result: Record<string, { x: number; y: number }> = {};
  AGENTS.forEach((a, i) => {
    result[a.id] = { x: i % 2 === 0 ? -COL_OFFSET : COL_OFFSET, y: i * ROW };
  });
  return result;
}

function agentPosKey(jobId: string): string {
  return `nof:agentpos:${jobId}`;
}

export function JobCanvasPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const nav = useNavigate();
  const [pipeline, setPipeline] = useState<PipelineDef | null>(null);
  const [openAgent, setOpenAgent] = useState<AgentId | null>(null);
  const { job, connected } = useJobStream(jobId);

  const [nodes, setNodes, onNodesChange] = useNodesState<AgentCardData>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const initialized = useRef(false);
  const rfRef = useRef<ReactFlowInstance | null>(null);

  useEffect(() => {
    if (!job) return;
    api.getPipeline(job.pipeline_id).then(setPipeline).catch(console.error);
  }, [job?.pipeline_id]); // eslint-disable-line react-hooks/exhaustive-deps

  // 进画布即自动采集：无论从哪个入口（账号作品 / 临时任务 / 历史作品）进来，只要采集源已就位
  // 且沈括(asr) 还没起步，就自动触发一次 collect。后端有采集缓存，已采过的作品直接返回结果。
  const autoCollectFired = useRef(false);
  useEffect(() => {
    if (!job || !jobId || autoCollectFired.current) return;
    const asr = job.nodes.asr?.status ?? 'idle';
    // 仅在 idle / failed 时自动采集；queued/running/done 不重复触发。
    if (asr !== 'idle' && asr !== 'failed') return;
    const input = (job.nodes.input?.outputs ?? {}) as { urls?: unknown; shares?: unknown };
    const hasSource =
      (Array.isArray(input.shares) && input.shares.length > 0) ||
      (Array.isArray(input.urls) && input.urls.length > 0);
    if (!hasSource) return; // 空作品（无采集源）不触发
    autoCollectFired.current = true;
    api.runNode(jobId, 'asr').catch((e) => {
      autoCollectFired.current = false; // 失败放行，下次 job 更新时可重试
      console.error('[JobCanvasPage] 自动采集触发失败', e);
    });
  }, [job, jobId]);

  // 进画布即后台刷新沈括的播放数据 + 评论（与抽屉是否打开无关）。fire-and-forget：后端逐条
  // 作品 1 小时内只采一次（Redis 节流锁）省 API 成本，没采过 / 正在采则后端自动 no-op。
  const refreshFired = useRef(false);
  useEffect(() => {
    if (!job || !jobId || refreshFired.current) return;
    refreshFired.current = true;
    api.refreshShenkuo(jobId).catch((e) => {
      console.error('[JobCanvasPage] 沈括数据/评论刷新触发失败', e);
    });
  }, [job, jobId]);

  // 鬼谷子选题"已确认"= 柳永(rw) 已起步（确认选题即触发 rw）。可靠且响应式，无需 localStorage。
  const angleConfirmed = useMemo(
    () => ['queued', 'running', 'done'].includes(job?.nodes.rw?.status ?? 'idle'),
    [job?.nodes.rw?.status],
  );

  // —— 首次：构建 6 个 agent 节点 + 串行连线 + 初始布局
  useEffect(() => {
    if (!job || initialized.current) return;
    initialized.current = true;

    const layout = computeAgentLayout();
    let saved: Record<string, { x: number; y: number }> = {};
    try {
      saved = JSON.parse(localStorage.getItem(agentPosKey(jobId!)) || '{}');
    } catch { /* ignore */ }

    const newNodes: Node<AgentCardData>[] = AGENTS.map((a, i) => ({
      id: a.id,
      type: 'card',
      position: saved[a.id] ?? layout[a.id] ?? { x: 0, y: i * ROW },
      data: {
        agent: a,
        status: agentStatus(a, job.nodes, { angleConfirmed }),
        progress: agentProgressText(a, job.nodes),
        index: i,
        isFirst: i === 0,
        isLast: i === AGENTS.length - 1,
        onOpen: () => setOpenAgent(a.id),
      },
      draggable: true,
    }));

    const newEdges: Edge[] = [];
    for (let i = 1; i < AGENTS.length; i++) {
      newEdges.push({
        id: `${AGENTS[i - 1].id}__${AGENTS[i].id}`,
        source: AGENTS[i - 1].id,
        target: AGENTS[i].id,
        type: 'pulse',
        animated: false,
        style: { stroke: 'url(#opus-gradient)' },
      });
    }
    setNodes(newNodes);
    requestAnimationFrame(() => setEdges(newEdges));
  }, [job, jobId, angleConfirmed, setNodes, setEdges]);

  // —— fitView 收进视口
  useEffect(() => {
    if (nodes.length === 0 || !rfRef.current) return;
    const t = setTimeout(() => {
      rfRef.current?.fitView({ padding: 0.18, maxZoom: 0.85, duration: 240 });
    }, 60);
    return () => clearTimeout(t);
  }, [nodes.length === 0]);

  // —— job 状态变化：patch agent 聚合状态 + edge animated
  useEffect(() => {
    if (!job || !initialized.current) return;
    setNodes((cur) =>
      cur.map((n) => {
        const a = AGENTS.find((x) => x.id === n.id);
        if (!a) return n;
        return {
          ...n,
          data: {
            ...n.data,
            status: agentStatus(a, job.nodes, { angleConfirmed }),
            progress: agentProgressText(a, job.nodes),
          },
        };
      }),
    );
    setEdges((cur) =>
      cur.map((e) => {
        const a = AGENTS.find((x) => x.id === e.target);
        const st = a ? agentStatus(a, job.nodes, { angleConfirmed }) : 'idle';
        return { ...e, animated: st === 'running' || st === 'queued', style: { stroke: 'url(#opus-gradient)' } };
      }),
    );
  }, [job, angleConfirmed, setNodes, setEdges]);

  // —— 拖动写 localStorage（agent id 不是 engine 节点，不走后端 position 接口）
  const onNodeDragStop: NodeMouseHandler = useCallback(
    (_e, node) => {
      if (!jobId) return;
      try {
        const cur = JSON.parse(localStorage.getItem(agentPosKey(jobId)) || '{}');
        cur[node.id] = { x: node.position.x, y: node.position.y };
        localStorage.setItem(agentPosKey(jobId), JSON.stringify(cur));
      } catch { /* ignore */ }
    },
    [jobId],
  );

  const overview = useMemo(() => computeOverview(job, angleConfirmed), [job, angleConfirmed]);

  if (!jobId) {
    return (
      <div className="page" style={{ display: 'grid', placeItems: 'center', minHeight: '60vh', gap: 'var(--s-3)' }}>
        <span className="dim-mono">缺少作品 ID</span>
        <button className="btn ghost sm" onClick={() => nav('/')}>
          <ArrowLeft size={14} strokeWidth={1.6} /> 返回任务列表
        </button>
      </div>
    );
  }

  return (
    <div className="canvas-page">
      <div className="topbar">
        <button className="btn ghost sm" onClick={() => nav('/')}>
          <ArrowLeft size={14} strokeWidth={1.6} /> 任务列表
        </button>
        <div className="brand">
          <EditableMark jobId={jobId} value={job?.title ?? `作品 ${jobId.slice(0, 6)}`} />
          <span className="sub mono">
            {pipeline?.id}
            <span style={{ margin: '0 6px', opacity: 0.5 }}>·</span>
            <Hash size={10} strokeWidth={1.6} style={{ verticalAlign: '-1px', marginRight: 2 }} />
            {jobId.slice(0, 8)}
          </span>
        </div>
        <div className="spacer" />
        <span className={`status-pill ${connected ? 'live' : ''}`}>
          <span className="dot" />
          {connected ? 'LIVE' : 'OFFLINE'}
        </span>
        <ThemeSwitcher />
      </div>

      {/* 卧龙总览条：统领全局（聚合进度 + 当前阶段 + 进入按钮） */}
      <div className={`wolong-bar status-${overview.status}`}>
        <span className="wolong-id">
          <Crown size={15} strokeWidth={1.7} /> 卧龙
          <span className="dim-mono">统领全局</span>
        </span>
        <div className="wolong-track">
          {AGENTS.map((a) => {
            const st = job ? agentStatus(a, job.nodes, { angleConfirmed }) : 'idle';
            return <span key={a.id} className={`wolong-pip status-${st}`} title={`${a.name} · ${a.role}`} />;
          })}
        </div>
        <span className="wolong-stat dim-mono">
          {overview.done}/{AGENTS.length} 阶段 · {overview.label}
        </span>
        {overview.current && (
          <button className="btn primary sm" onClick={() => setOpenAgent(overview.current!)}>
            进入 {AGENTS[agentIndex(overview.current)].name}
          </button>
        )}
      </div>

      <div className="canvas-frame">
        {(!job || nodes.length === 0) && (
          <div className="canvas-loading" role="status" aria-live="polite">
            <Loader2 size={20} strokeWidth={2} className="spin" />
            <span className="dim-mono">{!job ? '连接中…' : '加载 agent 图…'}</span>
          </div>
        )}
        <svg aria-hidden style={{ position: 'absolute', width: 0, height: 0, top: 0, left: 0, pointerEvents: 'none' }}>
          <defs>
            <linearGradient id="opus-gradient" x1="0%" y1="0%" x2="0%" y2="100%">
              <stop offset="0%" stopColor="#4285F4" />
              <stop offset="50%" stopColor="#EA4335" />
              <stop offset="100%" stopColor="#FBBC04" />
            </linearGradient>
          </defs>
        </svg>

        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeDragStop={onNodeDragStop}
          onInit={(rf) => { rfRef.current = rf; }}
          nodeTypes={NODE_TYPES}
          edgeTypes={EDGE_TYPES}
          defaultViewport={{ x: 0, y: 0, zoom: 1 }}
          minZoom={0.3}
          maxZoom={1.5}
          nodesConnectable={false}
          edgesFocusable={false}
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="var(--grid-dot)" />
          <MiniMap pannable zoomable nodeColor="var(--ink-3)" maskColor="var(--bg-overlay)" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      {openAgent && job && pipeline && (
        <AgentDrawer
          key={openAgent}
          jobId={jobId}
          agent={AGENTS[agentIndex(openAgent)]}
          job={job}
          pipeline={pipeline}
          angleConfirmed={angleConfirmed}
          onClose={() => setOpenAgent(null)}
          onAdvanceAgent={(next) => setOpenAgent(next)}
        />
      )}
    </div>
  );
}

// 卧龙总览聚合：完成阶段数 / 整体状态 / 当前应进入的 agent。
function computeOverview(job: JobState | null, angleConfirmed: boolean): {
  done: number;
  status: NodeStatus;
  label: string;
  current: AgentId | null;
} {
  if (!job) return { done: 0, status: 'idle', label: '连接中', current: null };
  const sts = AGENTS.map((a) => agentStatus(a, job.nodes, { angleConfirmed }));
  const done = sts.filter((s) => s === 'done').length;
  let status: NodeStatus = 'idle';
  if (sts.some((s) => s === 'failed')) status = 'failed';
  else if (sts.some((s) => s === 'running' || s === 'queued')) status = 'running';
  else if (done === AGENTS.length) status = 'done';
  const label =
    status === 'failed' ? '有阶段失败' : status === 'done' ? '已完成' : status === 'running' ? '进行中' : '待开始';
  // 当前阶段：首个未完成的 agent（失败的也算"需处理"）。
  const idx = sts.findIndex((s) => s !== 'done');
  const current = idx >= 0 ? AGENTS[idx].id : null;
  return { done, status, label, current };
}

function EditableMark({ jobId, value }: { jobId: string; value: string }) {
  const { showToast } = useToast();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  async function commit() {
    const next = draft.trim();
    setEditing(false);
    if (!next || next === value) return;
    try {
      await api.updateJobTitle(jobId, next);
    } catch (e) {
      showToast('改名失败，请稍后再试');
      console.error('[JobCanvasPage] updateJobTitle 失败', e);
    }
  }

  if (!editing) {
    return (
      <span
        className="mark editable"
        title="点击修改作品名"
        onClick={() => {
          setDraft(value);
          setEditing(true);
        }}
      >
        {value}
      </span>
    );
  }
  return (
    <input
      ref={inputRef}
      className="mark editable-input"
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
        else if (e.key === 'Escape') setEditing(false);
      }}
    />
  );
}
