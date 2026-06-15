// 数字缩写：1.2万 / 3.4亿（粉丝/获赞/作品等计数展示）
export function formatCount(n: number): string {
  if (n >= 1e8) return `${(n / 1e8).toFixed(1)}亿`;
  if (n >= 1e4) return `${(n / 1e4).toFixed(1)}万`;
  return String(n);
}

// 相对时间：刚刚 / X小时前 / X天前 / X周前（tsSeconds = unix 秒）
export function timeAgo(tsSeconds?: number | null): string {
  if (!tsSeconds) return '';
  const diff = Date.now() / 1000 - tsSeconds;
  if (diff < 3600) return '刚刚';
  if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}天前`;
  return `${Math.floor(diff / 604800)}周前`;
}

// 运行计时 / 总耗时：<60s 显示「45s」，>=60s 显示「1m 30s」（seconds = 秒，可为小数）
export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

// 视频时长：秒 -> 「m:ss」（>=1h 显示「h:mm:ss」）。用于封面右下角时长徽标。
export function formatDuration(seconds?: number | null): string {
  const s = Math.max(0, Math.floor(seconds ?? 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, '0');
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${sec}`;
  return `${m}:${sec}`;
}
