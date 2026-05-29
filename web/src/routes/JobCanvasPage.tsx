// 画布页：vertical DAG + 节点抽屉 + SSE 实时状态。
// 状态管理细节：
//   - 节点 position：受 React Flow 自管控（useNodesState + onNodesChange），
//     拖动后不会被后端 job 状态覆盖。
//   - 节点 data.state：每次 SSE 推到新 job 时 patch 进对应节点的 data。
//   - 首次加载时按拓扑层级算垂直布局。

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
import { ArrowLeft, Hash, Loader2 } from 'lucide-react';

import { api } from '../api/client';
import { useToast } from '../components/Toast';
import type { NodeState, PipelineDef, PipelineNodeDef } from '../api/types';
import { useJobStream } from '../hooks/useJobStream';
import { NodeCard, type NodeCardData } from '../components/NodeCard';
import { NodeDrawer } from '../components/NodeDrawer';
import { PulseEdge } from '../components/PulseEdge';
import { ThemeSwitcher } from '../components/ThemeSwitcher';

const NODE_TYPES = { card: NodeCard };
const EDGE_TYPES = { pulse: PulseEdge };

// —— 节点拓扑序 → zigzag 两列错开布局（奇偶左右）
// 节点放大到 1.5×（min 330px / max 390px）后，错位距离和行间距也按比例放大避免重叠。
function computeVerticalLayout(pipeline: PipelineDef): Record<string, { x: number; y: number }> {
  // 收紧行距与列偏移：原 280/280 让 10 节点纵向铺到 2520px、左右各空 1/3，
  // fitView 后文字被压到不可读。降到 200/160 后总高约 1800px，整图入屏后仍可辨认。
  const ROW = 200;
  const COL_OFFSET = 160;
  const result: Record<string, { x: number; y: number }> = {};
  pipeline.nodes.forEach((nd, i) => {
    const left = i % 2 === 0;
    result[nd.name] = {
      x: left ? -COL_OFFSET : COL_OFFSET,
      y: i * ROW,
    };
  });
  return result;
}

