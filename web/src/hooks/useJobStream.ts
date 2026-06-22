// SSE hook：订阅 /jobs/{id}/events，把事件流转成响应式 JobState。
// 首条 snapshot 直接覆盖；后续 node_status 增量更新对应节点；job_updated 触发 refetch。

import { useCallback, useEffect, useRef, useState } from 'react';
import type { JobState, StreamEvent } from '../api/types';
import { api } from '../api/client';

export interface JobStreamState {
  job: JobState | null;
  connected: boolean;
  error: string | null;
  /** 断开当前 SSE 连接并重建，用于"重新执行"等操作前后避免边缘事件冲入造成竞态。 */
  reconnect: () => void;
}

export function useJobStream(jobId: string | undefined): JobStreamState {
  const [job, setJob] = useState<JobState | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const [retry, setRetry] = useState(0);

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
        es.close();
        esRef.current = null;
        setConnected(false);
        setError('SSE disconnected');
        // 触发 retry 自增，useEffect [retry] 重新建立连接
        setRetry((n) => n + 1);
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
  }, [jobId, retry]);

  const reconnect = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
    setConnected(false);
    setRetry((n) => n + 1);
  }, []);

  return { job, connected, error, reconnect };
}
