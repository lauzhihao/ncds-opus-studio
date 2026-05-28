// 后端 server/routes/pipelines.py 的响应类型镜像。
// 改后端 schema 时同步改这里。

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
}

// RW 节点 outputs.drafts 中的一条；与 pipeline_runner._execute_rw 输出对齐。
// 4 模型并行后，失败/不可用的也保留在列表中（status='failed' + reason）。
export interface RwDraft {
  model_id: string;
  label: string;
  status?: 'success' | 'failed';
  reason?: string | null;
  draft_relpath: string | null;
  episode_relpath: string | null;
}

// LINES 节点 outputs；与 pipeline_runner._mock_outputs("lines") 对齐。
// 产物是单文件 lines.md（人类可读的台词稿），用户在抽屉里编辑后启动 tts。
export interface LinesOutputs {
  lines_relpath: string;
  beats_count: number;
}

// IMAGE 节点 outputs.items[].sketches 中的一条；与 _execute_image 简笔画产物对齐。
export interface ImageSketchItem {
  index: number;
  prompt: string;
  image_relpath: string | null;
  error?: string | null;
}

// IMAGE 节点 outputs.items 中的一条；与 pipeline_runner._execute_image 对齐。
// image_relpath 为 null 时表示未生成（mock 模式或单图重生中）。
// sketches：该 scene 的简笔画层产物（白底黑剪影，渲染层 multiply 抠白）。
export interface ImageItem {
  scene_id: string;
  prompt: string;
  image_relpath: string | null;
  sketches?: ImageSketchItem[];
}

// STORYBOARD（分镜）节点 outputs；与 pipeline_runner._execute_storyboard 对齐。
export interface StoryboardOutputs {
  episode_relpath: string;
  scenes_count: number;
  sketches_count: number;
  groups_count: number;
  beats_count: number;
}

// TTS 节点 outputs.items 中的一条；与 pipeline_runner._mock_outputs("tts") 对齐。
export interface TtsItem {
  index: number;
  zh: string;
  scene: string;
  audio_relpath: string | null;
  // 015 整段合成：beat 在整段里的区间（ms）
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

// 前端从抖音分享文本里 regex 解析出的一条作品。
// 桌面 / 手机 / 纯 URL 三种形态都支持，识别不到的字段就 undefined。
export interface ParsedShare {
  url: string;
  originalUrl?: string;
  title?: string;
  author?: string;
  tags: string[];
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
    palette: string;
    bandStyle: string;
    kenBurns: boolean;
    showSubtitleEn: boolean;
    capZhSize: number;
    capEnSize: number;
    capZhFont?: string;
    capEnFont?: string;
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
  chapter?: boolean;
}

export interface Scene {
  prompt: string;
  label?: string;
  group?: string;
  imageFit?: 'cover' | 'contain' | 'fill';
  motion?: { enter: string; duration?: number };
  overlays?: Overlay[];
  // 简笔画层：director agent 设计、IMAGE 节点出图，渲染层叠在容器图上（multiply 抠白）
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
