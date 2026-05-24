// SSE hook：订阅 /jobs/{id}/events，把事件流转成响应式 JobState。
// 首条 snapshot 直接覆盖；后续 node_status 增量更新对应节点；job_updated 触发 refetch。

import { useEffect, useRef, useState } from 'react';
import type { JobState, StreamEvent } from '../api/types';
import { api } from '../api/client';

export interface JobStreamState {
  job: JobState | null;
  connected: boolean;
  error: string | null;
}

export function useJobStream(jobId: string | undefined): JobStreamState {
  const [job, setJob] = useState<JobState | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;

    const url = `/jobs/${jobId}/events`;
    const es = new EventSource(url);
    esRef.current = es;

    es.onopen = () => {
      if (!cancelled) setConnected(true);
    };
    es.onerror = () => {
      if (!cancelled) {
        setConnected(false);
        setError('SSE disconnected');
      }
    };
    es.onmessage = (ev) => {
      let parsed: StreamEvent;
      try {
        parsed = JSON.parse(ev.data) as StreamEvent;
      } catch {
        return;
      }
      if (parsed.type === 'snapshot') {
        setJob(parsed.state);
      } else if (parsed.type === 'node_status') {
        setJob((prev) =>
          prev
            ? { ...prev, nodes: { ...prev.nodes, [parsed.node]: parsed.state } }
            : prev,
        );
      } else if (parsed.type === 'job_updated') {
        // 写 episode 后后端发的，重新拉一份全量
        api.getJob(jobId).then((s) => {
          if (!cancelled) setJob(s);
        });
      }
    };

    return () => {
      cancelled = true;
      es.close();
      esRef.current = null;
      setConnected(false);
    };
  }, [jobId]);

  return { job, connected, error };
}
