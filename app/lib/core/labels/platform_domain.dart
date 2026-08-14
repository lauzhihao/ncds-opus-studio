// 平台 / 赛道展示文案 —— 对齐 web `utils/platform.ts` + `config/domains.ts`。
// 存储仍用英文 key（douyin/tiktok/youtube、finance/emotion）；UI 只走这里取 label。

/// 平台 badge 文案（web: platformDisplayName）。
String platformDisplayName(String? platform) {
  switch ((platform ?? '').toLowerCase().trim()) {
    case 'tiktok':
      return 'TK';
    case 'youtube':
      return '油管';
    case 'douyin':
    case '':
      return '抖音';
    default:
      // 未知 key 原样回退，避免把新平台吞成抖音。
      return platform!.trim();
  }
}

/// 赛道 badge 文案（web: domainByKey(...).label）。未知 key 返回 null（调用方可不渲染）。
String? domainDisplayName(String? domain) {
  switch ((domain ?? '').toLowerCase().trim()) {
    case 'finance':
      return '财经';
    case 'emotion':
      return '情感';
    case 'film':
      return '影视';
    case 'comedy':
      return '搞笑';
    case '':
      return null;
    default:
      return domain!.trim();
  }
}

/// 与 web DOMAINS 一致的可选赛道（编辑表单用）。
const List<({String key, String label})> kDomainOptions = <({String key, String label})>[
  (key: 'finance', label: '财经'),
  (key: 'emotion', label: '情感'),
  (key: 'film', label: '影视'),
  (key: 'comedy', label: '搞笑'),
];
