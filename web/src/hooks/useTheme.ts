// 主题切换：localStorage 持久化，document.documentElement.dataset.theme 控制。
// 4 套：paper / onyx / sage / ink。

import { useEffect, useState } from 'react';

export type ThemeId = 'paper' | 'onyx' | 'sage' | 'ink';

export const THEMES: { id: ThemeId; label: string; swatch: string }[] = [
  { id: 'paper', label: '暖纸', swatch: '#b8362a' },
  { id: 'onyx', label: '暗夜', swatch: '#e8814d' },
  { id: 'sage', label: '鼠尾草', swatch: '#2f6b50' },
  { id: 'ink', label: '黑白报刊', swatch: '#c41e3a' },
];

const STORAGE_KEY = 'ncds-studio-theme';

function readInitial(): ThemeId {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === 'paper' || v === 'onyx' || v === 'sage' || v === 'ink') return v;
  } catch {
    /* SSR / privacy mode */
  }
  return 'paper';
}

export function useTheme(): [ThemeId, (t: ThemeId) => void] {
  const [theme, setTheme] = useState<ThemeId>(readInitial);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  return [theme, setTheme];
}

// 在 main.tsx 启动期同步应用，避免首屏闪烁
export function applyThemeFromStorage() {
  document.documentElement.dataset.theme = readInitial();
}
