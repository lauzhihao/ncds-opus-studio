// React Flow 自定义节点：每个 pipeline 节点一张卡片。
// 状态由父级通过 data 传入；按钮事件回调到父级处理。

import { memo } from 'react';
import { Handle, Position } from 'reactflow';
import type { NodeState, PipelineNodeDef } from '../api/types';

export interface NodeCardData {
  def: PipelineNodeDef;
  state: NodeState;
  onRun: () => void;
  onOpen: () => void;
}

interface Props {
  data: NodeCardData;
}

const STATUS_LABEL: Record<NodeState['status'], string> = {
  idle: '待运行',
  queued: '排队中',
  running: '运行中',
  done: '已完成',
  failed: '失败',
};

function fmtDuration(state: NodeState): string {
  if (state.started_at == null) return '';
  const end = state.finished_at ?? Date.now() / 1000;
  const s = Math.max(0, end - state.started_at);
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m${Math.round(s % 60)}s`;
}

function NodeCardImpl({ data }: Props) {
  const { def, state, onRun, onOpen } = data;
  const isInput = def.kind === 'input';
  const isOutput = def.kind === 'output';
  const canRun = !isInput && !isOutput && state.status !== 'running' && state.status !== 'queued';
  const showDuration = state.status === 'running' || state.status === 'done';

  return (
    <div className={`node-card kind-${def.kind} status-${state.status}`}>
      {/* Handles：input 节点无 target，output 节点无 source */}
      {!isInput && <Handle type="target" position={Position.Left} />}
      {!isOutput && <Handle type="source" position={Position.Right} />}

      <div className="head">
        <span className="dot" />
        <span className="label">{def.label}</span>
        <span style={{ fontSize: 11, color: 'var(--ink-soft)' }}>{STATUS_LABEL[state.status]}</span>
      </div>

      <div className="body">
        {state.status === 'running' && state.progress && (
          <div className="progress">⏳ {state.progress}</div>
        )}
        {state.status === 'failed' && state.error && (
          <div style={{ color: 'var(--status-failed)' }}>{state.error}</div>
        )}
        {state.status === 'done' && Object.keys(state.outputs).length > 0 && (
          <div>
            <div style={{ fontSize: 11 }}>产物：</div>
            <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 11 }}>
              {Object.entries(state.outputs).slice(0, 3).map(([k, v]) => (
                <li key={k} title={String(v)} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {k}: <span style={{ color: 'var(--ink)' }}>{String(v).slice(0, 28)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {state.status === 'idle' && (
          <div style={{ fontSize: 11 }}>{def.description}</div>
        )}
        {showDuration && (
          <div style={{ fontSize: 11, color: 'var(--ink-soft)', marginTop: 4 }}>
            耗时 {fmtDuration(state)}
          </div>
        )}
      </div>

      <div className="footer">
        <button className="btn sm" onClick={onOpen}>详情</button>
        {canRun && (
          <button className="btn sm primary" onClick={onRun}>
            {state.status === 'done' ? '重跑' : '运行'}
          </button>
        )}
      </div>
    </div>
  );
}

export const NodeCard = memo(NodeCardImpl);
