// 鬼谷子（选题）面板 —— 两步流·评论驱动多模型。
//
// 沈括(asr) 完成后，用户在沈括面板点选 1-5 条高赞评论作选题参考（存 localStorage）。本面板：
//   A. 已选评论 +「分析爆款原因」按钮 → 第一步 analyze（多模型并行反推爆款原因，不固定赛道）。
//   B. 多栏展示各模型的结构化分析，用户**可编辑** + 每栏「用这份分析出选题」→ 第二步 generate。
//   C. 多栏展示选题，hover 选定一个（N 选 1）→ 放行柳永(rw) 出稿。
// 评论变化时支持增量「更新选题」/ 全量「重新选题」；改分析则回 A 点「重新分析」。
// 不可用的模型（如 opus 订阅过期）自动隐藏，不展示也不显示错误。

import { useEffect, useLayoutEffect, useRef, useState, type ComponentProps } from 'react';
import { FileText, Heart, Lightbulb, MessageCircle, PenLine, Search, Sparkles } from 'lucide-react';

import { api } from '../../api/client';
import type {
  GuiguziAnalysis,
  GuiguziAnalysisColumn,
  GuiguziCandidate,
  GuiguziItem,
  GuiguziResult,
  GuiguziTopic,
  JobState,
} from '../../api/types';
import { guiguziChosenStorageKey, guiguziItemsStorageKey } from '../../config/agents';
import { GlobalLoading } from '../GlobalLoading';
import { useToast } from '../Toast';

interface Props {
  jobId: string;
  job: JobState;
  onConfirmed?: () => void;
  onGotoShenkuo?: () => void;
}

// 模型元信息：name → label
const MODEL_META: Record<string, string> = {
  opus: 'Opus 4.8',
  deepseek: 'DeepSeek',
  agy: 'AGY',
};
const ALL_MODELS = Object.keys(MODEL_META);

function diggCount(n: number): string {
  if (n >= 1e8) return `${(n / 1e8).toFixed(1)}亿`;
  if (n >= 1e4) return `${(n / 1e4).toFixed(1)}w`;
  return String(n);
}