export function JobCanvasPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const nav = useNavigate();
  const { showToast } = useToast();
  const [pipeline, setPipeline] = useState<PipelineDef | null>(null);
  const [openNode, setOpenNode] = useState<string | null>(null);
  const { job, connected } = useJobStream(jobId);

  // React Flow 自管节点/边状态；拖动靠 onNodesChange 写回内部 state
  const [nodes, setNodes, onNodesChange] = useNodesState<NodeCardData>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const initializedFor = useRef<string | null>(null);
  const rfRef = useRef<ReactFlowInstance | null>(null);

  useEffect(() => {
    if (!job) return;
    api.getPipeline(job.pipeline_id).then(setPipeline).catch(console.error);
  }, [job?.pipeline_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleRun = useCallback(
    async (nodeName: string) => {
      if (!jobId) return;
      try {
        await api.runNode(jobId, nodeName);
      } catch (e: unknown) {
        showToast(`触发「${nodeName}」失败，请稍后再试`);
        console.error('[JobCanvasPage] runNode 失败', nodeName, e);
      }
    },
    [jobId, showToast],
  );

  // —— 首次（或 pipeline 变化）：构建节点 + 边 + 初始布局
  useEffect(() => {
    if (!pipeline || !job) return;
    if (initializedFor.current === pipeline.id) return;
    initializedFor.current = pipeline.id;

    const layout = computeVerticalLayout(pipeline);
    // 如果老的 node_positions 不能覆盖当前 schema 全部节点（升级过 pipeline），整体丢弃，避免布局错位
    const positions = job.node_positions ?? {};
    const schemaMatch = pipeline.nodes.every((n) => positions[n.name] != null);
    const newNodes: Node<NodeCardData>[] = pipeline.nodes.map((nd, i) => {
      const override = schemaMatch ? positions[nd.name] : undefined;
      const pos = override ?? layout[nd.name] ?? { x: 0, y: i * 160 };
      return {
        id: nd.name,
        type: 'card',
        position: pos,
        data: {
          def: nd,
          state: job.nodes[nd.name] ?? defaultIdleState(nd),
          index: i,
          onOpen: () => setOpenNode(nd.name),
        },
        draggable: true,
      };
    });

    const newEdges: Edge[] = [];
    for (const nd of pipeline.nodes) {
      for (const dep of nd.deps) {
        newEdges.push({
          id: `${dep}__${nd.name}`,
          source: dep,
          target: nd.name,
          // 自定义 pulse edge：底层常态线 + animated 时顶层一段光带从 source 滑向 target
          type: 'pulse',
          animated: isEdgeFlowing(job.nodes[nd.name]?.status),
          style: { stroke: 'url(#opus-gradient)' },
        });
      }
    }
    // edges 必须在 nodes DOM 渲染并被 React Flow 测量过之后再设，
    // 否则首次 mount 时拿不到节点真实宽高 → handle 位置算不出 → 边不画。
    setNodes(newNodes);
    requestAnimationFrame(() => setEdges(newEdges));
  }, [pipeline, job, handleRun, setNodes, setEdges]);

  // —— nodes 填入后用 fitView 把整条 pipeline 收进视口：给用户一个「全流程概览」，
  // 不再硬编码 setCenter(0, 280)（会把视口卡在第 2 个节点、START 落在屏外、且与 ROW 耦合）。
  useEffect(() => {
    if (nodes.length === 0 || !rfRef.current) return;
    const t = setTimeout(() => {
      rfRef.current?.fitView({ padding: 0.18, maxZoom: 0.85, duration: 240 });
    }, 60);
    return () => clearTimeout(t);
  }, [pipeline?.id, nodes.length === 0]);

  // —— job 状态变化（SSE 推 / refresh）：仅 patch 节点的 data.state 与 edge animated，不动 position
  useEffect(() => {
    if (!job || initializedFor.current !== pipeline?.id) return;
    setNodes((cur) =>
      cur.map((n) => {
        const ns = job.nodes[n.id];
        if (!ns) return n;
        return { ...n, data: { ...n.data, state: ns } };
      }),
    );
    setEdges((cur) =>
      cur.map((e) => ({
        ...e,
        animated: isEdgeFlowing(job.nodes[e.target]?.status),
        style: { stroke: 'url(#opus-gradient)' },
      })),
    );
  }, [job, pipeline?.id, setNodes, setEdges]);

  // —— 拖动结束写回后端
  const onNodeDragStop: NodeMouseHandler = useCallback(
    (_e, node) => {
      if (!jobId) return;
      api.updateNodePosition(jobId, node.id, node.position.x, node.position.y).catch(console.error);
    },
    [jobId],
  );

  const stats = useMemo(() => computeStats(job), [job]);

  // 节点抽屉锁定：节点已 done 且其下游节点已被触发(queued/running/done)且未彻底失败 →
  // 锁住该节点抽屉（防误触重跑冲掉下游产物）；下游彻底失败 或 下游还没触发(idle) → 不锁。
  // 含 input(START)：START 永远 done，下游是 asr —— asr 一旦触发(queued/running/done)
  // 即锁住 START，防止同一作品被重复启动；asr 失败或未启动则不锁。output 节点不参与。
  const lockedForOpen = useMemo(() => {
    if (!openNode || !job || !pipeline) return false;
    const nd = pipeline.nodes.find((n) => n.name === openNode);
    if (!nd || nd.kind === 'output') return false;
    if (job.nodes[openNode]?.status !== 'done') return false;
    const succ = pipeline.nodes.find((n) => n.deps.includes(openNode));
    if (!succ) return false;
    const ss = job.nodes[succ.name]?.status;
    return ss === 'queued' || ss === 'running' || ss === 'done';
  }, [openNode, job, pipeline]);

  if (!jobId) {
    return (
      <div className="page" style={{ display: 'grid', placeItems: 'center', minHeight: '60vh', gap: 'var(--s-3)' }}>
        <span className="dim-mono">缺少作品 ID</span>
        <button className="btn ghost sm" onClick={() => nav('/')}>
          <ArrowLeft size={14} strokeWidth={1.6} /> 返回模板中心
        </button>
      </div>
    );
  }

  return (
    <div className="canvas-page">
      <div className="topbar">
        <button className="btn ghost sm" onClick={() => nav('/')}>
          <ArrowLeft size={14} strokeWidth={1.6} /> 模板中心
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
        <span className="dim-mono">
          {stats.done}/{stats.total} done · {stats.running} running
        </span>
        <ThemeSwitcher />
      </div>

      <div className="canvas-frame">
        {(!job || !pipeline || nodes.length === 0) && (
          <div className="canvas-loading" role="status" aria-live="polite">
            <Loader2 size={20} strokeWidth={2} className="spin" />
            <span className="dim-mono">{!job ? '连接中…' : '加载节点图…'}</span>
          </div>
        )}
        {/* SVG defs：连线用 Google 蓝/红/黄 三色纵向渐变 */}
        <svg
          aria-hidden
          style={{ position: 'absolute', width: 0, height: 0, top: 0, left: 0, pointerEvents: 'none' }}
        >
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

      {openNode && job && pipeline && (
        <NodeDrawer
          jobId={jobId}
          nodeDef={pipeline.nodes.find((n) => n.name === openNode)!}
          nodeState={job.nodes[openNode] ?? defaultIdleState(pipeline.nodes.find((n) => n.name === openNode)!)}
          siblingNodes={job.nodes}
          locked={lockedForOpen}
          onClose={() => setOpenNode(null)}
          onRun={() => handleRun(openNode)}
        />
      )}
    </div>
  );
}

// edge "正在流动" 的判定：上游已 done，下游 queued 或 running，意味着数据正在流过这条边。
function isEdgeFlowing(targetStatus: string | undefined): boolean {
  return targetStatus === 'queued' || targetStatus === 'running';
}

function computeStats(job: { nodes: Record<string, NodeState> } | null) {
  if (!job) return { done: 0, running: 0, total: 0 };
  const vals = Object.values(job.nodes);
  return {
    done: vals.filter((n) => n.status === 'done').length,
    running: vals.filter((n) => n.status === 'running').length,
    total: vals.length,
  };
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

function defaultIdleState(nd: PipelineNodeDef): NodeState {
  return {
    name: nd.name,
    status: 'idle',
    started_at: null,
    finished_at: null,
    progress: '',
    outputs: {},
    error: null,
    task_id: null,
  };
}
