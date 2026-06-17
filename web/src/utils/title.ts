// 作品标题解析：把「正文 + 内嵌 #话题」拆成正文标题 + 话题标签列表。
// 沈括详情、作品卡片、画布顶栏共用，统一「作品 + 话题标签」呈现。

export interface TitleParts {
  title: string; // 剥掉话题后的正文标题（为空时回退原文）
  tags: string[]; // 话题标签（不含前导 #）
}

// 两种输入模式：
//   - 传 hashtags 数组（沈括 ShenkuoEntry 有结构化 hashtags）→ 按它把 desc 里内嵌的 #话题剥掉；
//   - 不传 / 空数组（作品卡片 JobSummary、画布只有 title 字符串）→ 正则从文本里提取 #话题。
// 两种模式都把多余空白折叠成单空格再 trim，得到干净正文。
export function parseTitleTags(raw?: string | null, hashtags?: string[]): TitleParts {
  const text = (raw ?? '').trim();
  if (!text) return { title: '', tags: [] };

  let tags: string[];
  let body = text;
  if (hashtags && hashtags.length > 0) {
    tags = hashtags;
    for (const tag of hashtags) body = body.replaceAll(`#${tag}`, ' ');
  } else {
    // #话题：# 后跟一段非空白、非 # 字符（覆盖中英文/数字标签）
    const matched = text.match(/#[^\s#]+/g) ?? [];
    tags = matched.map((t) => t.slice(1));
    body = text.replace(/#[^\s#]+/g, ' ');
  }

  const title = body.split(/\s+/).filter(Boolean).join(' ').trim();
  return { title: title || text, tags };
}
