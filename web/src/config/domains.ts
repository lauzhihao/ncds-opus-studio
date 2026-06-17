// 领域标签配置（关注作者时单选）。
//
// 领域 = 一个 profile：背后对应一组提示词（选题/撰稿等），每领域要求不同。
// 提示词目前还没填，占位在后端 domain_profiles.py（key 与这里一一对应）。
// 这里只管「展示」：弹窗的领域单选器与作者卡片的领域徽标共用此单一数据源。
//
// 存储用稳定英文 key（finance/football/emotion），与展示文案/配色解耦——
// 后续改 label 或加领域不影响已存数据。

export type DomainKey = 'finance' | 'football' | 'emotion';

export interface DomainDef {
  key: DomainKey;
  label: string; // 中文展示名
  // 颜色类名后缀：对应 SCSS .domain-badge.<colorClass> / .domain-pill.<colorClass>
  colorClass: 'finance' | 'football' | 'emotion';
}

export const DOMAINS: DomainDef[] = [
  { key: 'finance', label: '财经', colorClass: 'finance' },
  { key: 'football', label: '足球', colorClass: 'football' },
  { key: 'emotion', label: '情感', colorClass: 'emotion' },
];

// 默认领域：保证「关注作者」仍是一键流程（弹窗默认选中第一个）。
export const DEFAULT_DOMAIN: DomainKey = DOMAINS[0].key;

// key -> 定义，卡片徽标按 author.domain 查 label/配色；未知 key 返回 undefined（不渲染）。
export function domainByKey(key?: string | null): DomainDef | undefined {
  if (!key) return undefined;
  return DOMAINS.find((d) => d.key === key);
}
