// 节点详情抽屉。按 node.name 路由到不同的 panel。
// rw 节点用 RwEditorPanel；其余用 GenericNodePanel。

import { useEffect } from 'react';
import {
  BadgeCheck,
  Cog,
  Download,
  Eye,
  Globe,
  Image as ImageIcon,
  Mic,
  PenLine,
  Play,
  RefreshCw,
  Volume2,
  X,
} from 'lucide-react';

import type { NodeState, PipelineNodeDef } from '../api/types';
import { AsrResultPanel } from './panels/AsrResultPanel';
import { EpisodeEditorPanel } from './panels/EpisodeEditorPanel';
import { GenericNodePanel } from './panels/GenericNodePanel';
import { InputPanel } from './panels/InputPanel';
import { RwResultPanel } from './panels/RwResultPanel';

interface Props {
  jobId: string;
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
  onClose: () => void;
  onRun: () => void;
}

function getIcon(name: string): typeof Cog {
  switch (name) {
    case 'input': return Globe;
    case 'asr': return Mic;
    case 'rw': return PenLine;
    case 'image': return ImageIcon;
    case 'tts': return Volume2;
    case 'preview': return Eye;
    case 'render': return Cog;
    case 'download': return Download;
    case 'output': return Download;
    default: return Cog;
  }
}

export function NodeDrawer({ jobId, nodeDef, nodeState, onClose, onRun }: Props) {
  const Icon = getIcon(nodeDef.name);

  // Esc 关闭
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal>
        <div className="head">
          <div className="icon-frame">
            <Icon size={18} strokeWidth={1.6} />
          </div>
          <div className="titles">
            <h3 className="title">{nodeDef.label}</h3>
            <div className="subtitle">
              {nodeDef.name} · {nodeDef.name === 'input' ? 'share links' : nodeDef.kind}
            </div>
          </div>
          {nodeDef.kind === 'command' && nodeDef.name !== 'input' && nodeDef.name !== 'asr' && nodeDef.name !== 'rw' && (
            <button className="btn primary sm" onClick={onRun}>
              {nodeDef.name === 'preview' ? (
                nodeState.status === 'done' ? (
                  <>
                    <BadgeCheck size={12} strokeWidth={1.8} /> 已通过 · 重审
                  </>
                ) : (
                  <>
                    <BadgeCheck size={12} strokeWidth={1.8} /> 通过审核
                  </>
                )
              ) : nodeState.status === 'done' ? (
                <>
                  <RefreshCw size={12} strokeWidth={1.8} /> 重跑
                </>
              ) : (
                <>
                  <Play size={12} strokeWidth={1.8} /> 运行
                </>
              )}
            </button>
          )}
          <button className="btn sm icon-only ghost" onClick={onClose} title="关闭 (Esc)">
            <X size={14} strokeWidth={1.6} />
          </button>
        </div>
        <div className="body">
          {nodeDef.name === 'input' ? (
            <InputPanel
              jobId={jobId}
              nodeDef={nodeDef}
              nodeState={nodeState}
              onStarted={onClose}
            />
          ) : nodeDef.name === 'preview' ? (
            <EpisodeEditorPanel jobId={jobId} nodeState={nodeState} />
          ) : nodeDef.name === 'asr' ? (
            <AsrResultPanel jobId={jobId} nodeDef={nodeDef} nodeState={nodeState} onAdvanced={onClose} />
          ) : nodeDef.name === 'rw' ? (
            <RwResultPanel jobId={jobId} nodeDef={nodeDef} nodeState={nodeState} onAdvanced={onClose} />
          ) : (
            <GenericNodePanel nodeDef={nodeDef} nodeState={nodeState} />
          )}
        </div>
      </aside>
    </>
  );
}
