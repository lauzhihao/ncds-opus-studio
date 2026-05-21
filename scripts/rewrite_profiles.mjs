export const DEFAULT_REWRITE_PROFILE_ID = 'toutiao';
export const DEFAULT_ARTICLE_REWRITE_PROFILE_ID = 'douyin';

function joinNonEmptyLines(lines) {
  return lines.filter((line) => typeof line === 'string' && line.length > 0).join('\n');
}

function formatList(items = []) {
  return Array.isArray(items) && items.length > 0 ? items.join('；') : '（无）';
}

function formatText(value, fallback = '（未提供）') {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : fallback;
}

function buildAnalysisSummaryBlock(analysisRecord = {}) {
  const record = analysisRecord && typeof analysisRecord === 'object' ? analysisRecord : {};
  const formatName = record.formatName || '头条';
  return joinNonEmptyLines([
    `已设定的【${formatName}】改写要求：`,
    `- 平台格式特征：${formatList(record.formatFeatures)}`,
    `- 行文逻辑：${formatList(record.writingLogic)}`,
    `- 表达方式：${formatList(record.expressionStyle)}`,
    `- 目标文章类型：${formatText(record.articleType)}`,
    `- 目标受众：${formatText(record.audience)}`,
    `- 平台语气：${formatText(record.platformTone)}`,
    '',
    '原素材分析：',
    `- 原文类型：${formatText(record.sourceArticleType)}`,
    `- 原文风格：${formatList(record.sourceStyle)}`,
    `- 核心摘要：${formatText(record.summary)}`,
    `- 可保留事实：${formatList(record.keyFacts)}`,
    `- 主观判断：${formatList(record.subjectiveClaims)}`,
    `- 必须保留：${formatList(record.mustKeep)}`,
    `- 必须避免：${formatList(record.mustAvoid)}`,
    `- 可用切入角度：${formatList(record.rewriteAngles)}`,
    `- 风险约束：${formatList(record.risks)}`,
    '',
    '标题分析：',
    `- 核心看点：${formatList(record.coreHighlights)}`,
    `- 冲突点：${formatList(record.conflictPoints)}`,
    `- 情绪点：${formatList(record.emotionPoints)}`,
    `- 立场点：${formatList(record.stancePoints)}`,
    `- 受众情绪：${formatList(record.audienceEmotions)}`,
    `- 传播钩子：${formatList(record.propagationHooks)}`,
    `- 平台语境：${formatText(record.platformContext)}`,
    `- 标题打法：${formatText(record.headlineType)}`,
    `- 打法原因：${formatText(record.headlineApproachReason)}`,
    `- 标题公式：${formatList(record.headlineFormula)}`,
    `- 标题备选：${formatList(record.headlineCandidates)}`,
    `- 最强标题：${formatText(record.bestHeadline)}`,
    `- 标题理由：${formatText(record.bestHeadlineReason)}`,
    `- 学到的知识：${formatList(record.learnedKnowledge)}`,
  ]);
}

function buildDouyinOutlinePrompt({ transcriptText }) {
  return joinNonEmptyLines([
    '请基于下面这份中文转写/清洗稿，提炼一份供短视频改写使用的大纲。',
    '只保留事实、观点、切入角度和表达约束，不要直接写成口播稿。',
    '',
    '必须只输出 JSON 对象，字段固定为：',
    '{',
    '  "topic": string,        // 一句话概括主题',
    '  "corePoints": string[], // 原文的核心观点',
    '  "facts": string[],      // 原文中可引用的事实依据',
    '  "angles": string[],     // 适合短视频的切入角度',
    '  "constraints": string[] // 改写时必须遵守的内容约束（如不可编造的事实边界、敏感表述等）',
    '}',
    '',
    '注意：constraints 只填内容层面的约束，不要填输出格式要求。',
    '使用简体中文，只输出合法 JSON，不要输出 markdown 代码块，不要解释额外内容。',
    '',
    '原文如下：',
    transcriptText,
  ]);
}

