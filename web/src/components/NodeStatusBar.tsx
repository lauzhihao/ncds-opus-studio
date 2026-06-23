// 抽屉顶部统一节点状态条：左侧色条 = 状态指示灯（沿用 panel-hint 的直角风格，border-radius:0）。
// running/queued 呼吸 + 实时计时；done/failed 静止 + 总耗时；failed 展开错误信息 + 任务 ID（可一键复制）。
// 注意：本组件消费 SSE 状态，只展示长任务进度，不做前端超时取消。

import { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, Copy, Loader2 } from 'lucide-react';

import type { NodeState, NodeStatus } from '../api/types';
import { formatElapsed } from '../utils/format';
import { friendlyProgressText } from '../utils/progress';

const STATUS_ZH: Record<NodeStatus, string> = {
  idle: '待机',
  queued: '排队中',
  running: '执行中',
  done: '已完成',
  failed: '失败',
};

function friendlyNodeProgress(name: string, progress: string | null | undefined): string {
  return friendlyProgressText(name, progress);
}

function friendlyNodeError(name: string, error: string | null | undefined): string {
  let msg = (error ?? '').trim();
  if (!msg) return '';
  msg = msg.replace(/^RuntimeError: engine step \w+ -> failed:\s*/g, '');
  msg = msg.replace(/^(?:RuntimeError|ValueError|Exception):\s*/g, '');
  if (name === 'lines') {
    if (/视觉方案准备暂时失败|台词结构化|launcher exited|Traceback|beats|模型/.test(msg)) {
      return '视觉方案准备暂时失败：备用通道都没有成功，请稍后重试。';
    }
  }
  if (name === 'storyboard') {
    if (/视觉方案生成暂时失败|launcher exited|Traceback|director agent|beats|模型/.test(msg)) {
      return '视觉方案生成暂时失败：备用通道都没有成功，请稍后重试。';
    }
  }
  if (name === 'image') {
    if (/unrecognized arguments: --quality/.test(msg)) {
      return '画面资产生成失败：生图参数已更新，请重新生成画面资产。';
    }
    if (/gpt-image|scene image generations failed|Traceback/.test(msg)) {
      return '画面资产生成暂时失败：请稍后重试，详细错误已写入服务日志。';
    }
  }
  if (/launcher exited|Traceback/.test(msg)) {
    return '执行暂时失败：请稍后重试。';
  }
  return msg;
}

export function NodeStatusBar({ nodeState }: { nodeState: NodeState }) {
  const { name, status, progress, error, task_id, started_at, finished_at } = nodeState;
  const [copied, setCopied] = useState(false);
  const [nowS, setNowS] = useState(() => Date.now() / 1000);

  const active = status === 'running' || status === 'queued';

  // running/queued 时每秒 tick：只驱动计时显示；长任务结束由 SSE 状态决定。
  useEffect(() => {
    if (!active) return;
    setNowS(Date.now() / 1000);
    const id = window.setInterval(() => setNowS(Date.now() / 1000), 1000);
    return () => window.clearInterval(id);
  }, [active]);

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
  const displayProgress = friendlyNodeProgress(name, progress);
  const displayError = friendlyNodeError(name, error);

  // 复制「任务ID + 错误」整段，方便用户直接贴回反馈。
  async function copyReport() {
    const text = [task_id ? `任务ID: ${task_id}` : '', displayError ? `错误: ${displayError}` : '']
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
        {active && displayProgress && <span className="nsb-progress">{displayProgress}</span>}
        {/* running/done 也露出任务ID（failed 在下方详情块带复制按钮，这里不重复） */}
        {status !== 'failed' && task_id && (
          <span className="nsb-taskid nsb-taskid-inline">任务 ID&nbsp;&nbsp;{task_id}</span>
        )}
      </div>

      {status === 'failed' && (displayError || task_id) && (
        <div className="nsb-fail">
          {displayError && <div className="nsb-error">{displayError}</div>}
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
