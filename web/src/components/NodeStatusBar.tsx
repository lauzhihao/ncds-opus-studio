// 抽屉顶部统一节点状态条：左侧色条 = 状态指示灯（沿用 panel-hint 的直角风格，border-radius:0）。
// running/queued 呼吸 + 实时计时；done/failed 静止 + 总耗时；failed 展开错误信息 + 任务 ID（可一键复制）。
// running 满 5 分钟前端强制超时：自动调 cancel（已幂等）止损。idle 不渲染——交给面板内引导。

import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, Copy, Loader2 } from 'lucide-react';

import { api } from '../api/client';
import type { NodeState, NodeStatus } from '../api/types';
import { formatElapsed } from '../utils/format';
import { useToast } from './Toast';

const TIMEOUT_S = 300; // 前端强制超时：5 分钟

const STATUS_ZH: Record<NodeStatus, string> = {
  idle: '待机',
  queued: '排队中',
  running: '执行中',
  done: '已完成',
  failed: '失败',
};

export function NodeStatusBar({ nodeState, jobId }: { nodeState: NodeState; jobId: string }) {
  const { name, status, progress, error, task_id, started_at, finished_at } = nodeState;
  const { showToast } = useToast();
  const [copied, setCopied] = useState(false);
  const [nowS, setNowS] = useState(() => Date.now() / 1000);
  const timedOutRef = useRef(false);

  const active = status === 'running' || status === 'queued';

  // running/queued 时每秒 tick：驱动计时递增 + 触发超时检测。
  useEffect(() => {
    if (!active) return;
    setNowS(Date.now() / 1000);
    const id = window.setInterval(() => setNowS(Date.now() / 1000), 1000);
    return () => window.clearInterval(id);
  }, [active]);

  // 离开运行态（结束/重置）时复位超时闸，允许下一轮重跑再计时。
  useEffect(() => {
    if (!active) timedOutRef.current = false;
  }, [active]);

  // 5 分钟强制超时：自动调取消（cancel 已幂等，必成功 → 节点回 idle、脱离 loading）。
  useEffect(() => {
    if (!active || started_at == null || timedOutRef.current) return;
    if (nowS - started_at > TIMEOUT_S) {
      timedOutRef.current = true;
      showToast('本步骤已运行超 5 分钟，已自动取消');
      api.cancelNode(jobId, name).catch(() => { /* 失败静默；SSE 仍会纠偏 */ });
    }
  }, [active, started_at, nowS, jobId, name, showToast]);

  // 空闲态不占位。
  if (status === 'idle') return null;

  // 计时：running 取 now-started（递增）；结束态取 finished-started（总耗时）。
  const elapsed =
    active && started_at != null
      ? Math.max(0, nowS - started_at)
      : started_at != null && finished_at != null
        ? Math.max(0, finished_at - started_at)
        : null;
  const elapsedText =
    elapsed == null ? null : active ? formatElapsed(elapsed) : `耗时 ${formatElapsed(elapsed)}`;

  // 复制「任务ID + 错误」整段，方便用户直接贴回反馈。
  async function copyReport() {
    const text = [task_id ? `任务ID: ${task_id}` : '', error ? `错误: ${error}` : '']
      .filter(Boolean)
      .join('\n');
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      // 无剪贴板权限时静默，不打扰。
    }
  }

  return (
    <div className={`node-status-bar status-${status}`} role="status" aria-live="polite">
      <div className="nsb-main">
        {active && <Loader2 className="nsb-icon spin" size={15} strokeWidth={2} />}
        {status === 'done' && <CheckCircle2 className="nsb-icon" size={15} strokeWidth={1.9} />}
        {status === 'failed' && <AlertTriangle className="nsb-icon" size={15} strokeWidth={1.9} />}
        <span className="nsb-status">{STATUS_ZH[status]}</span>
        {elapsedText && <span className="nsb-elapsed">{elapsedText}</span>}
        {active && progress && <span className="nsb-progress">{progress}</span>}
      </div>

      {status === 'failed' && (error || task_id) && (
        <div className="nsb-fail">
          {error && <div className="nsb-error">{error}</div>}
          <div className="nsb-meta">
            {task_id && <span className="nsb-taskid">任务 ID&nbsp;&nbsp;{task_id}</span>}
            <button className="btn ghost sm nsb-copy" onClick={copyReport}>
              <Copy size={12} strokeWidth={1.8} /> {copied ? '已复制' : '复制'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
