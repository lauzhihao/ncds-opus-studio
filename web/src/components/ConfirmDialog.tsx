// 项目内通用确认对话框；替代 window.confirm。
// 声明式 API：open=true 时显示；Esc 或点击 backdrop 调 onCancel。

import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

interface Props {
  open: boolean;
  title: string;
  message?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = '确认',
  cancelLabel = '取消',
  danger = false,
  onConfirm,
  onCancel,
}: Props) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onCancel();
    };
    window.addEventListener('keydown', h);
    confirmRef.current?.focus();
    return () => window.removeEventListener('keydown', h);
  }, [open, busy, onCancel]);

  if (!open) return null;

  async function handleConfirm() {
    if (busy) return;
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="modal-backdrop"
      onClick={() => !busy && onCancel()}
      role="presentation"
    >
      <div
        className="confirm-dialog"
        role="dialog"
        aria-modal
        aria-labelledby="confirm-dialog-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div id="confirm-dialog-title" className="title">{title}</div>
        {message && <div className="message">{message}</div>}
        <div className="actions">
          <button
            type="button"
            className="btn ghost sm"
            disabled={busy}
            onClick={onCancel}
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            type="button"
            className={`btn sm ${danger ? 'danger-solid' : 'primary'}`}
            disabled={busy}
            onClick={handleConfirm}
          >
            {busy ? '处理中…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