function buildDouyinDraftPrompt({ outline, analysisRecord = null }) {
  // passthrough 模式：outline 携带 sourceText 时，直接把整份源文档当作"写作指南"喂给模型，
  // 跳过 outline 压缩造成的信息丢失。否则回退到旧的结构化大纲模式（向后兼容）。
  const sourceText = typeof outline?.sourceText === 'string' && outline.sourceText.trim()
    ? outline.sourceText.trim()
    : '';
  const userRequirements = typeof outline?.userRequirements === 'string' && outline.userRequirements.trim()
    ? outline.userRequirements.trim()
    : (typeof analysisRecord?.userRequirements === 'string' && analysisRecord.userRequirements.trim()
      ? analysisRecord.userRequirements.trim()
      : '');

  if (sourceText) {
    return joinNonEmptyLines([
      '下面是一份「爆款内容分析报告」，它不是待逐字改写的原文，而是写作指南：',
      '其中可能包含主题概览、叙事角度、写作风格、爆款原因、可复用模板、金句公式等结构化要素。',
      '请你扮演抖音口播稿写手，把这份分析报告当作创作依据，写一篇全新的抖音口播稿。',
      '',
      '要求：',
      '- 只输出最终稿件正文，不要解释、不要 markdown 代码块',
      '- 必须使用简体中文',
      '- 充分吸收报告中的「叙事角度」「写作风格」「可复用模板」「金句公式」「结尾模板」等内容，把它们落到稿件里',
      '- 开头钩子要直接抛出核心反差或反常识结论，黄金 3 秒抓住注意力',
      '- 主体按报告里给出的结构模板推进，段落之间过渡自然，节奏感强',
      '- 适当使用对比、反转、追问句式，复用报告里提到的金句结构',
      '- 结尾要落到低门槛行动号召，自然引导互动',
      '- 正文字数 1800～2000 字（含标点，不含可能的标题行），不要过短',
      '- 全文加上自然标点和分段，适合业务方直接挑选',
      '',
      '【硬性禁止】：',
      '- 不得引入报告未涉及的人物、案例、数据、平台名或专有名词',
      '- 不得为了"高级感"对报告观点重新命名包装；只能使用报告里已出现的措辞或其直白同义改写',
      '- 不得引入报告未出现的概念名词、标签词、潮流术语、品类名或自造金句标签',
      '- 如果报告里反复使用某个标签或口号，可以保留并复用；除此之外，一律不得新增概念性命名',
      '',
      '== 爆款内容分析报告 ==',
      sourceText,
      '== 报告结束 ==',
      userRequirements ? '' : '',
      userRequirements ? '【用户附加要求（最高优先级，必须遵守，可覆盖以上默认字数等约束）】：' : '',
      userRequirements,
    ]);
  }

  return joinNonEmptyLines([
    '请根据下面这份结构化大纲，写一篇适合抖音口播的视频稿。',
    '',
    '要求：',
    '- 只输出最终稿件正文，不要解释',
    '- 必须使用简体中文',
    '- 开头要有明显钩子，正文要有节奏感',
    '- 可以重组结构，但不能编造事实',
    '- 不要照抄原文句式，不要写成逐字稿',
    '- 全文加上自然标点和分段，适合业务方直接挑选',
    '',
    '【硬性禁止】：',
    '- 不得引入原文未出现的概念名词、标签词、潮流术语、品类名或自造金句标签',
    '  （例如：原文没有"一人公司""第二曲线""慢就业""多维价值交换"等说法时，禁止在改写稿中出现）',
    '- 不得为了"高级感"对原文观点重新命名包装；表达原文观点时，只能使用原文已经使用过的措辞或其直白同义改写',
    '- 不得引入原文未提到的人物、案例、数据、平台名或专有名词',
    '- 如果原文已经反复使用某个标签或口号，可以保留并复用；除此之外，一律不得新增概念性命名',
    '',
    `主题：${outline.topic}`,
    `核心观点：${formatList(outline.corePoints)}`,
    `事实依据：${formatList(outline.facts)}`,
    `切入角度：${formatList(outline.angles)}`,
    `表达约束：${formatList(outline.constraints)}`,
    userRequirements ? '' : '',
    userRequirements ? '【用户附加要求（最高优先级，必须遵守）】：' : '',
    userRequirements,
  ]);
}

