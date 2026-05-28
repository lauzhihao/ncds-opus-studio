// 节点详情抽屉。按 node.name 路由到不同的 panel。
// rw / lines / asr 等有专属 panel，其余走 GenericNodePanel。

import { useEffect, useRef } from 'react';
import {
  BadgeCheck,
  Captions,
  Clapperboard,
  Cog,
  Download,
  Eye,
  Globe,
  Image as ImageIcon,
  Lock,
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
import { StoryboardPanel } from './panels/StoryboardPanel';
import { TtsResultPanel } from './panels/TtsResultPanel';

interface Props {
  jobId: string;
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
  // 兄弟节点的当前状态（用来在 InputPanel 等子面板里做 cross-node 判断，
  // 例如 ASR 已 done 时 INPUT 面板要锁编辑）。
  siblingNodes?: Record<string, NodeState>;
  // 节点已完成且下游已在消费其产物时锁定：抽屉内所有控件禁用（除头部关闭按钮）。
  locked?: boolean;
  onClose: () => void;
  onRun: () => void;
}

function getIcon(name: string): typeof Cog {
  switch (name) {
    case 'input': return Globe;
    case 'asr': return Mic;
    case 'rw': return PenLine;
    case 'lines': return Captions;
    case 'storyboard': return Clapperboard;
    case 'image': return ImageIcon;
    case 'tts': return Volume2;
    case 'preview': return Eye;
    case 'render': return Cog;
    case 'download': return Download;
    case 'output': return Download;
    default: return Cog;
  }
}

export function NodeDrawer({ jobId, nodeDef, nodeState, siblingNodes, locked, onClose, onRun }: Props) {
  const Icon = getIcon(nodeDef.name);

  // Esc 关闭
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  // 锁定：用 inert 让抽屉 body 整棵子树不可交互（按钮/输入框全部失效，且键盘不可聚焦）。
  // fieldset[disabled] 负责把原生控件灰显（:disabled 样式），inert 兜底拦截一切交互。
  // input(START) 例外：它由 InputPanel 自管锁定（禁用编辑+开始按钮，但保留「新建作品」
  // 逃生入口），所以不对其 body 做 inert，只在头部显示「已锁定」chip 保持一致。
  const bodyLocked = !!locked && nodeDef.name !== 'input';
  const lockRef = useRef<HTMLFieldSetElement>(null);
  useEffect(() => {
    if (lockRef.current) lockRef.current.inert = bodyLocked;
  }, [bodyLocked]);

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
          {locked && (
            <span
              className="drawer-lock-chip"
              title="本节点已完成，下游正在使用其产物；下游彻底失败后自动解锁"
            >
              <Lock size={11} strokeWidth={1.9} /> 已锁定
            </span>
          )}
          {nodeDef.kind === 'command' && nodeDef.name !== 'input' && nodeDef.name !== 'asr' && nodeDef.name !== 'rw' && nodeDef.name !== 'lines' && nodeDef.name !== 'storyboard' && nodeDef.name !== 'image' && nodeDef.name !== 'tts' && (
            <button className="btn primary sm" disabled={locked} onClick={onRun}>
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
          <fieldset className="drawer-lock" disabled={bodyLocked} ref={lockRef}>
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
            ) : nodeDef.name === 'storyboard' ? (
              <StoryboardPanel jobId={jobId} nodeDef={nodeDef} nodeState={nodeState} onAdvanced={onClose} />
            ) : nodeDef.name === 'image' ? (
              <ImageResultPanel jobId={jobId} nodeDef={nodeDef} nodeState={nodeState} onAdvanced={onClose} />
            ) : nodeDef.name === 'tts' ? (
              <TtsResultPanel jobId={jobId} nodeDef={nodeDef} nodeState={nodeState} onAdvanced={onClose} />
            ) : (
              <GenericNodePanel nodeDef={nodeDef} nodeState={nodeState} />
            )}
          </fieldset>
        </div>
      </aside>
    </>
  );
}
