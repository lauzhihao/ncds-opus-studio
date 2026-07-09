// 画布页（agent 视角重构）：6 个有序 agent 节点 + agent 抽屉 + SSE 实时状态。
//
// 底层仍是一条 final_preview recipe 的 job（input→asr→rw→lines→storyboard→image→tts→preview→
// render→download）；这里把这些 engine 节点按 agent 分组重新呈现为 6 个有序 agent。
// agent 节点是纯前端分组，操作仍打到底层 engine 节点（见 components/AgentDrawer）。

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
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
import { ArrowLeft } from 'lucide-react';

import { api } from '../api/client';
import { useToast } from '../components/Toast';
import type { PipelineDef } from '../api/types';
import { useJobStream } from '../hooks/useJobStream';
import { AgentCard, type AgentCardData } from '../components/AgentCard';
import { AgentDrawer } from '../components/AgentDrawer';
import { PulseEdge } from '../components/PulseEdge';
import { AppsMenu } from '../components/AppsMenu';
import { ThemeSwitcher } from '../components/ThemeSwitcher';
import { GlobalLoading } from '../components/GlobalLoading';
import {
  AGENTS,
  agentIndex,
  agentProgressText,
  agentStatus,
  guiguziChosenStorageKey,
  type AgentId,
} from '../config/agents';
import {
  STUDIO_MOCK_JOB_ID,
  clearMockJobClientState,
  isStudioMockMode,
  withMockQuery,
} from '../utils/mockMode';
import { parseTitleTags } from '../utils/title';

// 6 个 agent 的 zigzag 两列错开布局。
const ROW = 400;
const COL_OFFSET = 240;
const AGENT_LAYOUT_VERSION = 3;
function computeAgentLayout(): Record<string, { x: number; y: number }> {
  const result: Record<string, { x: number; y: number }> = {};
  AGENTS.forEach((a, i) => {
    result[a.id] = { x: i % 2 === 0 ? -COL_OFFSET : COL_OFFSET, y: i * ROW };
  });
  return result;
}

function agentPosKey(jobId: string): string {
  return `nof:agentpos:v${AGENT_LAYOUT_VERSION}:${jobId}`;
}

export function JobCanvasPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const nav = useNavigate();
  const [searchParams] = useSearchParams();
  const mockMode = isStudioMockMode(searchParams);
  const homePath = withMockQuery('/', mockMode);
  const [pipeline, setPipeline] = useState<PipelineDef | null>(null);
  const [openAgent, setOpenAgent] = useState<AgentId | null>(null);
  // 选题确认的本地脉冲：解耦后选定选题不再触发 rw 的 SSE，靠它让 angleConfirmed 立即重算翻鬼谷子 DONE。
  const [angleTick, setAngleTick] = useState(0);
  const { job, connected, reconnect } = useJobStream(jobId);

  const [nodes, setNodes, onNodesChange] = useNodesState<AgentCardData>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const initialized = useRef(false);
  const rfRef = useRef<ReactFlowInstance | null>(null);
  const nodeTypesRef = useRef({ card: AgentCard });
  const edgeTypesRef = useRef({ pulse: PulseEdge });
  const mockRedirecting = useRef(false);

  useEffect(() => {
    if (!mockMode || !jobId || jobId === STUDIO_MOCK_JOB_ID || mockRedirecting.current) return;
    mockRedirecting.current = true;
    api
      .ensureMock()
      .then((demo) => {
        clearMockJobClientState(demo.job_id);
        nav(withMockQuery(`/jobs/${demo.job_id}`, true), { replace: true });
      })
      .catch((e) => {
        mockRedirecting.current = false;
        console.error('[JobCanvasPage] ensureMock 失败', e);
      });
  }, [mockMode, jobId, nav]);

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
    if (mockMode) return;
    if (!job || !jobId || refreshFired.current) return;
    refreshFired.current = true;
    api.refreshShenkuo(jobId).catch((e) => {
      console.error('[JobCanvasPage] 沈括数据/评论刷新触发失败', e);
    });
  }, [job, jobId, mockMode]);

  // 鬼谷子的"已确认"= 最终选题已选定（持久化于 localStorage，chooseTopic 写入），与柳永(rw) 是否
  // 起步**解耦**：选定即标 DONE，即便柳永随后被取消/重置/失败/根本没起步，鬼谷子仍保持 DONE
  // （选题已是它的产出）。兜底：localStorage 缺失但 rw 已起步过（老任务/换设备）也认作已确认。
  // 响应式来源：angleTick（选题确认脉冲，解耦后选题不触发 SSE）+ job（SSE 脉冲，覆盖刷新/rw 兜底）。
  const angleConfirmed = useMemo(() => {
    let chosen = false;
    try {
      chosen = !!(jobId && localStorage.getItem(guiguziChosenStorageKey(jobId)));
    } catch {
      /* localStorage 不可用时忽略，退回 rw 状态兜底 */
    }
    const rwStarted = ['queued', 'running', 'done'].includes(job?.nodes.rw?.status ?? 'idle');
    return chosen || rwStarted;
  }, [job, jobId, angleTick]);

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

  if (!jobId) {
    return (
      <div className="page" style={{ display: 'grid', placeItems: 'center', minHeight: '60vh', gap: 'var(--s-3)' }}>
        <span className="dim-mono">缺少作品 ID</span>
        <button className="btn ghost sm" onClick={() => nav(homePath)}>
          <ArrowLeft size={14} strokeWidth={1.6} /> 返回任务列表
        </button>
      </div>
    );
  }

  return (
    <div className="canvas-page">
      <div className="topbar">
        <button className="btn ghost sm" onClick={() => nav(homePath)}>
          <ArrowLeft size={14} strokeWidth={1.6} /> 任务列表
        </button>
        <div className="brand">
          <EditableMark jobId={jobId} value={job?.title ?? `作品 ${jobId.slice(0, 6)}`} />
        </div>
        <div className="spacer" />
        <span className={`status-pill ${connected ? 'live' : ''}`}>
          <span className="dot" />
          {connected ? 'LIVE' : 'OFFLINE'}
        </span>
        <AppsMenu />
        <ThemeSwitcher />
      </div>

      <div className="canvas-frame">
        {(!job || nodes.length === 0) && (
          <div className="canvas-loading" role="status" aria-live="polite">
            <GlobalLoading size={34} coreColor="var(--bg-canvas)" />
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
          nodeTypes={nodeTypesRef.current}
          edgeTypes={edgeTypesRef.current}
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
          onTopicConfirmed={() => setAngleTick((t) => t + 1)}
          onReconnectSSE={reconnect}
        />
      )}
    </div>
  );
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
    // 显示态拆「正文 + 话题 chips」；编辑态仍编辑完整原文（含 #），数据层不变。
    // 顶栏右侧空白充足，话题标签全部显示、不折叠。
    const { title, tags } = parseTitleTags(value);
    return (
      <>
        <span
          className="mark editable"
          title="点击修改作品名"
          onClick={() => {
            setDraft(value);
            setEditing(true);
          }}
        >
          {title || value}
        </span>
        {tags.length > 0 && (
          <span className="title-tags">
            {tags.map((t) => (
              <span key={t} className="title-tag">#{t}</span>
            ))}
          </span>
        )}
      </>
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
