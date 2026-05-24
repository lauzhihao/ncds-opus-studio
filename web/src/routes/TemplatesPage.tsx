import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { JobSummary, PipelineDef } from '../api/types';

export function TemplatesPage() {
  const nav = useNavigate();
  const [pipelines, setPipelines] = useState<PipelineDef[]>([]);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.listPipelines(), api.listJobs()])
      .then(([pl, jl]) => {
        setPipelines(pl.pipelines);
        setJobs(jl.jobs);
      })
      .catch((e: unknown) => console.error('load templates page failed', e))
      .finally(() => setLoading(false));
  }, []);

  async function startFromTemplate(pid: string) {
    const url = window.prompt('粘贴抖音视频链接（mock 模式接受任意字符串）');
    if (!url) return;
    setCreating(pid);
    try {
      const state = await api.createJob({
        pipeline_id: pid,
        title: `作品 ${new Date().toLocaleString('zh-CN', { hour12: false })}`,
        inputs: { url },
      });
      nav(`/jobs/${state.job_id}`);
    } catch (e: unknown) {
      alert(`创建失败: ${(e as Error).message}`);
    } finally {
      setCreating(null);
    }
  }

  async function deleteJob(id: string) {
    if (!window.confirm(`确定删除作品 ${id.slice(0, 6)}…?`)) return;
    await api.deleteJob(id);
    setJobs((prev) => prev.filter((j) => j.job_id !== id));
  }

  return (
    <div className="page">
      <div className="topbar">
        <span className="title">NCDS Opus Studio</span>
        <span className="meta">模板化视频工厂 · {pipelines.length} 个模板</span>
      </div>

      <h2 style={{ padding: '20px 20px 0', margin: 0, fontSize: 13, color: 'var(--ink-soft)' }}>
        选择模板新建作品
      </h2>
      <div className="tpl-grid">
        {loading ? <div style={{ color: 'var(--ink-soft)' }}>加载中…</div> : null}
        {pipelines.map((p) => (
          <div key={p.id} className="tpl-card" onClick={() => startFromTemplate(p.id)}>
            <div className="cover">{p.name}</div>
            <div className="body">
              <div className="name">{p.name}</div>
              <div className="desc">{p.description}</div>
              <div className="footer">
                <button
                  className="btn primary sm"
                  disabled={creating === p.id}
                  onClick={(e) => {
                    e.stopPropagation();
                    startFromTemplate(p.id);
                  }}
                >
                  {creating === p.id ? '创建中…' : '+ 新建作品'}
                </button>
                <span style={{ color: 'var(--ink-soft)', fontSize: 11, alignSelf: 'center' }}>
                  {p.nodes.length} 节点
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="jobs-section">
        <h2>最近作品 · {jobs.length}</h2>
        {jobs.length === 0 && !loading && (
          <div style={{ color: 'var(--ink-soft)', fontSize: 13 }}>还没有作品，从上面的模板开个新作品吧。</div>
        )}
        <div className="job-list">
          {jobs.map((j) => (
            <div key={j.job_id} className="job-row">
              <div className="title">{j.title || '未命名'}</div>
              <div className="time">
                {new Date(j.updated_at * 1000).toLocaleString('zh-CN', { hour12: false })}
              </div>
              <button className="btn sm" onClick={() => nav(`/jobs/${j.job_id}`)}>
                打开
              </button>
              <button className="btn sm danger" onClick={() => deleteJob(j.job_id)}>
                删除
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