function buildToutiaoAnalysisPrompt({ sourceText }) {
  return joinNonEmptyLines([
    '你是专业的“今日头条爆款标题分析师”。',
    '请先仔细分析下面这篇文章的内容主题、核心冲突、受众情绪、传播钩子、隐秘链条和平台语境，再输出适合今日头条的标题分析结论和改写约束。',
    '你同时要完成两件事：',
    '1. 做好原素材分析，给后续图文改写提供事实边界与表达约束。',
    '2. 按今日头条爆款标题分析方法，产出高质量标题策略，供后续标题生成直接参考。',
    '',
    '必须只输出 JSON 对象，字段固定为：',
    '{',
    '  "formatName": string,          // 固定写“头条”',
    '  "sourceStyle": string[],       // 原素材本身的表达风格，不是头条平台格式',
    '  "sourceArticleType": string,   // 原素材本身的内容类型',
    '  "normalizedText": string,      // 对原文做纠错、去噪、断句整理和必要的图文化清洗，不得编造新事实',
    '  "summary": string,             // 一句话概括原文核心信息',
    '  "keyFacts": string[],          // 原文明确给出的事实或可保守转述的信息',
    '  "subjectiveClaims": string[],  // 原文中的主观判断、效果感受、对比结论或未经证实的说法',
    '  "mustKeep": string[],          // 改写时必须保留的核心信息',
    '  "mustAvoid": string[],         // 改写时需要降温、不要照搬或不要扩写的表达',
    '  "rewriteAngles": string[],     // 适合改写成头条图文的切入角度',
    '  "risks": string[],             // 合规、事实、数据、敏感表达等风险约束',
    '  "coreHighlights": string[],    // 内容拆解：核心看点',
    '  "conflictPoints": string[],    // 内容拆解：冲突点',
    '  "emotionPoints": string[],     // 内容拆解：情绪点',
    '  "stancePoints": string[],      // 内容拆解：立场点',
    '  "audienceEmotions": string[],  // 内容拆解：受众情绪',
    '  "propagationHooks": string[],  // 内容拆解：传播钩子',
    '  "platformContext": string,     // 内容拆解：平台语境',
    '  "headlineType": string,        // 标题打法判断：最适合的标题打法',
    '  "headlineApproachReason": string, // 标题打法判断：为什么适合这类打法',
    '  "headlineFormula": string[],   // 标题结构公式',
    '  "headlineCandidates": string[], // 3个标题备选，固定输出3条',
    '  "bestHeadline": string,        // 1个最强标题',
    '  "bestHeadlineReason": string,  // 最强标题的理由',
    '  "learnedKnowledge": string[]   // 这次分析沉淀出的标题知识',
    '}',
    '',
    '要求：',
    '- formatName 必须写“头条”',
    '- 不要重新定义【头条】平台格式；平台格式由系统预设，你只分析内容并给出标题策略',
    '- sourceStyle 只描述原素材本身的表达风格',
    '- sourceArticleType 只描述原素材本身的内容类型',
    '- normalizedText 只能做纠错、去噪、断句整理和必要的图文化清洗，不得补充新事实',
    '- keyFacts 只写原文中明确给出的事实、动作、场景、说法，不要补充外部信息',
    '- subjectiveClaims 专门归纳主观感受、效果判断、对比结论、收益暗示等不能当作事实写死的内容',
    '- mustKeep 写后续改写必须保留的关键信息点',
    '- mustAvoid 写后续改写时需要降温、不要照搬或不要扩写的表达',
    '- rewriteAngles 写适合头条图文展开的切入角度',
    '- risks 列出不能随意扩写、需要保守处理、需要避免敏感表达的点',
    '- 如果数据来源不明、时效性无法确认或结论过于绝对，要写入 subjectiveClaims、mustAvoid 或 risks',
    '- coreHighlights、conflictPoints、emotionPoints、stancePoints 必须先做提炼，不能空泛',
    '- audienceEmotions 要判断读者最可能被什么情绪驱动点击，如好奇、愤怒、焦虑、代入、认同、反感、爽感',
    '- propagationHooks 要总结这篇内容最能传播的钩子，比如反差、悬念、结果、冲突、认知颠覆、现实焦虑、身份代入、信息差盲区',
    '- headlineType 只能选择最适合本文的一种主打法，优先考虑：反差型、悬念型、冲突型、结果型、情绪型、认知颠覆型',
    '- headlineApproachReason 要说明为什么这篇内容更适合这种打法',
    '- headlineFormula 要总结出最适合本文的标题结构公式，便于后续继续仿写',
    '- headlineCandidates 必须固定输出 3 条，按吸引力从高到低排序',
    '- headlineCandidates 必须是小众切口、深层逻辑、反向解读、隐秘链条或信息差盲区，绝不允许大路货',
    '- headlineCandidates 必须符合“现象/事件 + 反常识结论 + 冲击”结构，冲突感和爽感要强，但不能脱离原文事实',
    '- headlineCandidates 不要假大空，不要文艺腔，不要标题党过头，必须和正文强相关',
    '- headlineCandidates 可适度加入数字、反差、结果、情绪词，但不要堆砌',
    '- headlineCandidates 每条必须控制在 30 字以内；如果素材无法支撑最新且真实的官方数据，不要为了数字感硬写数字',
    '- bestHeadline 必须从 headlineCandidates 中选出',
    '- bestHeadlineReason 必须分别说明为什么它最可能出点击',
    '- learnedKnowledge 要沉淀这次分析学到的标题方法、标题结构和适用条件',
    '- 使用简体中文',
    '- 不要输出 markdown，不要解释，不要代码块',
    '',
    '原文如下：',
    sourceText,
  ]);
}

