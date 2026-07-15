// 后端 server/routes/pipelines.py 的响应类型镜像。
// 改后端 schema 时同步改这里。

export interface AuthUser {
  id: number;
  provider?: string;
  email: string;
  name: string | null;
  pictureUrl: string | null;
  lastLoginAt: string;
}

export interface AuthMeResponse {
  authRequired: boolean;
  authenticated: boolean;
  user: AuthUser | null;
  providers?: { google?: boolean; apple?: boolean };
}

export interface TaskCreateResponse {
  task_id: string;
  status: string;
}

export interface TaskDetailResponse {
  task_id: string;
  cmd: string;
  status: string;
  result?: { images?: string[]; output_dir?: string } | null;
  error?: string | null;
  artifacts?: { label: string; kind: string; url: string; path: string }[] | null;
}

export type NodeStatus = 'idle' | 'queued' | 'running' | 'done' | 'failed';
export type NodeKind = 'input' | 'command' | 'output';

export interface PipelineNodeDef {
  name: string;
  label: string;
  cmd: string;
  deps: string[];
  out_dir: string;
  description: string;
  kind: NodeKind;
  position: { x: number; y: number };
}

export interface PipelineDef {
  id: string;
  name: string;
  description: string;
  nodes: PipelineNodeDef[];
}

export interface NodeState {
  name: string;
  status: NodeStatus;
  started_at: number | null;
  finished_at: number | null;
  progress: string;
  outputs: Record<string, unknown>;
  error: string | null;
  task_id: string | null;
}

export interface JobState {
  job_id: string;
  pipeline_id: string;
  title: string;
  created_at: number;
  updated_at: number;
  inputs: Record<string, unknown>;
  nodes: Record<string, NodeState>;
  node_positions: Record<string, { x: number; y: number }>;
}

export interface JobSummary {
  job_id: string;
  pipeline_id: string;
  title: string;
  created_at: number;
  updated_at: number;
  // 任一节点 running/queued 时为 true；running_node 是首个执行中节点名
  running?: boolean;
  running_node?: string | null;
  // 各底层节点 status（节点名→status），供作品列表算 agent 级进度灯
  node_status?: Record<string, NodeStatus>;
}

// 柳永质检：ai_taste（AI 味）报告，对齐后端 ai_taste.scan（打回重写后的终判）。
export interface RwQcAiTaste {
  verdict?: 'pass' | 'fail';
  summary?: string;
  density?: unknown[];
}

// 柳永质检：quality_rubric（5 维度质量分），对齐后端 quality_rubric.score。
export interface RwQcRubric {
  available?: boolean;
  skipped?: string;
  judge_model?: string; // 实际执行打分的模型 id（opus/codex/agy/ds）
  total?: number; // 满分 50
  grade?: string;
  dims?: Record<string, number>; // 节奏/真实性/精炼度/直接性/信任度
  issues?: string[];
}

// RW 节点 outputs.drafts 中的一条；与 pipeline_runner._execute_rw 输出对齐。
// 4 模型并行后，失败/不可用的也保留在列表中（status='failed' + reason）。
// 柳永质检闸门产出 qc（AI 味终判）+ qc_rubric（5 维度质量分）。
export interface RwDraft {
  model_id: string;
  label: string;
  status?: 'success' | 'failed';
  reason?: string | null;
  draft_relpath: string | null;
  episode_relpath: string | null;
  qc?: RwQcAiTaste;
  qc_rubric?: RwQcRubric;
}

// LINES 节点 outputs；与 pipeline_runner._mock_outputs("lines") 对齐。
// 产物是单文件 lines.md（人类可读的台词稿），用户在抽屉里编辑后启动 tts。
export interface LinesOutputs {
  lines_relpath: string;
  beats_count: number;
}

// IMAGE 节点 outputs.items[].assets 中的一条；与 _execute_image 前景素材产物对齐。
export interface ImageAssetItem {
  index: number;
  asset_id?: string;
  role?: string;
  prompt: string;
  pos?: { x: number; y: number };
  size?: number;
  motion?: { enter?: string; duration?: number; delay?: number };
  image_relpath: string | null;
  status?: 'queued' | 'running' | 'done' | 'failed' | 'skipped';
  variants?: ImageVariantItem[];
  selected_variant_relpath?: string | null;
  error?: string | null;
}

