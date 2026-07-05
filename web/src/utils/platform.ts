export function platformDisplayName(platform?: string | null): string {
  switch ((platform || '').toLowerCase()) {
    case 'tiktok':
      return 'TK';
    case 'youtube':
      return '油管';
    case 'douyin':
    default:
      return '抖音';
  }
}

export function platformBadgeClass(platform?: string | null): string {
  const key = (platform || '').toLowerCase();
  return key === 'tiktok' || key === 'youtube' ? key : 'douyin';
}