function buildToutiaoOutlinePrompt({ transcriptText, analysisRecord }) {
  return joinNonEmptyLines([
    '请基于下面这份中文清洗稿、已设定的【头条】改写要求和原素材分析，提炼一份供今日头条图文改写使用的大纲。',
    '只保留事实、观点、结构、切入角度和表达约束，不要直接写成成稿。',
    '',
    buildAnalysisSummaryBlock(analysisRecord),
    '',
    '必须只输出 JSON 对象，字段固定为：',
    '{',
    '  "topic": string,        // 一句话概括主题',
    '  "corePoints": string[], // 原文必须保留的核心观点',
    '  "facts": string[],      // 原文中可直接使用或保守转述的事实依据',
    '  "angles": string[],     // 适合今日头条图文稿的切入角度',
    '  "constraints": string[] // 改写时必须遵守的内容约束、数据边界、合规要求',
    '}',
    '',
    '注意：',
    '- 【头条】平台格式以上述“已设定的改写要求”为准，不要把原素材的口播、种草、直播话术继续放大成平台格式',
    '- facts 优先保留“可保留事实”和“必须保留”中的信息；对主观判断只能保守转述',
    '- angles 优先吸收“核心看点、冲突点、情绪点、传播钩子、标题打法、标题公式”中的有效信息',
    '- constraints 里要覆盖“必须避免”和“风险约束”中的要求',
    '- constraints 只填内容和合规层面的约束，不要填输出格式要求',
    '- 如果原文中的数据来源不明或时效性无法确认，要在 constraints 中明确“不要写死具体数据”',
    '- 使用简体中文，只输出合法 JSON，不要输出 markdown 代码块，不要解释额外内容',
    '',
    '原文如下：',
    transcriptText,
  ]);
}

