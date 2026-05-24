// 画布页骨架。React Flow 渲染节点 + 只读连线。
// C2 阶段补：节点卡片真实状态 + SSE 订阅。
// C3 阶段补：点击节点弹出展开面板（核心是 rw 编辑器）。

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from 'reactflow';

import { api } from '../api/client';
import type { NodeState, PipelineDef, PipelineNodeDef } from '../api/types';
import { useJobStream } from '../hooks/useJobStream';
import { NodeCard, type NodeCardData } from '../components/NodeCard';
import { NodeDrawer } from '../components/NodeDrawer';

const NODE_TYPES = { card: NodeCard };

export function JobCanvasPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const nav = useNavigate();
  const [pipeline, setPipeline] = useState<PipelineDef | null>(null);
  const [openNode, setOpenNode] = useState<string | null>(null);
  const { job, connected } = useJobStream(jobId);

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
        alert(`触发 ${nodeName} 失败：${(e as Error).message}`);
      }
    },
    [jobId],
  );

  const { nodes, edges } = useMemo<{ nodes: Node<NodeCardData>[]; edges: Edge[] }>(() => {
    if (!pipeline || !job) return { nodes: [], edges: [] };
    const rfNodes: Node<NodeCardData>[] = pipeline.nodes.map((nd) => {
      const ns: NodeState | undefined = job.nodes[nd.name];
      const overridePos = job.node_positions?.[nd.name];
      const pos = overridePos ?? nd.position;
      return {
        id: nd.name,
        type: 'card',
        position: { x: pos.x, y: pos.y },
        data: {
          def: nd,
          state: ns ?? defaultIdleState(nd),
          onRun: () => handleRun(nd.name),
          onOpen: () => setOpenNode(nd.name),
        },
        draggable: true,
        // input 卡和 output 卡也支持 drag 缩放，仅步骤本身不能改
      };
    });
    const rfEdges: Edge[] = [];
    for (const nd of pipeline.nodes) {
      for (const dep of nd.deps) {
        const sourceState = job.nodes[dep];
        const animated = sourceState?.status === 'running';
        rfEdges.push({
          id: `${dep}__${nd.name}`,
          source: dep,
          target: nd.name,
          animated,
          // edges 不可由用户改连接，react-flow 默认就只读
          type: 'smoothstep',
        });
      }
    }
    return { nodes: rfNodes, edges: rfEdges };
  }, [pipeline, job, handleRun]);

  const onNodeDragStop: NodeMouseHandler = useCallback(
    (_e, node) => {
      if (!jobId) return;
      api.updateNodePosition(jobId, node.id, node.position.x, node.position.y).catch(console.error);
    },
    [jobId],
  );

  if (!jobId) return <div style={{ padding: 20 }}>missing jobId</div>;

  return (
    <div className="canvas-page">
      <div className="topbar">
        <button className="btn ghost" onClick={() => nav('/')}>
          ← 模板列表
        </button>
        <span className="title">{job?.title ?? `作品 ${jobId.slice(0, 6)}`}</span>
        <span className="meta">{connected ? '🟢 SSE 已连接' : '⚪ 未连接'}</span>
        <div className="spacer" />
        <span className="meta">{pipeline?.name}</span>
      </div>

      <div className="canvas-frame">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          onNodeDragStop={onNodeDragStop}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          minZoom={0.3}
          maxZoom={1.5}
          nodesConnectable={false}
          edgesFocusable={false}
        >
          <Background gap={16} />
          <MiniMap pannable zoomable />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      {openNode && job && pipeline && (
        <NodeDrawer
          jobId={jobId}
          nodeDef={pipeline.nodes.find((n) => n.name === openNode)!}
          nodeState={job.nodes[openNode] ?? defaultIdleState(pipeline.nodes.find((n) => n.name === openNode)!)}
          onClose={() => setOpenNode(null)}
          onRun={() => handleRun(openNode)}
        />
      )}
    </div>
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
