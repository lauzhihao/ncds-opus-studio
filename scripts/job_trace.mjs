import { appendFile, mkdir } from 'node:fs/promises';
import path from 'node:path';

export function getJobTracePath(workspaceDir, jobId) {
  return path.join(workspaceDir, 'video-jobs', jobId, 'trace.log');
}

export async function appendJobTrace({ workspaceDir, jobId, source, stage, detail }) {
  if (!workspaceDir || !jobId) return;
  const logPath = getJobTracePath(workspaceDir, jobId);
  await mkdir(path.dirname(logPath), { recursive: true });
  const timestamp = new Date().toISOString();
  const body = typeof detail === 'string' ? detail : JSON.stringify(detail, null, 2);
  await appendFile(logPath, `[${timestamp}] [${source}] ${stage}\n${body}\n\n`, 'utf8');
}
