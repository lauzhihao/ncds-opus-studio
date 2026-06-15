// 进程状态行（柳永 4 模型 / 沈括阶段 / lines / image / storyboard / tts 共用）。
// 直角信息框 + 状态配色 + 状态标签。原先寄居在 RwResultPanel 里，抽成独立通用件。

import { AlertTriangle, CheckCircle2, Circle, XCircle } from 'lucide-react';

export type ProcStatus = 'pending' | 'running' | 'done' | 'failed' | 'unavailable';

export interface ProcRow {
  id: string;
  label: string;
  status: ProcStatus;
  detail?: string; // hover tooltip 显示的完整信息（如失败原因全文）
}

const PROC_MAP: Record<ProcStatus, { icon: (s: number) => React.ReactNode; text: string }> = {
  pending: { icon: (s) => <Circle size={s} strokeWidth={1.7} className="rw-ms-pending" />, text: '等待中' },
  running: { icon: () => <span className="rw-ms-blink-dot" aria-label="执行中" />, text: '执行中' },
  done: { icon: (s) => <CheckCircle2 size={s} strokeWidth={2} className="rw-ms-done" />, text: '完成' },
  failed: { icon: (s) => <XCircle size={s} strokeWidth={2} className="rw-ms-failed" />, text: '错误' },
  unavailable: { icon: (s) => <AlertTriangle size={s} strokeWidth={2} className="rw-ms-warn" />, text: '不可用' },
};

export function ProcStatusRow({ row, runningText }: { row: ProcRow; runningText?: string }) {
  const m = PROC_MAP[row.status] ?? PROC_MAP.pending;
  const text = row.status === 'running' && runningText ? runningText : m.text;
  // hover 显示完整信息：有 detail（如失败原因全文）优先，否则 "标签 · 状态"
  const title = row.detail ? `${row.label} · ${row.detail}` : `${row.label} · ${text}`;
  return (
    <div className={`proc-row proc-${row.status}`} title={title}>
      <span className="proc-row-icon">{m.icon(14)}</span>
      <span className="proc-row-label">{row.label}</span>
      <span className="proc-row-badge">{text}</span>
    </div>
  );
}
