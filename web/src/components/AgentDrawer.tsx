// Agent 抽屉：顶部 agent 身份 + 状态监控（成员步进 chips），下方按当前成员渲染现成面板。
//
// 成员推进完全由引擎真实 NEXT 链驱动（见 config/agents.ts NODE_NEXT）：同 agent 内的下一步
// 切 tab，跨 agent 的下一步打开下一个 agent。各成员面板（InputPanel/ShenkuoCollectPanel/...）原样复用，
// 它们内部的 runNode 仍打到底层 engine 节点——"agent 调现成 performer"。

import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { X } from 'lucide-react';

import type { JobState, NodeState, NodeStatus, PipelineDef, PipelineNodeDef } from '../api/types';
import {
  AGENT_BY_NODE,
  NODE_NEXT,
  agentMemberStatuses,
  type AgentDef,
  type AgentId,
} from '../config/agents';
import { NodeStatusBar } from './NodeStatusBar';
import { ShenkuoCollectPanel } from './panels/ShenkuoCollectPanel';
import { GenericNodePanel } from './panels/GenericNodePanel';
import { GuiguziPanel } from './panels/GuiguziPanel';
import { ImageResultPanel } from './panels/ImageResultPanel';
import { InputPanel } from './panels/InputPanel';
import { LinesPanel } from './panels/LinesPanel';
import { PreviewIframePanel } from './panels/PreviewIframePanel';
import { LiuyongPanel } from './panels/LiuyongPanel';
import { StepRunPanel } from './panels/StepRunPanel';
import { StoryboardPanel } from './panels/StoryboardPanel';
import { TtsResultPanel } from './panels/TtsResultPanel';

interface Props {
  jobId: string;
  agent: AgentDef;
  job: JobState;
  pipeline: PipelineDef;
  angleConfirmed: boolean;
  onClose: () => void;
  // 跨 agent 推进：打开下一个 agent 抽屉。
  onAdvanceAgent: (nextAgentId: AgentId) => void;
  // 鬼谷子选定选题时上报：让 page 翻 angleConfirmed（解耦后选题不再触发 rw 的 SSE，需本地脉冲）。
  onTopicConfirmed?: () => void;
}

function defaultIdleState(name: string): NodeState {
  return {
    name,
    status: 'idle',
    started_at: null,
    finished_at: null,
    progress: '',
    outputs: {},
    error: null,
    task_id: null,
  };
}

const STATUS_DOT_ZH: Record<NodeStatus, string> = {
  idle: '待机',
  queued: '排队',
  running: '执行中',
  done: '完成',
  failed: '失败',
};

