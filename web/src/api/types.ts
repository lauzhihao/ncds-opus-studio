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

// RW 节点 outputs.drafts 中的一条；与 pipeline_runner._mock_outputs("rw") 对齐。
export interface RwDraft {
  model_id: string;
  label: string;
  draft_relpath: string;
  episode_relpath: string;
}

// ASR 节点 outputs.items 中的一条；与 pipeline_runner._mock_outputs("asr") 对齐。
// 真 worker 接入后由后端 adapter 转译成相同形状。
export interface AsrItem {
  index: number;
  url: string;
  title: string;
  author: string;
  transcript_relpath: string;
  article_relpath: string;
  highlight_relpath: string;
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
  style: {
    font?: string;
    size?: number;
    weight?: number;
    color?: string;
    rotation?: number;
    letterSpacing?: number;
    shadow?: string;
  };
  motion?: { enter: string; duration?: number; delay?: number };
}
