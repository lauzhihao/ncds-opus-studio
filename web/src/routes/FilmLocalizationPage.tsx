import { useEffect, useState } from 'react';
import { ArrowLeft, Download, Loader2, Play } from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';

import { api } from '../api/client';
import type { JobState, NodeState } from '../api/types';
import { useJobStream } from '../hooks/useJobStream';

const STEP_COPY: Record<string, string> = {
  import: '导入原片',
  analyze: '分析原片',
  localize: '英语本地化',
  voice: '英语配音',
  render: '竖版合成',
};

function mediaUrl(jobId: string, relpath: unknown): string | null {
  if (typeof relpath !== 'string' || !relpath) return null;
  return `/jobs/${encodeURIComponent(jobId)}/files/${relpath.split('/').map(encodeURIComponent).join('/')}`;
}

export function FilmLocalizationPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const nav = useNavigate();
  const { job: streamJob } = useJobStream(jobId);
  const [fallbackJob, setFallbackJob] = useState<JobState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [localization, setLocalization] = useState('');
  const [localizationDirty, setLocalizationDirty] = useState(false);

  const job = streamJob ?? fallbackJob;

  useEffect(() => {
    if (!jobId || streamJob) return;
    api.getJob(jobId).then(setFallbackJob).catch((e: unknown) => setError(String(e)));
  }, [jobId, streamJob]);

  useEffect(() => {
    if (!jobId || job?.nodes.localize?.status !== 'done' || localizationDirty) return;
    fetch(`/jobs/${encodeURIComponent(jobId)}/files/02_localize/localization.json`, { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setLocalization)
      .catch(() => undefined);
  }, [jobId, job?.nodes.localize?.status, localizationDirty]);

  async function runStep(step: string) {
    if (!jobId) return;
    setBusy(true);
    setError(null);
    try {
      const next = await api.runNode(jobId, step);
      setFallbackJob(next);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '启动任务失败');
    } finally {
      setBusy(false);
    }
  }

  async function saveLocalization() {
    if (!jobId || !localizationDirty) return;
    setBusy(true);
    try {
      await api.writeFile(jobId, '02_localize/localization.json', localization);
      setLocalizationDirty(false);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '保存失败');
    } finally {
      setBusy(false);
    }
  }

  if (!jobId) {
    return (
      <main className="film-page">
        <header className="film-header">
          <div>
            <span className="film-kicker">Film localization MVP</span>
            <h1>原片直入 · 英语本地化</h1>
            <p>在 Studio 粘贴原片分享链接，选择“影视”并创建任务。系统会导入该原片，生成英语配音、双语字幕和竖版成片。</p>
          </div>
          <button type="button" className="btn ghost sm" onClick={() => nav('/')}><ArrowLeft size={14} /> 返回 Studio</button>
        </header>
        <section className="film-card">
          <h2>从分享链接创建</h2>
          <p>返回 Studio 后点击“优质作品”，粘贴原片分享链接；解析完成选择“影视”，即可直接创建并导入原片。</p>
          <button type="button" className="btn primary" onClick={() => nav('/')}><ArrowLeft size={15} /> 前往 Studio</button>
        </section>
      </main>
    );
  }

  const sourceInput = job?.inputs.source;
  const sourcePath =
    sourceInput && typeof sourceInput === 'object' && 'path' in sourceInput
      ? (sourceInput as { path?: unknown }).path
      : job?.inputs.source_video;
  const sourceUrl = mediaUrl(jobId, sourcePath);
  const output = job?.nodes.render?.outputs.video_relpath;
  const outputUrl = mediaUrl(jobId, output);
  const nodes = Object.entries(job?.nodes ?? {}).filter(([name]) => STEP_COPY[name]);

  return (
    <main className="film-page">
      <header className="film-header">
        <div>
          <span className="film-kicker">Film localization</span>
          <h1>{job?.title || '影视本地化任务'}</h1>
          <p>中文原片 → 英语解说 → 双语字幕 → 9:16 成片</p>
        </div>
        <button type="button" className="btn ghost sm" onClick={() => nav('/')}><ArrowLeft size={14} /> 返回 Studio</button>
      </header>
      {error && <p className="film-error">{error}</p>}
      <div className="film-grid">
        <section className="film-card">
          <h2>原片与成片</h2>
          {sourceUrl ? <video className="film-preview" src={sourceUrl} controls /> : <p>原片导入完成后会在这里预览。</p>}
          {outputUrl && (
            <>
              <h2 style={{ marginTop: 'var(--s-6)' }}>英语竖版成片</h2>
              <video className="film-preview" src={outputUrl} controls />
              <a className="btn primary" style={{ marginTop: 'var(--s-3)' }} href={outputUrl} download><Download size={15} /> 下载 MP4</a>
            </>
          )}
        </section>
        <section className="film-card">
          <h2>生产步骤</h2>
          <div className="film-steps">
            {nodes.map(([name, node]) => <Step key={name} name={name} node={node} busy={busy} onRun={runStep} />)}
          </div>
        </section>
      </div>
      <section className="film-card" style={{ marginTop: 'var(--s-5)' }}>
        <h2>英语本地化稿</h2>
        <p>完成本地化后可直接编辑 JSON 中的英语稿与字幕，再继续配音。</p>
        <textarea className="film-script" value={localization} onChange={(e) => { setLocalization(e.target.value); setLocalizationDirty(true); }} placeholder="运行“英语本地化”后显示可编辑稿件" />
        <div className="film-actions" style={{ marginTop: 'var(--s-3)' }}>
          <button type="button" className="btn ghost sm" disabled={!localizationDirty || busy} onClick={() => void saveLocalization()}>保存稿件</button>
        </div>
      </section>
    </main>
  );
}

function Step({ name, node, busy, onRun }: { name: string; node: NodeState; busy: boolean; onRun: (step: string) => Promise<void> }) {
  const canRun = node.status === 'idle' || node.status === 'failed';
  return (
    <div className="film-step">
      <span className={`film-step-dot ${node.status}`} />
      <div>
        <span className="film-step-label">{STEP_COPY[name]}</span>
        {(node.progress || node.error) && <span className="film-step-progress">{node.error || node.progress}</span>}
      </div>
      <button type="button" className="btn ghost sm" disabled={!canRun || busy} onClick={() => void onRun(name)}>
        {node.status === 'running' || node.status === 'queued' ? <Loader2 size={14} className="spin" /> : <Play size={14} />} {node.status === 'done' ? '完成' : '执行'}
      </button>
    </div>
  );
}