export interface ImageVariantItem {
  index: number;
  image_relpath: string;
  selected?: boolean;
}

// IMAGE 节点 outputs.items 中的一条；当前语义是一句字幕对应一个 visual shot。
// assets：该 shot 的前景素材产物（白底黑剪影，渲染层 multiply 抠白）。
export interface ImageItem {
  shot_id: string;
  beat_index: number;
  group?: string;
  intent: string;
  layout?: string;
  transition?: string;
  background_relpath?: string | null;
  status?: 'queued' | 'running' | 'done' | 'failed' | 'skipped';
  assets?: ImageAssetItem[];
  error?: string | null;
}

export interface ImageBackgroundItem {
  id: string;
  prompt: string;
  image_relpath: string | null;
  status?: 'queued' | 'running' | 'done' | 'failed' | 'skipped';
  variants?: ImageVariantItem[];
  selected_variant_relpath?: string | null;
  error?: string | null;
}

export type MaterialScope = 'current_job' | 'same_author' | 'same_domain' | 'global';

export interface MaterialItem {
  id: string;
  kind: 'cutout' | 'image' | 'video_frame';
  title?: string;
  preview_url: string;
  object_url?: string;
  source_label?: string;
  source?: {
    platform?: string;
    aweme_id?: string;
    author_id?: string;
    job_id?: string;
  };
  tags?: string[];
  domain?: string | null;
  usage_count?: number;
}

export interface MaterialSearchResponse {
  index_ready: boolean;
  items: MaterialItem[];
  query?: Record<string, unknown>;
  scopes?: { id: MaterialScope; label: string }[];
  todo?: string;
}

// STORYBOARD（分镜）节点 outputs；与 pipeline_runner._execute_storyboard 对齐。
export interface StoryboardOutputs {
  episode_relpath: string;
  shots_count: number;
  assets_count: number;
  groups_count: number;
  beats_count: number;
  background_count?: number;
}

// TTS 节点 outputs.items 中的一条；与 pipeline_runner._mock_outputs("tts") 对齐。
export interface TtsItem {
  index: number;
  zh: string;
  scene: string;
  audio_relpath: string | null;
  // final_preview 整段合成：beat 在整段里的区间（ms）
  audio_start?: number | null;
  audio_end?: number | null;
}

// ASR 节点 outputs.items 中的一条；与 pipeline_runner._execute_asr 输出对齐。
// 现在 asr 只产 transcript（听写稿）+ article（清洗稿）；爆款精华已下放到 rw。
export interface AsrItem {
  index: number;
  url: string;
  title: string;
  author: string;
  transcript_relpath: string;
  article_relpath: string;
  error?: string | null;
}

// 高赞评论（沈括采集，对齐 app ShenkuoComment）。
export interface ShenkuoComment {
  nickname?: string;
  text?: string;
  digg?: number;
  ip?: string;
}

// 沈括采集单条作品产物（ASR 节点 outputs.collected[]，对齐 app ShenkuoEntry / 后端 collect_one entry）。
// 沈括统一走采集后，画布「开始创作」即触发 collect_one：快采出 文案/评论/播放数据/封面，
// 音轨（original/vocals/bgm）与抠图由后台第二趟补（字段后到）。媒体相对路径走 /artifacts/files/。
export interface ShenkuoEntry {
  index?: number;
  url?: string;
  aweme_id?: string;
  desc?: string; // 作品文案/标题
  digg?: number;
  author?: string;
  hashtags?: string[];
  stats?: Record<string, number>; // digg/comment/share/collect（抖音不公开播放量）
  cover?: string; // 封面相对路径
  duration?: number; // 视频时长（秒），缺失/0 -> 前端不渲染时长徽标
  text?: string; // 提取文案（清洗稿，内嵌）
  top_comments?: ShenkuoComment[]; // 高赞评论（>10 赞，按赞排序）
  audio?: Record<string, string>; // original/vocals/bgm 相对路径（后台补）
  frames?: string[];
  cutouts?: string[]; // 抠图相对路径（后台补）
  status?: Record<string, string>; // download/transcribe/audio/cutout/comments -> ok/cached/error:*
  error?: string | null;
}

