// 节点详情抽屉。按 node.name 路由到不同的 panel。
// rw / lines / asr 等有专属 panel，其余走 GenericNodePanel。

import { useEffect } from 'react';
import {
  BadgeCheck,
  Captions,
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
import { GenericNodePanel } from './panels/GenericNodePanel';
import { ImageResultPanel } from './panels/ImageResultPanel';
import { InputPanel } from './panels/InputPanel';
import { LinesPanel } from './panels/LinesPanel';
import { PreviewIframePanel } from './panels/PreviewIframePanel';
import { RwResultPanel } from './panels/RwResultPanel';
import { TtsResultPanel } from './panels/TtsResultPanel';

interface Props {
  jobId: string;
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
  // 兄弟节点的当前状态（用来在 InputPanel 等子面板里做 cross-node 判断，
  // 例如 ASR 已 done 时 INPUT 面板要锁编辑）。
  siblingNodes?: Record<string, NodeState>;
  onClose: () => void;
  onRun: () => void;
}

function getIcon(name: string): typeof Cog {
  switch (name) {
    case 'input': return Globe;
    case 'asr': return Mic;
    case 'rw': return PenLine;
    case 'lines': return Captions;
    case 'image': return ImageIcon;
    case 'tts': return Volume2;
    case 'preview': return Eye;
    case 'render': return Cog;
    case 'download': return Download;
    case 'output': return Download;
    default: return Cog;
  }
}

export function NodeDrawer({ jobId, nodeDef, nodeState, siblingNodes, onClose, onRun }: Props) {
  const Icon = getIcon(nodeDef.name);

  // Esc 关闭
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  // preview 节点需要全屏（iframe 大画布 + 拖拽 overlay）；其余节点保持侧抽屉
  const isFullscreen = nodeDef.name === 'preview';

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className={`drawer${isFullscreen ? ' fullscreen' : ''}`} role="dialog" aria-modal>
        <div className={`head${nodeState.status === 'running' || nodeState.status === 'queued' ? ' loading' : ''}`}>
          <div className="icon-frame">
            <Icon size={18} strokeWidth={1.6} />
          </div>
          <div className="titles">
            <h3 className="title">{nodeDef.label}</h3>
            <div className="subtitle">
              {nodeDef.name} · {nodeDef.name === 'input' ? 'share links' : nodeDef.kind}
            </div>
          </div>
          {nodeDef.kind === 'command' && nodeDef.name !== 'input' && nodeDef.name !== 'asr' && nodeDef.name !== 'rw' && nodeDef.name !== 'lines' && nodeDef.name !== 'image' && nodeDef.name !== 'tts' && (
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
              asrStatus={siblingNodes?.asr?.status}
              onStarted={onClose}
            />
          ) : nodeDef.name === 'preview' ? (
            <PreviewIframePanel jobId={jobId} />
          ) : nodeDef.name === 'asr' ? (
            <AsrResultPanel jobId={jobId} nodeDef={nodeDef} nodeState={nodeState} onAdvanced={onClose} />
          ) : nodeDef.name === 'rw' ? (
            <RwResultPanel jobId={jobId} nodeDef={nodeDef} nodeState={nodeState} onAdvanced={onClose} />
          ) : nodeDef.name === 'lines' ? (
            <LinesPanel jobId={jobId} nodeDef={nodeDef} nodeState={nodeState} onAdvanced={onClose} />
          ) : nodeDef.name === 'image' ? (
            <ImageResultPanel jobId={jobId} nodeDef={nodeDef} nodeState={nodeState} onAdvanced={onClose} />
          ) : nodeDef.name === 'tts' ? (
            <TtsResultPanel jobId={jobId} nodeDef={nodeDef} nodeState={nodeState} onAdvanced={onClose} />
          ) : (
            <GenericNodePanel nodeDef={nodeDef} nodeState={nodeState} />
          )}
        </div>
      </aside>
    </>
  );
}
