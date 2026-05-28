import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Clock,
  Layers,
  PenBox,
  Plus,
  Search,
  Trash2,
  X,
} from 'lucide-react';

import { api } from '../api/client';
import type { JobSummary, PipelineDef } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { ThemeSwitcher } from '../components/ThemeSwitcher';

export function TemplatesPage() {
  const nav = useNavigate();
  const [pipelines, setPipelines] = useState<PipelineDef[]>([]);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<JobSummary | null>(null);
  // 作品列表前端关键字检索：匹配 title / job_id / pipeline_id，全部小写包含
  const [query, setQuery] = useState('');
  const filteredJobs = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return jobs;
    return jobs.filter((j) =>
      (j.title || '').toLowerCase().includes(q)
      || j.job_id.toLowerCase().includes(q)
      || j.pipeline_id.toLowerCase().includes(q),
    );
  }, [jobs, query]);
  const hasQuery = query.trim().length > 0;

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
    setCreating(pid);
    try {
      const state = await api.createJob({
        pipeline_id: pid,
        title: `作品 ${new Date().toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })}`,
        inputs: { url: '' },
      });
      nav(`/jobs/${state.job_id}`);
    } catch (e: unknown) {
      alert(`创建失败：${(e as Error).message}`);
    } finally {
      setCreating(null);
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    const id = pendingDelete.job_id;
    await api.deleteJob(id);
    setJobs((prev) => prev.filter((j) => j.job_id !== id));
    setPendingDelete(null);
  }

  return (
    <div className="page">
      <div className="topbar">
        <div className="brand">
          <span className="mark">Opus Studio</span>
        </div>
        <div className="spacer" />
        <ThemeSwitcher />
      </div>

      <div className="section-title" style={{ marginTop: 'var(--s-6)' }}>
        <span className="label">模板</span>
        <span className="count">{pipelines.length.toString().padStart(2, '0')}</span>
        <span className="line" />
      </div>
      <div className="tpl-grid">
        {loading
          ? Array.from({ length: 3 }).map((_, i) => <TemplateCardSkeleton key={i} />)
          : pipelines.map((p, idx) => (
              <TemplateCard
                key={p.id}
                pipeline={p}
                index={idx}
                creating={creating === p.id}
                onCreate={() => startFromTemplate(p.id)}
              />
            ))}
      </div>

      <div className="section-title">
        <Clock size={14} strokeWidth={1.6} className="dim" />
        <span className="label">最近作品</span>
        <span className="count">
          {hasQuery
            ? `${filteredJobs.length.toString().padStart(2, '0')}/${jobs.length.toString().padStart(2, '0')}`
            : jobs.length.toString().padStart(2, '0')}
        </span>
        <div className="section-search">
          <Search size={12} strokeWidth={1.7} className="dim" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索作品 / ID"
            spellCheck={false}
            aria-label="搜索作品"
          />
          {hasQuery && (
            <button
              type="button"
              className="section-search-clear"
              onClick={() => setQuery('')}
              title="清空"
              aria-label="清空搜索"
            >
              <X size={11} strokeWidth={1.8} />
            </button>
          )}
        </div>
        <span className="line" />
      </div>
      <div className="tpl-grid">
        {!loading && jobs.length === 0 ? (
          <div className="empty-state">
            还没有作品。从上面挑一个模板，把链接喂进去看看。
          </div>
        ) : !loading && hasQuery && filteredJobs.length === 0 ? (
          <div className="empty-state">
            没有匹配 <strong>{query}</strong> 的作品。
          </div>
        ) : (
          filteredJobs.map((j) => (
            <JobCard
              key={j.job_id}
              job={j}
              onOpen={() => nav(`/jobs/${j.job_id}`)}
              onDelete={() => setPendingDelete(j)}
            />
          ))
        )}
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        title="删除作品？"
        message={
          <>
            将删除 <strong>{pendingDelete?.title || '未命名作品'}</strong>
            （<span className="mono">{pendingDelete?.job_id.slice(0, 8)}</span>），此操作不可恢复。
          </>
        }
        confirmLabel="删除"
        danger
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}

