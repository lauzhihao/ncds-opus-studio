import { angleStorageKey, guiguziChosenStorageKey, guiguziItemsStorageKey } from '../config/agents';

export const STUDIO_MOCK_JOB_ID = '36aacfec847e';

export function isStudioMockMode(params: URLSearchParams): boolean {
  return params.get('m') === '1' || params.get('mock') === '1';
}

export function withMockQuery(path: string, enabled: boolean): string {
  if (!enabled) return path;
  return `${path}${path.includes('?') ? '&' : '?'}m=1`;
}

export function clearMockJobClientState(jobId: string): void {
  try {
    localStorage.removeItem(angleStorageKey(jobId));
    localStorage.removeItem(guiguziItemsStorageKey(jobId));
    localStorage.removeItem(guiguziChosenStorageKey(jobId));
    localStorage.removeItem(`nof:agentpos:v3:${jobId}`);
    localStorage.removeItem(`nof:agentpos:v2:${jobId}`);
    localStorage.removeItem(`nof:agentpos:${jobId}`);
  } catch {
    /* ignore */
  }
}
