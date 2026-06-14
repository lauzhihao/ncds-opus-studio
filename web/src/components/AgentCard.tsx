// React Flow 自定义节点（agent 版）：复用 NodeCard 的 .node 样式类，
// 只显示信息（编号 + agent 图标 + 名字/职责 + 聚合状态徽章 + 进度/简介），
// 整张卡点击打开 agent 抽屉；具体操作在抽屉内的成员面板里。

import { memo } from 'react';
import { Handle, Position } from 'reactflow';

import type { NodeStatus } from '../api/types';
import type { AgentDef } from '../config/agents';

export interface AgentCardData {
  agent: AgentDef;
  status: NodeStatus;
  progress: string;
  index: number;
  isFirst: boolean;
  isLast: boolean;
  onOpen: () => void;
}

const STATUS_LABEL: Record<NodeStatus, string> = {
  idle: 'IDLE',
  queued: 'QUEUED',
  running: 'RUNNING',
  done: 'DONE',
  failed: 'FAILED',
};

const STATUS_ZH: Record<NodeStatus, string> = {
  idle: '待机',
  queued: '排队中',
  running: '执行中',
  done: '已完成',
  failed: '失败',
};

function AgentCardImpl({ data }: { data: AgentCardData }) {
  const { agent, status, progress, index, isFirst, isLast, onOpen } = data;
  const Icon = agent.icon;

  return (
    <div
      className={`node kind-command status-${status}`}
      role="button"
      tabIndex={0}
      aria-label={`${agent.name}（${agent.role}），状态：${STATUS_ZH[status]}。按 Enter 打开详情`}
      onClick={(e) => {
        if ((e.target as HTMLElement).closest('.react-flow__handle')) return;
        onOpen();
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpen();
        }
      }}
    >
      {!isFirst && <Handle type="target" position={Position.Top} />}
      {!isLast && <Handle type="source" position={Position.Bottom} />}

      <div className="head">
        <span className="seq mono">{String(index + 1).padStart(2, '0')}</span>
        <span className="label">
          <Icon size={20} strokeWidth={1.6} style={{ marginRight: 9, verticalAlign: '-3px', color: 'var(--ink-2)' }} />
          {agent.name}
          <span className="dim-mono" style={{ marginLeft: 8, fontSize: 'var(--text-2xs)', opacity: 0.7 }}>
            {agent.role}
          </span>
        </span>
        <span className="status-badge">{STATUS_LABEL[status]}</span>
      </div>

      <div className="body">
        {status === 'running' && (
          <div className="progress-line">
            <span className="spinner" />
            <span className="progress-text">{progress || '执行中…'}</span>
          </div>
        )}
        {status !== 'running' && <div className="brief">{agent.description}</div>}
      </div>
    </div>
  );
}

export const AgentCard = memo(AgentCardImpl);
