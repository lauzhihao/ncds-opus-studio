// 鬼谷子（选题）面板 —— v1 薄 gate。
//
// 015 recipe 没有"选题"这一 engine 步：衍生作品的源即素材，鬼谷子在这里做两件事：
//   1) 展示沈括(asr) 产出的源文摘要，帮用户判断切入角度；
//   2) 录入/确认"选题角度"，作为 gate 放行柳永(rw) 出稿。
//
// v1 angle 仅用 localStorage 持久化（不落后端，"引擎不动"）；把 angle 注入柳永 rewrite
// 提示、或接 guiguzi.run 做真选题，留后续（见 docs PRODUCTION-ENGINE-DESIGN §10）。

import { useEffect, useMemo, useState } from 'react';
import { Lightbulb, PenLine } from 'lucide-react';

import { api } from '../../api/client';
import type { AsrItem, JobState } from '../../api/types';
import { angleStorageKey } from '../../config/agents';
import { useToast } from '../Toast';

interface Props {
  jobId: string;
  job: JobState;
  // 确认选题后推进到柳永（由 AgentDrawer 决定"下一步"语义）。
  onConfirmed?: () => void;
}

export function GuiguziPanel({ jobId, job, onConfirmed }: Props) {
  const { showToast } = useToast();
  const [angle, setAngle] = useState('');
  const [running, setRunning] = useState(false);

  const asr = job.nodes.asr;
  const asrDone = asr?.status === 'done';
  const sources = useMemo<AsrItem[]>(() => {
    const items = (asr?.outputs?.items as AsrItem[] | undefined) ?? [];
    return Array.isArray(items) ? items : [];
  }, [asr?.outputs?.items]);

  // 进面板时回填上次的选题角度
  useEffect(() => {
    try {
      const saved = localStorage.getItem(angleStorageKey(jobId));
      if (saved) setAngle(saved);
    } catch {
      /* localStorage 不可用时忽略 */
    }
  }, [jobId]);

  function onAngleChange(v: string) {
    setAngle(v);
    try {
      localStorage.setItem(angleStorageKey(jobId), v);
    } catch {
      /* 忽略持久化失败 */
    }
  }

  async function confirmAndDraft() {
    if (!asrDone) {
      showToast('请先完成沈括的采集与转写');
      return;
    }
    setRunning(true);
    try {
      // 放行柳永出稿：触发底层 rw 节点（v1 不注入 angle，后端不变）。
      await api.runNode(jobId, 'rw');
      onConfirmed?.();
    } catch (e: unknown) {
      showToast('启动出稿失败，请稍后再试');
      console.error('[GuiguziPanel] runNode(rw) 失败', e);
    } finally {
      setRunning(false);
    }
  }

  const rwStarted = ['queued', 'running', 'done'].includes(job.nodes.rw?.status ?? 'idle');

  return (
    <div className="panel guiguzi-panel">
      <div className="panel-section">
        <div className="panel-section-title">
          <Lightbulb size={14} strokeWidth={1.8} /> 源素材
        </div>
        {!asrDone ? (
          <div className="empty-state" style={{ padding: 'var(--s-4)' }}>
            等待沈括完成采集与转写后，这里会列出源作品摘要。
          </div>
        ) : sources.length === 0 ? (
          <div className="empty-state" style={{ padding: 'var(--s-4)' }}>沈括未返回可用素材。</div>
        ) : (
          <ul className="guiguzi-sources">
            {sources.map((s) => (
              <li key={s.index} className="guiguzi-source">
                <span className="guiguzi-source-title">{s.title || `素材 ${s.index + 1}`}</span>
                {s.author && <span className="dim-mono">@{s.author}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="panel-section">
        <div className="panel-section-title">
          <PenLine size={14} strokeWidth={1.8} /> 选题角度
        </div>
        <textarea
          className="guiguzi-angle"
          value={angle}
          onChange={(e) => onAngleChange(e.target.value)}
          placeholder="用一句话写下这条衍生作品的切入角度 / 选题（可选）。例如：聚焦‘普通人也能做到’的反差。"
          rows={3}
          spellCheck={false}
        />
        <div className="dim-mono" style={{ marginTop: 'var(--s-2)', fontSize: 'var(--text-2xs)' }}>
          v1 选题角度仅本地保存，作为出稿前的人工判断记录；自动注入柳永随后续接入。
        </div>
      </div>

      <div className="panel-footer">
        <button
          className="btn primary"
          disabled={!asrDone || running}
          onClick={confirmAndDraft}
          title={asrDone ? '确认选题并让柳永出稿' : '请先完成沈括采集转写'}
        >
          <PenLine size={13} strokeWidth={1.8} />
          {rwStarted ? '已交柳永 · 重新出稿' : running ? '启动中…' : '确认选题 · 让柳永出稿'}
        </button>
      </div>
    </div>
  );
}