export function AgentDrawer({ jobId, agent, job, pipeline, angleConfirmed, onClose, onAdvanceAgent, onTopicConfirmed }: Props) {
  const { members } = agent;
  const Icon = agent.icon;

  const memberStatuses = agentMemberStatuses(agent, job.nodes, { angleConfirmed });

  // 当前展示的成员：默认聚焦首个 running，否则首个未完成，否则末位。用户点 chip 后停止自动聚焦。
  const computeFocus = () => {
    const running = memberStatuses.findIndex((s) => s === 'running' || s === 'queued');
    if (running >= 0) return running;
    const undone = memberStatuses.findIndex((s) => s !== 'done');
    return undone >= 0 ? undone : members.length - 1;
  };
  const [active, setActive] = useState(computeFocus);
  const manualRef = useRef(false);

  // SSE 推新状态时，未手动切过 tab 则跟随聚焦。
  useEffect(() => {
    if (manualRef.current) return;
    setActive(computeFocus());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [memberStatuses.join(',')]);

  const asideRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  // Esc 关闭
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  useEffect(() => {
    const t = window.setTimeout(() => closeRef.current?.focus(), 0);
    return () => window.clearTimeout(t);
  }, []);

  function trapFocus(e: ReactKeyboardEvent) {
    if (e.key !== 'Tab') return;
    const root = asideRef.current;
    if (!root) return;
    const focusables = Array.from(
      root.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    ).filter((el) => el.offsetParent !== null);
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  // 推进：从某底层节点完成 -> 引擎真实 NEXT 链决定去哪。
  function advance(fromNode: string) {
    const next = NODE_NEXT[fromNode];
    if (!next) { onClose(); return; }
    const nextAgentId = AGENT_BY_NODE[next];
    if (nextAgentId === agent.id) {
      const idx = members.findIndex((m) => m.node === next);
      manualRef.current = true;
      if (idx >= 0) setActive(idx);
    } else {
      onAdvanceAgent(nextAgentId);
    }
  }

  const activeMember = members[active] ?? members[0];
  const activeNode = activeMember.node;
  const nodeDef: PipelineNodeDef | undefined = pipeline.nodes.find((n) => n.name === activeNode);
  const nodeState = job.nodes[activeNode] ?? defaultIdleState(activeNode);
  const isFullscreen = activeNode === 'preview';

  function renderMember() {
    switch (activeNode) {
      case 'input':
        return (
          <InputPanel
            jobId={jobId}
            nodeDef={nodeDef!}
            nodeState={nodeState}
            asrStatus={job.nodes.asr?.status}
            onStarted={() => advance('input')}
          />
        );
      case 'asr':
        return (
          <ShenkuoCollectPanel jobId={jobId} nodeDef={nodeDef!} nodeState={nodeState} onAdvanced={() => advance('asr')} />
        );
      case 'guiguzi':
        return (
          <GuiguziPanel
            jobId={jobId}
            job={job}
            onConfirmed={() => {
              // 解耦：选题确认既翻鬼谷子 DONE（上报 page 脉冲），又跳到柳永面板（柳永停 IDLE 待手动启动）。
              onTopicConfirmed?.();
              advance('guiguzi');
            }}
            onGotoShenkuo={() => onAdvanceAgent('shenkuo')}
          />
        );
      case 'rw':
        return (
          <LiuyongPanel jobId={jobId} nodeDef={nodeDef!} nodeState={nodeState} onAdvanced={() => advance('rw')} />
        );
      case 'lines':
        return (
          <LinesPanel jobId={jobId} nodeDef={nodeDef!} nodeState={nodeState} onAdvanced={() => advance('lines')} />
        );
      case 'storyboard':
        return (
          <StoryboardPanel jobId={jobId} nodeDef={nodeDef!} nodeState={nodeState} onAdvanced={() => advance('storyboard')} />
        );
      case 'image':
        return (
          <ImageResultPanel jobId={jobId} nodeDef={nodeDef!} nodeState={nodeState} onAdvanced={() => advance('image')} />
        );
      case 'tts':
        return (
          <TtsResultPanel jobId={jobId} nodeDef={nodeDef!} nodeState={nodeState} onAdvanced={() => advance('tts')} />
        );
      case 'preview':
        return (
          <div className="agent-preview-wrap">
            <PreviewIframePanel jobId={jobId} />
            <div className="panel-footer">
              <button className="btn primary" onClick={() => advance('preview')}>通过审核 · 去渲染</button>
            </div>
          </div>
        );
      case 'render':
        return (
          <StepRunPanel
            jobId={jobId}
            nodeDef={nodeDef ?? { name: 'render', label: '渲染', cmd: '', deps: [], out_dir: '', description: '渲染成片', kind: 'command', position: { x: 0, y: 0 } }}
            nodeState={nodeState}
            actionLabel="开始渲染"
            rerunLabel="重新渲染"
            onAdvanced={() => advance('render')}
          />
        );
      case 'download':
        return (
          <StepRunPanel
            jobId={jobId}
            nodeDef={nodeDef ?? { name: 'download', label: '下载', cmd: '', deps: [], out_dir: '', description: '导出成片', kind: 'output', position: { x: 0, y: 0 } }}
            nodeState={nodeState}
            actionLabel="完成"
            onAdvanced={onClose}
          />
        );
      default:
        return nodeDef ? <GenericNodePanel nodeDef={nodeDef} nodeState={nodeState} /> : null;
    }
  }

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside
        ref={asideRef}
        className={`drawer${isFullscreen ? ' fullscreen' : ''}${agent.id === 'guiguzi' ? ' wide' : ''}`}
        role="dialog"
        aria-modal
        aria-labelledby="agent-drawer-title"
        onKeyDown={trapFocus}
      >
        <div className="head">
          <div className="icon-frame">
            <Icon size={18} strokeWidth={1.6} />
          </div>
          <div className="titles">
            <h3 className="title" id="agent-drawer-title">{agent.name}</h3>
            <div className="subtitle">{agent.role}</div>
          </div>
          <button ref={closeRef} className="btn sm icon-only ghost" onClick={onClose} title="关闭 (Esc)" aria-label="关闭 (Esc)">
            <X size={14} strokeWidth={1.6} />
          </button>
        </div>

        {/* 成员步进 chips：agent 的状态监控面板 */}
        {members.length > 1 && (
          <div className="agent-steps" role="tablist" aria-label={`${agent.name} 步骤`}>
            {members.map((m, i) => {
              const st = memberStatuses[i];
              return (
                <button
                  key={m.node}
                  role="tab"
                  aria-selected={i === active}
                  className={`agent-step status-${st}${i === active ? ' active' : ''}`}
                  title={`${m.label} · ${STATUS_DOT_ZH[st]}`}
                  onClick={() => { manualRef.current = true; setActive(i); }}
                >
                  <span className={`step-dot status-${st}`} />
                  {m.label}
                </button>
              );
            })}
          </div>
        )}

        <div className="body">
          {!isFullscreen && <NodeStatusBar nodeState={nodeState} jobId={jobId} />}
          {renderMember()}
        </div>
      </aside>
    </>
  );
}
