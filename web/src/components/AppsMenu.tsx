import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Sparkles } from 'lucide-react';

const APPS = [
  { key: 'gen', label: '图片生成', icon: Sparkles },
] as const;

export function AppsMenu() {
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (
        panelRef.current && !panelRef.current.contains(e.target as Node) &&
        btnRef.current && !btnRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <div className="apps-menu">
      <button
        ref={btnRef}
        type="button"
        className="apps-menu-btn"
        aria-label="创作工具"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="5" cy="5" r="1.5" fill="currentColor" stroke="none" />
          <circle cx="12" cy="5" r="1.5" fill="currentColor" stroke="none" />
          <circle cx="19" cy="5" r="1.5" fill="currentColor" stroke="none" />
          <circle cx="5" cy="12" r="1.5" fill="currentColor" stroke="none" />
          <circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
          <circle cx="19" cy="12" r="1.5" fill="currentColor" stroke="none" />
          <circle cx="5" cy="19" r="1.5" fill="currentColor" stroke="none" />
          <circle cx="12" cy="19" r="1.5" fill="currentColor" stroke="none" />
          <circle cx="19" cy="19" r="1.5" fill="currentColor" stroke="none" />
        </svg>
      </button>
      {open && (
        <>
          <div className="apps-menu-backdrop" onClick={() => setOpen(false)} />
          <div ref={panelRef} className="apps-menu-panel" role="menu">
            {APPS.map((app) => (
              <button
                key={app.key}
                type="button"
                className="apps-menu-item"
                role="menuitem"
                onClick={() => { setOpen(false); nav(`/canvas/${app.key}`); }}
              >
                <span className="apps-menu-item-icon"><app.icon size={16} strokeWidth={1.6} /></span>
                <span className="apps-menu-item-label">{app.label}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