// 鬼谷子选题种子：一条评论 + 它所属作品的提取文案（前端从沈括面板选出，传给后端逐条锚定）。
export interface GuiguziItem {
  text: string; // 提取文案（背景）
  comment: string; // 高赞评论（选题切入点）
  digg?: number; // 该评论点赞量（仅前端展示用，后端不读）
}

// 鬼谷子单条选题（双模型各产出，锚定一条评论）。
export interface GuiguziTopic {
  title: string;
  angle?: string;
  why?: string;
  potential?: number;
  anchor_comment?: string; // 锚定的评论
  source_model?: 'opus' | 'deepseek' | string;
}

// 单模型一栏产出（双栏对比用）。
export interface GuiguziCandidate {
  topics: GuiguziTopic[];
  error?: string | null; // 该模型失败（如 deepseek key 未设）时的文案，另一栏仍出
}

// 第一步「爆款原因分析」的结构化字段（双模型各产一份，用户可编辑后 2 选 1）。
export interface GuiguziAnalysis {
  hook_reason?: string; // 爆款原因（核心吸引力）
  audience?: string; // 目标受众
  hooks?: string[]; // 可复制钩子
  direction?: string; // 衍生选题建议方向
}

// 单模型一栏的分析产出。
export interface GuiguziAnalysisColumn {
  analysis?: GuiguziAnalysis | null;
  error?: string | null;
}

// 鬼谷子两步流结果（per-job guiguzi.json；前端轮询 GET /jobs/{id}/guiguzi）。
//   analyzing → analyzed（用户介入：编辑分析 + 2选1）→ generating → done
export interface GuiguziResult {
  // 粗粒度轮询信号：running(分析/出题中) / analyzed(分析完待用户) / done / failed
  status: 'running' | 'analyzed' | 'done' | 'failed';
  stage?: 'analyzing' | 'analyzed' | 'generating' | 'done' | 'failed';
  items?: GuiguziItem[];
  analysis?: Record<string, GuiguziAnalysisColumn | undefined> | null; // 第一步多模型
  chosen_analysis?: GuiguziAnalysis | null; // 第二步选定/编辑的那份
  candidates?: Record<string, GuiguziCandidate | undefined> | null; // 第二步多模型选题
  prompt?: string | null; // 本次出选题用的提示词模板（含 $source 占位），供前端回显/编辑
  topics?: GuiguziTopic[] | null;
  error?: string | null;
  progress?: string;
  updated_at?: number;
}

// 前端从抖音分享文本里 regex 解析出的一条作品。
// 桌面 / 手机 / 纯 URL 三种形态都支持，识别不到的字段就 undefined。
export interface ParsedShare {
  url: string;
  originalUrl?: string;
  title?: string;
  author?: string;
  tags: string[];
}

// —— 任务视角导航（长期任务 = 对标账号 → 作品） ——

// 监控的对标账号（沈括订阅配置，GET /subscriptions）。
export interface SubscriptionAuthor {
  sec_uid: string;
  note?: string | null;
  enabled: boolean;
  platform?: string; // 'douyin' | 'tiktok'，默认 douyin
  domain?: string | null; // 领域 profile key（finance/emotion，见 config/domains.ts）
  // 展示快照（新增时由 /accounts/resolve 带入；卡片直接显示）
  nickname?: string | null;
  avatar?: string | null;
  unique_id?: string | null;
  follower_count?: number | null;
  like_count?: number | null;
  works_count?: number | null;
  refreshed_at?: number | null; // 我方最后一次拉取的 unix 秒
}

export interface SubscriptionsConfig {
  interval_hours: number;
  authors: SubscriptionAuthor[];
}

// POST /accounts/resolve 的结果：从抖音/TK 分享链接解析出的账号档案。
export interface AccountResolveResult {
  platform: string; // 'douyin' | 'tiktok'
  sec_uid: string;
  nickname: string;
  unique_id: string; // 抖音号 / TK handle
  avatar: string;
  follower_count: number;
  like_count: number;
  works_count: number;
}

