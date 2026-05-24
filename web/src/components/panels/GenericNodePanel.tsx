// 通用节点详情面板：展示节点描述 + 当前状态 + outputs 字段。
// 用于尚未单独实现编辑器的节点（asr/wst/tts/render/input/output）。

import type { NodeState, PipelineNodeDef } from '../../api/types';

interface Props {
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
}

export function GenericNodePanel({ nodeDef, nodeState }: Props) {
  return (
    <div>
      <div className="section-h">描述</div>
      <p style={{ margin: '6px 0 12px', color: 'var(--ink-soft)' }}>{nodeDef.description}</p>

      <div className="section-h">状态</div>
      <table style={{ width: '100%', fontSize: 13 }}>
        <tbody>
          <tr><td style={{ width: 100, color: 'var(--ink-soft)' }}>状态</td><td>{nodeState.status}</td></tr>
          <tr><td style={{ color: 'var(--ink-soft)' }}>进度</td><td>{nodeState.progress || '—'}</td></tr>
          {nodeState.started_at && (
            <tr><td style={{ color: 'var(--ink-soft)' }}>开始</td>
              <td>{new Date(nodeState.started_at * 1000).toLocaleString('zh-CN', { hour12: false })}</td></tr>
          )}
          {nodeState.finished_at && (
            <tr><td style={{ color: 'var(--ink-soft)' }}>完成</td>
              <td>{new Date(nodeState.finished_at * 1000).toLocaleString('zh-CN', { hour12: false })}</td></tr>
          )}
          {nodeState.error && (
            <tr><td style={{ color: 'var(--status-failed)' }}>错误</td><td>{nodeState.error}</td></tr>
          )}
        </tbody>
      </table>

      <div className="section-h">产物</div>
      {Object.keys(nodeState.outputs).length === 0 ? (
        <div style={{ color: 'var(--ink-soft)', fontSize: 13 }}>暂无产物</div>
      ) : (
        <pre style={{
          fontSize: 12,
          background: '#faf8f3',
          padding: 10,
          border: '1px solid var(--border)',
          borderRadius: 6,
          overflow: 'auto',
          maxHeight: 360,
        }}>
{JSON.stringify(nodeState.outputs, null, 2)}
        </pre>
      )}
    </div>
  );
}
