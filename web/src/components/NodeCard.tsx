// React Flow 自定义节点：只显示信息（编号 + 图标 + label + 状态徽章 + 简介/进度），
// 整张卡点击打开抽屉；运行/重跑等操作全部移到抽屉内。

import { memo } from 'react';
import { Handle, Position } from 'reactflow';
import {
  Cog,
  Download,
  Eye,
  Globe,
  Image as ImageIcon,
  Mic,
  PenLine,
  Sparkles,
  Volume2,
} from 'lucide-react';

import type { NodeState, PipelineNodeDef } from '../api/types';

export interface NodeCardData {
  def: PipelineNodeDef;
  state: NodeState;
  index: number;
  onOpen: () => void;
}

const STATUS_LABEL: Record<NodeState['status'], string> = {
  idle: 'IDLE',
  queued: 'QUEUED',
  running: 'RUNNING',
  done: 'DONE',
  failed: 'FAILED',
};

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
    default: return Sparkles;
  }
}

function NodeCardImpl({ data }: { data: NodeCardData }) {
  const { def, state, index, onOpen } = data;
  const Icon = getIcon(def.name);
  const isInput = def.kind === 'input';
  const isOutput = def.kind === 'output';
  // asr 节点卡片只显示一句概要；子阶段（下载/提取音频/转写/整理）只在抽屉里追加展示
  const displayProgress =
    def.name === 'asr' ? '高精度语音转文字' : (state.progress || '执行中…');

  return (
    <div
      className={`node kind-${def.kind} status-${state.status}`}
      onClick={(e) => {
        // 让 React Flow 自己处理 drag-start；正常 click 才打开抽屉
        if ((e.target as HTMLElement).closest('.react-flow__handle')) return;
        onOpen();
      }}
    >
      {!isInput && <Handle type="target" position={Position.Top} />}
      {!isOutput && <Handle type="source" position={Position.Bottom} />}

      <div className="head">
        <span className="seq mono">{String(index).padStart(2, '0')}</span>
        <span className="label">
          <Icon size={20} strokeWidth={1.6} style={{ marginRight: 9, verticalAlign: '-3px', color: 'var(--ink-2)' }} />
          {def.label}
        </span>
        <span className="status-badge">{STATUS_LABEL[state.status]}</span>
      </div>

      <div className="body">
        {state.status === 'running' && (
          <div className="progress-line">
            <span className="spinner" />
            <span>{displayProgress}</span>
          </div>
        )}
        {state.status === 'failed' && state.error && (
          <div style={{ color: 'var(--status-failed)', fontSize: 'var(--text-xs)' }}>
            {state.error}
          </div>
        )}
        {(state.status === 'idle' || state.status === 'queued' || state.status === 'done') && (
          <div className="brief">{def.description}</div>
        )}
      </div>
    </div>
  );
}

export const NodeCard = memo(NodeCardImpl);