function buildToutiaoDraftPrompt({ outline, analysisRecord }) {
  return joinNonEmptyLines([
    '请根据下面这份结构化大纲，并严格按照已设定的【头条】格式，重新生成一篇适合今日头条平台发布的中文图文稿。',
    '这不是简单润色，而是一次深度重构：逻辑重排、语言重写、视角重塑，但必须忠于原始事实边界。',
    '',
    buildAnalysisSummaryBlock(analysisRecord),
    '',
    '输出格式要求：',
    '1. 先输出 3 个标题，每行一个，按吸引力排序，格式为“标题1：……”',
    '2. 标题写完后空一行，再输出正文',
    '3. 正文最后必须另起一行写“——END——”',
    '',
    '写作要求：',
    '- 【头条】平台格式以上述“已设定的改写要求”为准，不要模仿原素材里的口播、直播、种草或过强营销语气',
    '- 标题优先参考“标题打法、标题公式、标题备选、最强标题”的分析结论，确保标题具有今日头条点击感',
    '- 3 个标题都必须是全网独家新视角主题，优先使用小众切口、深层逻辑、反向解读、隐秘链条、信息差盲区',
    '- 3 个标题都必须符合“现象/事件 + 反常识结论 + 冲击”结构，冲突感和爽感要强，但不能夸大失真',
    '- 每个标题含标点在内不超过 30 字；若素材没有最新且真实的官方数据，不要为了标题形式硬写数据',
    '- 内容浅显易懂，符合图文阅读习惯，尽量避免专业术语；如必须涉及专业概念，要用大众能懂的说法解释',
    '- 开头必须用一句话完成黄金 3 秒钩子，直接抛出核心观点并制造好奇',
    '- 正文必须拆成 3 到 5 个大板块，层层推进，形成逻辑闭环',
    '- 正文逻辑严谨，段落过渡自然，信息组织清楚，信息密度高但不堆砌',
    '- 正文字数严格控制在 1600～1700 字之间，不含标题行和“——END——”',
    '- 可以重组结构，但不能编造事实，不能脱离原文核心信息',
    '- 观点可以反常识，但必须有事实或逻辑支撑，不造谣、不博眼球、不夸大',
    '- 对原素材中的主观判断、效果感受、工具对比和收益暗示，只能用“使用感受”“个人判断”“原文提到”等保守方式转述',
    '- 如果要写数据，只能保留原文中已明确给出且可归因为官方口径的数据；无法确认最新性、真实性或来源时，不要写具体数据',
    '- 如果素材里已经包含官方信源或官方口径，要把关键数据自然融入正文，不要单独堆在文末',
    '- 不要直接复用参考材料原句，要用原创表达完成改写，确保个人解读、趋势判断和结构表达明显重写',
    '- 全文不要出现字符“*”',
    '- 使用中国互联网语境；遇到敏感词、违禁词或高风险表述，要用委婉、安全、合规的说法替换',
    '- 结尾必须完成观点收束，并自然引导读者评论互动',
    '- 只输出标题和正文，不要写说明、备注、注释、代码块',
    '',
    `主题：${outline.topic}`,
    `核心观点：${formatList(outline.corePoints)}`,
    `事实依据：${formatList(outline.facts)}`,
    `切入角度：${formatList(outline.angles)}`,
    `表达约束：${formatList(outline.constraints)}`,
  ]);
}

