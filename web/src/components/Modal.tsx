// 通用弹窗壳：复用 .modal-backdrop；Esc / 点遮罩关闭。
import { useEffect, type ReactNode } from 'react';
import { X } from 'lucide-react';

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);
  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div className="modal-dialog" role="dialog" aria-modal aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3 className="title">{title}</h3>
          <button className="btn sm icon-only ghost" onClick={onClose} aria-label="关闭 (Esc)" title="关闭 (Esc)">
            <X size={14} strokeWidth={1.6} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