function JobCard({
  job,
  onOpen,
  onDelete,
}: {
  job: JobSummary;
  onOpen: () => void;
  onDelete: () => void;
}) {
  const updated = new Date(job.updated_at * 1000).toLocaleString('zh-CN', { hour12: false });
  // 沿用 TemplateCard 的 marker 算法：从 pipeline_id 抽末尾数字（同模板下作品 marker 一致）
  const marker = (() => {
    const m = /(\d{2,})/.exec(job.pipeline_id);
    return m ? m[1] : job.job_id.slice(0, 2).toUpperCase();
  })();
  return (
    <article className="tpl-card" onClick={onOpen} title="点击进入画布">
      <div className="cover">
        <span className="marker">{marker}</span>
        <span className="badge">{job.pipeline_id}</span>
      </div>
      <div className="body">
        <div className="name">{job.title || '未命名作品'}</div>
        <div className="desc">
          <Clock size={11} strokeWidth={1.6} style={{ verticalAlign: '-2px', marginRight: 4 }} />
          上次更新 {updated}
        </div>
        <div className="footer">
          <span className="meta">
            <Layers size={11} strokeWidth={1.6} style={{ verticalAlign: '-2px', marginRight: 4 }} />
            {job.job_id.slice(0, 8)}
          </span>
          <div style={{ flex: 1 }} />
          <button
            className="btn sm icon-only accent"
            title="进入画布"
            onClick={(e) => {
              e.stopPropagation();
              onOpen();
            }}
          >
            <PenBox size={13} strokeWidth={1.9} />
          </button>
          <button
            className="btn sm icon-only danger"
            title="删除"
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
          >
            <Trash2 size={12} strokeWidth={1.6} />
          </button>
        </div>
      </div>
    </article>
  );
}

// —— 模板卡 ——
function TemplateCard({
  pipeline,
  index,
  creating,
  onCreate,
}: {
  pipeline: PipelineDef;
  index: number;
  creating: boolean;
  onCreate: () => void;
}) {
  // 取模板 id 末尾数字做装饰大字（如 011）；缺失时用序号
  const marker = (() => {
    const m = /(\d{2,})/.exec(pipeline.id);
    return m ? m[1] : (index + 1).toString().padStart(2, '0');
  })();
  return (
    <article className="tpl-card" onClick={onCreate}>
      <div className="cover">
        <span className="marker">{marker}</span>
        <span className="badge">Pipeline · {pipeline.nodes.length}</span>
      </div>
      <div className="body">
        <div className="name">{pipeline.name}</div>
        <div className="desc">{pipeline.description}</div>
        <div className="footer">
          <span className="meta">
            <Layers size={11} strokeWidth={1.6} style={{ verticalAlign: '-2px', marginRight: 4 }} />
            {pipeline.id}
          </span>
          <div style={{ flex: 1 }} />
          <button
            className="btn primary sm"
            disabled={creating}
            onClick={(e) => {
              e.stopPropagation();
              onCreate();
            }}
          >
            <Plus size={12} strokeWidth={1.8} />
            {creating ? '创建中…' : '新建作品'}
          </button>
        </div>
      </div>
    </article>
  );
}

function TemplateCardSkeleton() {
  return (
    <div className="tpl-card" style={{ pointerEvents: 'none', opacity: 0.55 }}>
      <div className="cover" />
      <div className="body">
        <div style={{ height: 18, width: '70%', background: 'var(--bg-overlay)', borderRadius: 4 }} />
        <div style={{ height: 12, width: '90%', background: 'var(--bg-overlay)', borderRadius: 4, marginTop: 8 }} />
        <div style={{ height: 12, width: '60%', background: 'var(--bg-overlay)', borderRadius: 4, marginTop: 4 }} />
      </div>
    </div>
  );
}
