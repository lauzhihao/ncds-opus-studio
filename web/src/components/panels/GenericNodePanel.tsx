// 通用节点详情：节点描述 + 状态 table + outputs JSON。

import { CheckCircle2, CircleAlert, CircleDashed, Clock, FileJson, Loader2 } from 'lucide-react';
import type { NodeState, PipelineNodeDef } from '../../api/types';

interface Props {
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
}

const STATUS_ICON: Record<NodeState['status'], typeof CircleDashed> = {
  idle: CircleDashed,
  queued: Clock,
  running: Loader2,
  done: CheckCircle2,
  failed: CircleAlert,
};

const STATUS_COLOR: Record<NodeState['status'], string> = {
  idle: 'var(--status-idle)',
  queued: 'var(--status-queued)',
  running: 'var(--status-running)',
  done: 'var(--status-done)',
  failed: 'var(--status-failed)',
};

export function GenericNodePanel({ nodeDef, nodeState }: Props) {
  const StatusIcon = STATUS_ICON[nodeState.status];
  const statusColor = STATUS_COLOR[nodeState.status];
  return (
    <div>
      <div style={{ color: 'var(--ink-2)', fontSize: 'var(--text-md)', lineHeight: 1.65 }}>
        {nodeDef.description}
      </div>

      <div className="section-h">状态</div>
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'auto 1fr',
        gap: '8px 14px',
        fontSize: 'var(--text-sm)',
        color: 'var(--ink-2)',
      }}>
        <div className="dim-mono">status</div>
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: statusColor, fontWeight: 500 }}>
          <StatusIcon size={14} strokeWidth={1.8} className={nodeState.status === 'running' ? 'spin' : ''} />
          {nodeState.status}
        </div>
        {nodeState.progress && (
          <>
            <div className="dim-mono">progress</div>
            <div>{nodeState.progress}</div>
          </>
        )}
        {nodeState.started_at != null && (
          <>
            <div className="dim-mono">started</div>
            <div className="mono" style={{ fontSize: 'var(--text-xs)' }}>
              {new Date(nodeState.started_at * 1000).toLocaleString('zh-CN', { hour12: false })}
            </div>
          </>
        )}
        {nodeState.finished_at != null && (
          <>
            <div className="dim-mono">finished</div>
            <div className="mono" style={{ fontSize: 'var(--text-xs)' }}>
              {new Date(nodeState.finished_at * 1000).toLocaleString('zh-CN', { hour12: false })}
            </div>
          </>
        )}
        {nodeState.error && (
          <>
            <div className="dim-mono">error</div>
            <div style={{ color: 'var(--status-failed)' }}>{nodeState.error}</div>
          </>
        )}
      </div>

      <div className="section-h">
        <FileJson size={12} strokeWidth={1.6} /> 产物
      </div>
      {Object.keys(nodeState.outputs).length === 0 ? (
        <div className="dim" style={{ fontSize: 'var(--text-sm)' }}>暂无产物</div>
      ) : (
        <pre className="code-block">
{JSON.stringify(nodeState.outputs, null, 2)}
        </pre>
      )}

      <style>{`
        .spin { animation: spin-loader 0.9s linear infinite; }
        @keyframes spin-loader { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}
