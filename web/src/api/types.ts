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

// IMAGE 节点 outputs.items 中的一条；与 pipeline_runner._mock_outputs("image") 对齐。
// image_relpath 为 null 时表示未生成（mock 模式或单图重生中）。
export interface ImageItem {
  scene_id: string;
  prompt: string;
  image_relpath: string | null;
}

// TTS 节点 outputs.items 中的一条；与 pipeline_runner._mock_outputs("tts") 对齐。
export interface TtsItem {
  index: number;
  zh: string;
  audio_relpath: string | null;
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
  motion?: { enter: string; duration?: number };
  overlays?: Overlay[];
  num?: string;
  type?: string;
  subtitle?: string;
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
