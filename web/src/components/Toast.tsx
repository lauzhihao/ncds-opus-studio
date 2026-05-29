// 全局轻量 toast 通知层 —— 替代 window.alert()。
// 设计取向：复用设计系统 token（--shadow-4 / --status-* / --font-sans），不引第三方库。
// 用法：const { showToast } = useToast(); showToast('启动失败，请稍后再试');
// 原始错误对象一律 console.error，不再把英文 traceback 弹给用户。

import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from 'react';
import { AlertCircle, CheckCircle2, Info, X } from 'lucide-react';

export type ToastLevel = 'error' | 'info' | 'success';

interface ToastItem {
  id: number;
  level: ToastLevel;
  message: string;
}

interface ToastApi {
  // level 默认 error（绝大多数调用点是 catch 块）
  showToast: (message: string, level?: ToastLevel) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

// 自增 id，跨 Provider 唯一即可，不参与持久化
let seq = 0;

const ICONS: Record<ToastLevel, typeof Info> = {
  error: AlertCircle,
  info: Info,
  success: CheckCircle2,
};

// error 停留更久（6s），info/success 3.5s
const TTL: Record<ToastLevel, number> = {
  error: 6000,
  info: 3500,
  success: 3500,
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const remove = useCallback((id: number) => {
    setItems((cur) => cur.filter((t) => t.id !== id));
  }, []);

  const showToast = useCallback(
    (message: string, level: ToastLevel = 'error') => {
      const id = ++seq;
      setItems((cur) => [...cur, { id, level, message }]);
      window.setTimeout(() => remove(id), TTL[level]);
    },
    [remove],
  );

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      <div className="toaster" role="region" aria-label="通知" aria-live="polite">
        {items.map((t) => {
          const Icon = ICONS[t.level];
          return (
            <div key={t.id} className={`toast toast-${t.level}`} role="status">
              <Icon size={15} strokeWidth={1.8} className="toast-icon" />
              <span className="toast-msg">{t.message}</span>
              <button
                type="button"
                className="toast-close"
                aria-label="关闭通知"
                onClick={() => remove(t.id)}
              >
                <X size={13} strokeWidth={1.9} />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast 必须在 <ToastProvider> 内使用');
  return ctx;
}
