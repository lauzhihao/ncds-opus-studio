// 极简 fetch 封装：所有路径相对 /，由 Vite 在 dev 时 proxy，prod 走同源 FastAPI。
import type {
  Episode,
  JobState,
  JobSummary,
  ParsedShare,
  PipelineDef,
} from './types';

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`GET ${path} -> ${r.status}: ${await r.text()}`);
  return r.json() as Promise<T>;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`POST ${path} -> ${r.status}: ${await r.text()}`);
  return r.json() as Promise<T>;
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`PUT ${path} -> ${r.status}: ${await r.text()}`);
  return r.json() as Promise<T>;
}

async function del<T>(path: string): Promise<T> {
  const r = await fetch(path, { method: 'DELETE' });
  if (!r.ok) throw new Error(`DELETE ${path} -> ${r.status}: ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const api = {
  listPipelines: () => get<{ pipelines: PipelineDef[] }>('/pipelines'),
  getPipeline: (id: string) => get<PipelineDef>(`/pipelines/${id}`),
  listJobs: () => get<{ jobs: JobSummary[] }>('/jobs'),
  getJob: (id: string) => get<JobState>(`/jobs/${id}`),
  createJob: (body: { pipeline_id: string; title?: string; inputs: Record<string, unknown> }) =>
    post<JobState>('/jobs', body),
  deleteJob: (id: string) => del<{ deleted: string }>(`/jobs/${id}`),
  updateJobTitle: (jobId: string, title: string) =>
    put<{ job_id: string; title: string }>(`/jobs/${jobId}/title`, { title }),
  runNode: (jobId: string, node: string) =>
    post<JobState>(`/jobs/${jobId}/nodes/${node}/run`),
  cancelNode: (jobId: string, node: string) =>
    post<{ cancelled: boolean; job_id: string; node: string }>(
      `/jobs/${jobId}/nodes/${node}/cancel`,
    ),
  rewriteRwModel: (jobId: string, modelId: string) =>
    post<{ ok: boolean; job_id: string; model_id: string }>(
      `/jobs/${jobId}/nodes/rw/rewrite/${modelId}`,
    ),
  selectRwModel: (jobId: string, modelId: string) =>
    put<{ ok: boolean; job_id: string; selected_model_id: string }>(
      `/jobs/${jobId}/nodes/rw/select`,
      { model_id: modelId },
    ),
  writeFile: (jobId: string, relpath: string, text: string) =>
    put<{ ok: boolean; relpath: string; bytes: number }>(
      `/jobs/${jobId}/files/${relpath}`,
      { text },
    ),
  updateInputs: (
    jobId: string,
    body: {
      url?: string;
      urls?: string[];
      raw_text?: string;
      shares?: ParsedShare[];
    },
  ) =>
    put<{ ok: boolean; inputs: Record<string, unknown> }>(
      `/jobs/${jobId}/inputs`,
      body,
    ),
  updateNodePosition: (jobId: string, node: string, x: number, y: number) =>
    put<unknown>(`/jobs/${jobId}/nodes/${node}/position`, { x, y }),
  getEpisode: (jobId: string) => get<Episode>(`/jobs/${jobId}/episode`),
  putEpisode: (jobId: string, ep: Episode) =>
    put<{ ok: boolean }>(`/jobs/${jobId}/episode`, ep),
};