// POST /works/resolve 的结果：从抖音作品分享链接解析出的作品卡（临时任务"智能解析"）。
export interface WorkResolveResult {
  platform: string; // 'douyin'
  aweme_id: string;
  share_url: string;
  title: string; // 作品标题（desc）
  hashtags: string[];
  // 四项互动数据（抖音不公开播放量）
  digg: number;
  comment: number;
  share: number;
  collect: number;
  cover_url: string;
  // 作者档案：「关注ta」据此加入对标订阅
  author: {
    platform: string;
    sec_uid: string;
    nickname: string;
    unique_id: string;
    avatar: string;
  };
  cached: boolean; // true=命中本地缓存（未打 TikHub）
  // 赛道标签：manifest 里已存的（继承来的/之前手选的）；新作品首次解析为 null
  domain: string | null;
}

// 某对标账号的一条作品（GET /accounts/{sec_uid}/posts）。
export interface AccountPost {
  aweme_id: string;
  desc: string;
  digg: number;
  comment: number;
  share: number;
  collect: number;
  create: number;
  cover_url: string;
  duration: number; // 视频时长（秒），0=未知 -> 前端不渲染时长徽标
  share_url: string;
  collected: boolean;
}

// SSE 事件 payload
export type StreamEvent =
  | { type: 'snapshot'; job_id: string; state: JobState }
  | { type: 'node_status'; job_id: string; node: string; state: NodeState }
  | { type: 'job_updated'; job_id: string; state?: JobState };

// 完整 episode.json 结构（贴近 011 schema，前端 form 用）
export interface Episode {
  __schema__?: string;
  meta: {
    slug: string;
    title: string;
    brandTitle: string;
    disclaimer: string;
    titleOptions?: string[];
  };
  fonts?: Array<{ family: string; src: string; weight?: string | number; style?: string; format?: string; display?: string }>;
  visual: {
    palette: string | Record<string, unknown>;
    bandStyle?: string;
    kenBurns?: boolean;
    showSubtitleEn?: boolean;
    capZhSize?: number;
    capEnSize?: number;
    capZhFont?: string;
    capEnFont?: string;
    style?: string;
    stage?: VisualStage;
    shots?: VisualShot[];
  };
  playback: { rate: number };
  audio?: { tts?: Record<string, unknown> };
  image?: Record<string, unknown>;
  beats: Beat[];
  scenes: Record<string, Scene>;
}

export interface Beat {
  zh: string;
  en: string;
  scene: string;
  capEnter?: string;
  chapter?: number | boolean | null;
}

export interface VisualStage {
  background?: {
    prompt?: string;
    imageFit?: 'cover' | 'contain' | 'fill';
    imageFile?: string;
  };
  palette?: Record<string, unknown>;
  shotRhythm?: string;
}

export interface VisualShot {
  beatIndex: number;
  shotId: string;
  group?: string;
  intent: string;
  layout?: string;
  transition?: string;
  motion?: { enter?: string; duration?: number; delay?: number };
  emphasis?: Overlay[];
  assets?: VisualAsset[];
}

export interface VisualAsset {
  id: string;
  role?: string;
  prompt: string;
  pos: { x: number; y: number };
  size: number;
  motion?: { enter?: string; duration?: number; delay?: number };
  imageFile?: string;
  at?: { match: string; delay?: number };
}

export interface Scene {
  prompt: string;
  label?: string;
  group?: string;
  imageFit?: 'cover' | 'contain' | 'fill';
  motion?: { enter: string; duration?: number };
  overlays?: Overlay[];
  // 前景素材层：director agent 设计、IMAGE 节点出图，渲染层叠在全片背景上（multiply 抠白）
  sketches?: Sketch[];
  num?: string;
  type?: string;
  subtitle?: string;
}

// scenes[id].sketches[] 中的一条；与 storyboard_director._norm_sketch 对齐。
export interface Sketch {
  prompt: string;
  pos: { x: number; y: number };
  size: number;
  motion?: { enter: string; duration?: number; delay?: number };
  // 跟台词关键词飞入：beat.zh 含 match 时入场；缺省=子场景切入即显
  at?: { match: string; delay?: number };
}

export interface Overlay {
  text: string;
  pos: { x: number; y: number };
  // overlays.js applyStyleObject 支持的全部字段；缺省时随机走 os-* class
  style: {
    font?: string;
    size?: number;
    weight?: number;
    color?: string;
    rotation?: number;
    letterSpacing?: number;
    shadow?: string;
    padding?: string;
    background?: string;
    border?: string;
    borderRadius?: number;
  };
  motion?: { enter: string; duration?: number; delay?: number };
}
