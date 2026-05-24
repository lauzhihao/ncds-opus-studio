// 节点详情抽屉。按 node.name 路由到不同的 panel 组件。
// rw 节点是核心微调编辑器（C3）；其余节点先用通用 panel 展示 outputs。

import type { NodeState, PipelineNodeDef } from '../api/types';
import { RwEditorPanel } from './panels/RwEditorPanel';
import { GenericNodePanel } from './panels/GenericNodePanel';

interface Props {
  jobId: string;
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
  onClose: () => void;
  onRun: () => void;
}

export function NodeDrawer({ jobId, nodeDef, nodeState, onClose, onRun }: Props) {
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div className="drawer">
        <div className="drawer-head">
          <h3>{nodeDef.label}</h3>
          <span className="meta" style={{ color: 'var(--ink-soft)', fontSize: 12 }}>
            {nodeDef.name} · {nodeDef.kind}
          </span>
          {(nodeDef.kind === 'command') && (
            <button className="btn sm primary" onClick={onRun}>
              {nodeState.status === 'done' ? '重跑此步' : '运行'}
            </button>
          )}
          <button className="btn sm ghost" onClick={onClose}>关闭</button>
        </div>
        <div className="drawer-body">
          {nodeDef.name === 'rw' ? (
            <RwEditorPanel jobId={jobId} nodeState={nodeState} />
          ) : (
            <GenericNodePanel nodeDef={nodeDef} nodeState={nodeState} />
          )}
        </div>
      </div>
    </>
  );
}
