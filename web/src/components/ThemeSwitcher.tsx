import { THEMES, useTheme } from '../hooks/useTheme';

export function ThemeSwitcher() {
  const [theme, setTheme] = useTheme();
  return (
    <div className="theme-switcher" role="radiogroup" aria-label="主题切换">
      {THEMES.map((t) => (
        <button
          key={t.id}
          className={t.id === theme ? 'active' : ''}
          onClick={() => setTheme(t.id)}
          title={t.label}
          aria-checked={t.id === theme}
          role="radio"
        >
          <span className="swatch" style={{ background: t.swatch }} />
        </button>
      ))}
    </div>
  );
}