const REWRITE_PROFILES = {
  douyin: {
    id: 'douyin',
    requiresAnalysis: false,
    requiresOutline: false,
    draftFilePrefix: 'douyin',
    defaultAnalysisRecord: {
      formatName: 'douyin',
      formatFeatures: ['短视频口播', '开头钩子明显', '节奏较快'],
      writingLogic: ['快速切入主题', '分段展开观点', '结尾收束'],
      expressionStyle: ['口语化', '节奏感强', '适合口播'],
      articleType: '短视频口播稿',
      summary: '未执行 analysis，直接使用转写清洗版进入大纲提取。',
      audience: '短视频受众',
      platformTone: '抖音口播',
      risks: ['请基于原始转写保守改写，避免补充未被原文支持的事实。'],
      generatedBy: null,
    },
    buildAnalysisPrompt() {
      throw new Error('Douyin rewrite profile does not require analysis');
    },
    buildOutlinePrompt: buildDouyinOutlinePrompt,
    buildDraftPrompt: buildDouyinDraftPrompt,
    buildStageSystemPrompt(stage) {
      if (stage === 'outline' || stage === 'analysis') {
        return '你是一个中文内容策划编辑，只能输出合法 JSON。';
      }
      return '你是一个擅长抖音口播稿写作的中文内容编辑。';
    },
  },
  toutiao: {
    id: 'toutiao',
    requiresAnalysis: true,
    analysisDefinesTargetProfile: false,
    draftFilePrefix: 'toutiao',
    defaultAnalysisRecord: {
      formatName: '头条',
      formatFeatures: ['图文阅读友好', '开头直接抛出主题或悬念', '分段清晰', '信息密度适中'],
      writingLogic: ['开头用钩子点题', '中段分层展开事实与观点', '结尾做收束或提醒'],
      expressionStyle: ['浅显易懂', '中文互联网图文语体', '兼顾信息量与可读性'],
      articleType: '今日头条图文稿',
      summary: 'analysis 未执行，使用默认【头条】格式进入大纲提取。',
      audience: '今日头条普通图文读者',
      platformTone: '今日头条图文',
      sourceStyle: [],
      sourceArticleType: '（待分析）',
      keyFacts: [],
      subjectiveClaims: [],
      mustKeep: [],
      mustAvoid: [],
      rewriteAngles: [],
      coreHighlights: [],
      conflictPoints: [],
      emotionPoints: [],
      stancePoints: [],
      audienceEmotions: [],
      propagationHooks: [],
      platformContext: '今日头条图文语境',
      headlineType: '结果型',
      headlineApproachReason: '默认采用结果导向标题，保证与头条图文点击习惯兼容。',
      headlineFormula: [],
      headlineCandidates: [],
      bestHeadline: '',
      bestHeadlineReason: '',
      learnedKnowledge: [],
      risks: [
        '不得编造原文未支持的事实。',
        '无法确认来源或时效性的数据不要写入正文。',
        '对敏感表达采用委婉、安全、合规说法。',
      ],
      generatedBy: null,
    },
    buildAnalysisPrompt: buildToutiaoAnalysisPrompt,
    buildOutlinePrompt: buildToutiaoOutlinePrompt,
    buildDraftPrompt: buildToutiaoDraftPrompt,
    buildStageSystemPrompt(stage) {
      if (stage === 'analysis') {
        return '你是专业的今日头条爆款标题分析师，只能输出合法 JSON。';
      }
      if (stage === 'outline') {
        return '你是一个擅长中文平台内容策划的编辑，只能输出合法 JSON。';
      }
      return '你是一个擅长今日头条图文改写的中文内容编辑。';
    },
  },
};

export function normalizeRewriteProfileId(profileId, fallbackId = DEFAULT_REWRITE_PROFILE_ID) {
  const normalized = typeof profileId === 'string' ? profileId.trim().toLowerCase() : '';
  if (normalized && REWRITE_PROFILES[normalized]) {
    return normalized;
  }
  return REWRITE_PROFILES[fallbackId] ? fallbackId : DEFAULT_REWRITE_PROFILE_ID;
}

export function getRewriteProfile(profileId, fallbackId = DEFAULT_REWRITE_PROFILE_ID) {
  return REWRITE_PROFILES[normalizeRewriteProfileId(profileId, fallbackId)];
}

export function buildFallbackAnalysisRecord(profileId, overrides = {}) {
  const profile = getRewriteProfile(profileId);
  return {
    ...profile.defaultAnalysisRecord,
    ...overrides,
  };
}