function AutoTextarea({
  value,
  fitKey,
  ...rest
}: ComponentProps<'textarea'> & { value: string; fitKey?: unknown }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight + (fitKey === undefined ? 0 : 6)}px`;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, fitKey === undefined ? [value] : [fitKey]);
  return <textarea ref={ref} value={value} {...rest} />;
}

function readItems(jobId: string): GuiguziItem[] {
  try {
    const raw = localStorage.getItem(guiguziItemsStorageKey(jobId));
    return raw ? (JSON.parse(raw) as GuiguziItem[]) : [];
  } catch {
    return [];
  }
}

export function GuiguziPanel({ jobId, job, onConfirmed, onGotoShenkuo }: Props) {
  const { showToast } = useToast();
  const [items, setItems] = useState<GuiguziItem[]>(() => readItems(jobId));
  const [result, setResult] = useState<GuiguziResult | null>(null);
  const [busyAction, setBusyAction] = useState(false);
  const [chosenTitle, setChosenTitle] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, GuiguziAnalysis>>({});
  const [activeAnalysisTab, setActiveAnalysisTab] = useState<string>('');

  useLayoutEffect(() => {
    document.querySelectorAll<HTMLTextAreaElement>('.guiguzi-cols .gg-field-input').forEach(el => {
      el.style.height = 'auto';
      el.style.height = `${el.scrollHeight}px`;
    });
  }, [activeAnalysisTab]);

  const [promptEdit, setPromptEdit] = useState<string>('');
  const [promptSyncedAt, setPromptSyncedAt] = useState<number | undefined>(undefined);

  const asrDone = job.nodes.asr?.status === 'done';
  const stage = result?.stage;
  const running = result?.status === 'running';
  const analyzed = result?.status === 'analyzed';
  const done = result?.status === 'done';
  const busy = busyAction || running;

  const analysisMap = result?.analysis as Record<string, GuiguziAnalysisColumn | undefined> | undefined;
  const candidatesMap = result?.candidates as Record<string, GuiguziCandidate | undefined> | undefined;

  // 可用模型列表（无 error 的）
  const availAnalysis = analyzed
    ? ALL_MODELS.filter((m) => analysisMap?.[m] && !analysisMap[m]?.error)
    : [];
  const availTopics = done
    ? ALL_MODELS.filter((m) => candidatesMap?.[m] && !candidatesMap[m]?.error)
    : [];

  // 默认选中第一个可用模型
  if (analyzed && availAnalysis.length > 0 && !availAnalysis.includes(activeAnalysisTab)) {
    // 在 render 期同步更新（非 useEffect），避免 tab 切到不可用模型
    if (activeAnalysisTab !== availAnalysis[0]) {
      // eslint-disable-next-line react-compiler/react-internal/no-unused-state
      setActiveAnalysisTab(availAnalysis[0]);
    }
  }

  useEffect(() => {
    setItems(readItems(jobId));
    try {
      const raw = localStorage.getItem(guiguziChosenStorageKey(jobId));
      setChosenTitle(raw ? (JSON.parse(raw) as GuiguziTopic).title ?? null : null);
    } catch {
      setChosenTitle(null);
    }
    api.getGuiguzi(jobId).then(setResult).catch(() => setResult(null));
  }, [jobId]);

  // running 期间轮询（每 2s），兜底 SSE 断连/丢事件场景
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (!running) {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }
    const tick = () => {
      api.getGuiguzi(jobId).then((d) => {
        if (d.status !== 'running' && pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
        setResult(d);
      }).catch(() => {});
    };
    tick();
    pollRef.current = setInterval(tick, 2000);
    return () => {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    };
  }, [running, jobId]);

  useEffect(() => {
    if (analyzed && result?.analysis) {
      const next: Record<string, GuiguziAnalysis> = {};
      for (const m of ALL_MODELS) {
        const a = analysisMap?.[m]?.analysis;
        if (a) next[m] = { ...a };
      }
      setEdits(next);
    }
  }, [analyzed, result?.updated_at]); // eslint-disable-line react-hooks/exhaustive-deps

  if (done && result?.prompt != null && result.updated_at !== promptSyncedAt) {
    setPromptSyncedAt(result.updated_at);
    setPromptEdit(result.prompt);
  }

  async function analyze() {
    if (!asrDone) {
      showToast('请先让沈括完成采集');
      return;
    }
    setBusyAction(true);
    try {
      setResult(await api.analyzeGuiguzi(jobId, items));
    } catch (e) {
      showToast('分析失败，请稍后再试');
      console.error('[GuiguziPanel] analyzeGuiguzi 失败', e);
    } finally {
      setBusyAction(false);
    }
  }

  async function generate(analysis: GuiguziAnalysis, opts: { force?: boolean; prompt?: string } = {}) {
    setBusyAction(true);
    setResult((prev) =>
      prev ? { ...prev, status: 'running', stage: 'generating', candidates: null, topics: null, progress: '出题中…' } : prev,
    );
    try {
      setResult(await api.generateGuiguzi(jobId, items, analysis, opts));
    } catch (e) {
      showToast('出选题失败，请稍后再试');
      console.error('[GuiguziPanel] generateGuiguzi 失败', e);
    } finally {
      setBusyAction(false);
    }
  }

  async function chooseTopic(t: GuiguziTopic) {
    if (!asrDone) {
      showToast('请先完成沈括的采集与转写');
      return;
    }
    setChosenTitle(t.title);
    try {
      localStorage.setItem(guiguziChosenStorageKey(jobId), JSON.stringify(t));
    } catch { /* ignore */ }
    onConfirmed?.();
    try {
      await api.runNode(jobId, 'rw', undefined, true);
    } catch (e) {
      showToast('启动出稿失败，请稍后再试');
      console.error('[GuiguziPanel] runNode(rw) 失败', e);
    }
  }

  const hasAnalysis = !!(
    result?.analysis &&
    ALL_MODELS.some((m) => analysisMap?.[m]?.analysis)
  );
  const hasTopics =
    done &&
    ALL_MODELS.some((m) => (candidatesMap?.[m]?.topics?.length ?? 0) > 0);

  const coveredComments = new Set<string>();
  if (done) {
    for (const m of availTopics) {
      for (const t of candidatesMap?.[m]?.topics ?? []) {
        if (t.anchor_comment) coveredComments.add(t.anchor_comment);
      }
    }
  }
  const currentComments = items.map((it) => it.comment);
  const newCount = currentComments.filter((c) => !coveredComments.has(c)).length;
  const removedCount = [...coveredComments].filter((c) => !currentComments.includes(c)).length;
  const changed = items.length > 0 && hasTopics && (newCount > 0 || removedCount > 0);

  const analyzeButton = (
    <button
      className="btn primary sm"
      disabled={!asrDone || busy}
      onClick={analyze}
      title="从原文(+评论)反推爆款原因（不固定赛道）"
    >
      <Search size={13} strokeWidth={1.8} />
      {stage === 'analyzing'
        ? '分析中…'
        : hasAnalysis
          ? '重新分析'
          : items.length > 0
            ? '分析爆款原因'
            : '拆解爆款原因'}
    </button>
  );

  return (
    <div className="panel guiguzi-panel">
      <div className="panel-section">
        <div className="panel-section-title" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <MessageCircle size={14} strokeWidth={1.8} />
          <span style={{ flex: 1 }}>选题参考 · 已选 {items.length}/5 条评论</span>
          {items.length > 0 && analyzeButton}
        </div>
        {!asrDone ? (
          <div className="empty-state" style={{ padding: 'var(--s-4)' }}>
            还没有可选的评论。先让{' '}
            <button type="button" className="link-btn" onClick={onGotoShenkuo}>沈括</button>
            {' '}去采集些基础数据（文案 / 高赞评论），再回来找我选题。
          </div>
        ) : items.length === 0 ? (
          <div className="empty-state" style={{ padding: 'var(--s-4)' }}>
            <div>
              建议从{' '}
              <button type="button" className="link-btn" onClick={onGotoShenkuo}>沈括</button>
              {' '}处选择高赞评论，再进行选题。
            </div>
            <div style={{ marginTop: 'var(--s-3)', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span>或者您也可以</span>
              {analyzeButton}
            </div>
          </div>
        ) : (
          <ul className="guiguzi-seed-list">
            {items.map((it, i) => (
              <li key={i} className="guiguzi-seed">
                <span className="guiguzi-seed-idx">{i + 1}</span>
                <span className="guiguzi-seed-text">{it.comment}</span>
                {it.digg != null && (
                  <span className="guiguzi-seed-digg">
                    <Heart size={10} strokeWidth={2} /> {diggCount(it.digg)}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {running && (
        <div className="panel-section">
          <div className="gg-loading">
            <GlobalLoading size={48} coreColor="var(--bg-surface)" />
            <div className="dim-mono">
              {result?.progress ||
                (stage === 'analyzing' ? '多模型并行分析爆款原因中…' : '多模型并行出题中…')}
            </div>
          </div>
        </div>
      )}

      {analyzed && availAnalysis.length > 0 && (
        <div className="panel-section">
          <div className="panel-section-title">
            <Lightbulb size={14} strokeWidth={1.8} /> 爆款原因 · 改一改、选一个出选题
          </div>
          <div className="gg-tabs">
            {availAnalysis.map((m) => (
              <button
                key={m}
                className={`gg-tab${activeAnalysisTab === m ? ' active' : ''}`}
                onClick={() => setActiveAnalysisTab(m)}
              >
                {MODEL_META[m] || m}
              </button>
            ))}
          </div>
          <div className={`guiguzi-cols cols-${availAnalysis.length} tab-active-${activeAnalysisTab}`}>
            {availAnalysis.map((m) => (
              <AnalysisColumn
                key={m}
                name={m}
                label={MODEL_META[m] || m}
                value={edits[m] ?? {}}
                onChange={(a) => setEdits((p) => ({ ...p, [m]: a }))}
                onUse={() => generate(edits[m] ?? {}, { force: true })}
                disabled={busy}
              />
            ))}
          </div>
        </div>
      )}

      {analyzed && availAnalysis.length === 0 && (
        <div className="panel-section">
          <div className="empty-state" style={{ padding: 'var(--s-4)', color: 'var(--danger, #e5484d)' }}>
            所有模型均不可用，无法分析。
          </div>
        </div>
      )}

      {done && availTopics.length > 0 && (
        <div className="panel-section">
          <div className="panel-section-title" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <PenLine size={14} strokeWidth={1.8} />
            <span style={{ flex: 1 }}>选题产出 · 点选一个交柳永</span>
            {hasTopics && (
              <button
                className="btn ghost sm"
                disabled={busy}
                onClick={() => generate(result?.chosen_analysis ?? {}, { force: true, prompt: promptEdit })}
                title="用下方提示词，全部评论重出（含已出过题的）"
              >
                重新选题
              </button>
            )}
          </div>
          {result?.prompt != null && (
            <details className="gg-prompt" open>
              <summary>
                <FileText size={12} strokeWidth={1.8} /> 选题提示词（$source = 原文占位，可改）
              </summary>
              <AutoTextarea
                className="gg-prompt-input"
                value={promptEdit}
                fitKey={result?.updated_at}
                onChange={(e) => setPromptEdit(e.target.value)}
                spellCheck={false}
              />
            </details>
          )}
          {changed && (
            <div className="panel-hint panel-hint-info" style={{ marginBottom: 'var(--s-3)' }}>
              评论选择已变化
              {newCount > 0 && `，新增 ${newCount} 条`}
              {removedCount > 0 && `，移除 ${removedCount} 条`}
              。
              <button
                className="link-btn"
                disabled={busy}
                onClick={() => generate(result?.chosen_analysis ?? {}, {})}
              >
                更新选题{newCount > 0 ? ` +${newCount}` : ''}
              </button>
              （只为新评论补题，已出题的保留）；或上方「重新选题」全部重出。
            </div>
          )}
          <div className={`guiguzi-cols cols-${availTopics.length}`}>
            {availTopics.map((m) => (
              <ModelColumn
                key={m}
                name={m}
                label={MODEL_META[m] || m}
                cand={candidatesMap?.[m]}
                chosenTitle={chosenTitle}
                onChoose={chooseTopic}
              />
            ))}
          </div>
        </div>
      )}

      {done && availTopics.length === 0 && (
        <div className="panel-section">
          <div className="empty-state" style={{ padding: 'var(--s-4)', color: 'var(--danger, #e5484d)' }}>
            所有模型均不可用，无法出选题。
          </div>
        </div>
      )}

      {result?.status === 'failed' && (
        <div className="panel-section">
          <div className="empty-state" style={{ padding: 'var(--s-4)', color: 'var(--danger, #e5484d)' }}>
            失败：{result.error || '未知错误'}
          </div>
        </div>
      )}
    </div>
  );
}

// —— 第一步：单模型分析栏（结构化字段，可编辑）+「用这份分析出选题」——
function AnalysisColumn({
  name,
  label,
  value,
  onChange,
  onUse,
  disabled,
}: {
  name: string;
  label: string;
  value: GuiguziAnalysis;
  onChange: (a: GuiguziAnalysis) => void;
  onUse: () => void;
  disabled: boolean;
}) {
  const set = (patch: Partial<GuiguziAnalysis>) => onChange({ ...value, ...patch });
  return (
    <div className={`guiguzi-col model-${name}`}>
      <div className="guiguzi-col-head">{label}</div>
      <label className="gg-field">
        <span className="gg-field-tag tag-title">爆款原因</span>
        <AutoTextarea
          className="gg-field-input"
          value={value.hook_reason ?? ''}
          onChange={(e) => set({ hook_reason: e.target.value })}
        />
      </label>
      <label className="gg-field">
        <span className="gg-field-tag tag-why">目标受众</span>
        <AutoTextarea
          className="gg-field-input"
          value={value.audience ?? ''}
          onChange={(e) => set({ audience: e.target.value })}
        />
      </label>
      <label className="gg-field">
        <span className="gg-field-tag tag-angle">可复制钩子</span>
        <AutoTextarea
          className="gg-field-input"
          value={(value.hooks ?? []).join('\n')}
          onChange={(e) => set({ hooks: e.target.value.split('\n').map((s) => s.trim()).filter(Boolean) })}
        />
      </label>
      <label className="gg-field">
        <span className="gg-field-tag tag-dir">建议方向</span>
        <AutoTextarea
          className="gg-field-input"
          value={value.direction ?? ''}
          onChange={(e) => set({ direction: e.target.value })}
        />
      </label>
      <button className="btn primary sm gg-use-btn" disabled={disabled} onClick={onUse}>
        <Sparkles size={13} strokeWidth={1.8} /> 用这份分析出选题
      </button>
    </div>
  );
}

// —— 第二步：单模型选题栏；每个选题 hover 选定（N 选 1）→ 交柳永。
function ModelColumn({
  name,
  label,
  cand,
  chosenTitle,
  onChoose,
}: {
  name: string;
  label: string;
  cand?: GuiguziCandidate;
  chosenTitle: string | null;
  onChoose: (t: GuiguziTopic) => void;
}) {
  const topics = [...(cand?.topics ?? [])].sort((a, b) => (b.potential ?? 0) - (a.potential ?? 0));
  return (
    <div className={`guiguzi-col model-${name}`}>
      <div className="guiguzi-col-head">{label}</div>
      {topics.length === 0 ? (
        <div className="dim-mono" style={{ padding: 'var(--s-2)' }}>无产出</div>
      ) : (
        topics.map((t, i) => {
          const chosen = chosenTitle != null && t.title === chosenTitle;
          return (
            <div
              key={i}
              className={`guiguzi-topic${chosen ? ' chosen' : ''}`}
              role="button"
              tabIndex={0}
              onClick={() => onChoose(t)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onChoose(t);
                }
              }}
            >
              <div className="guiguzi-topic-bar">
                <span className="guiguzi-topic-no">备选 {i + 1}</span>
                {t.potential != null && <span className="guiguzi-topic-pot">潜力 {t.potential} 分</span>}
              </div>
              {t.anchor_comment && <blockquote className="guiguzi-topic-quote">{t.anchor_comment}</blockquote>}
              <div className="gg-line">
                <span className="gg-tag tag-title">标题</span>
                <span className="gg-line-text strong">{t.title}</span>
              </div>
              {t.why && (
                <div className="gg-line">
                  <span className="gg-tag tag-why">理由</span>
                  <span className="gg-line-text">{t.why}</span>
                </div>
              )}
              {t.angle && (
                <div className="gg-line">
                  <span className="gg-tag tag-angle">角度</span>
                  <span className="gg-line-text dim">{t.angle}</span>
                </div>
              )}
              <div className="guiguzi-topic-overlay">
                <PenLine size={14} strokeWidth={2} />
                {chosen ? '重新提交' : '选这个'}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}
