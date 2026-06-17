// 鬼谷子（选题）面板 —— 两步流·评论驱动双模型。
//
// 沈括(asr) 完成后，用户在沈括面板点选 1-5 条高赞评论作选题参考（存 localStorage）。本面板：
//   A. 已选评论 +「分析爆款原因」按钮 → 第一步 analyze（双模型并行反推爆款原因，不固定赛道）。
//   B. 双栏展示两模型的结构化分析，用户**可编辑** + 每栏「用这份分析出选题」→ 第二步 generate。
//   C. 双栏展示选题，hover 选定一个（N 选 1）→ 放行柳永(rw) 出稿。
// 评论变化时支持增量「更新选题」/ 全量「重新选题」；改分析则回 A 点「重新分析」。

import { useEffect, useLayoutEffect, useRef, useState, type ComponentProps } from 'react';
import { FileText, Heart, Lightbulb, Loader2, MessageCircle, PenLine, Search, Sparkles } from 'lucide-react';

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
import { useToast } from '../Toast';

interface Props {
  jobId: string;
  job: JobState;
  onConfirmed?: () => void; // 确认选题后推进到柳永
  onGotoShenkuo?: () => void; // 空状态里「沈括」链接：切换到沈括面板去采集/选评论
}

// 抖音口径点赞数：<1w 原样，<1亿 用 w，≥1亿 用 亿（与沈括面板一致）。
function diggCount(n: number): string {
  if (n >= 1e8) return `${(n / 1e8).toFixed(1)}亿`;
  if (n >= 1e4) return `${(n / 1e4).toFixed(1)}w`;
  return String(n);
}

// 自动贴合内容高度的 textarea。
//   - 不传 fitKey：每次输入都重算高度（随文字增长，永不滚动）——给分析字段用。
//   - 传 fitKey：只在 fitKey 变化时（如提示词重新加载）按内容贴合一次；之后高度固定，
//     用户编辑变长则框内滚动（配合 CSS overflow-y:auto）——给选题提示词用。
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
    // +6 缓冲：border-top(1px) + 子像素取整，避免恰好贴合时仍冒出滚动条
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
  const [busyAction, setBusyAction] = useState(false); // 提交中（提交后由轮询接管）
  const [chosenTitle, setChosenTitle] = useState<string | null>(null);
  // 第一步分析的可编辑副本（用户改完再 2 选 1 出选题）。
  const [edits, setEdits] = useState<{ opus?: GuiguziAnalysis; deepseek?: GuiguziAnalysis }>({});
  const [activeAnalysisTab, setActiveAnalysisTab] = useState<'opus' | 'deepseek'>('opus');
  // 切换 tab 时新显列的 AutoTextarea 曾是 display:none，scrollHeight=0 导致高度未算。
  // useLayoutEffect 在 DOM 更新后立即重算，确保切入时高度贴合内容。
  useLayoutEffect(() => {
    document.querySelectorAll<HTMLTextAreaElement>('.guiguzi-cols .gg-field-input').forEach(el => {
      el.style.height = 'auto';
      el.style.height = `${el.scrollHeight}px`;
    });
  }, [activeAnalysisTab]);
  // 第二步选题提示词的可编辑副本（含 $source 占位；用户改完点重新选题就用它）。
  const [promptEdit, setPromptEdit] = useState<string>('');
  const [promptSyncedAt, setPromptSyncedAt] = useState<number | undefined>(undefined);

  const asrDone = job.nodes.asr?.status === 'done';
  const stage = result?.stage;
  const running = result?.status === 'running';
  const analyzed = result?.status === 'analyzed';
  const done = result?.status === 'done';
  const busy = busyAction || running;

  // 进面板：回填已选评论 + 已选定选题 + 拉一次已有结果。
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

  // running（分析/出题中）时轮询，直到 analyzed/done/failed。
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(async () => {
      try {
        setResult(await api.getGuiguzi(jobId));
      } catch {
        /* 暂时读不到忽略，下个 tick 再试 */
      }
    }, 2000);
    return () => window.clearInterval(t);
  }, [running, jobId]);

  // 分析完成 → 用最新分析初始化可编辑副本（每次新分析到达都重置）。
  useEffect(() => {
    if (analyzed && result?.analysis) {
      setEdits({
        opus: { ...(result.analysis.opus?.analysis ?? {}) },
        deepseek: { ...(result.analysis.deepseek?.analysis ?? {}) },
      });
    }
  }, [analyzed, result?.updated_at]); // eslint-disable-line react-hooks/exhaustive-deps

  // 出选题完成 → 用本次提示词模板初始化可编辑副本。render 期同步（而非 useEffect）：
  // 否则 AutoTextarea 的 layout effect 先于 passive effect，贴合时拿到的是旧/空值，box 变矮。
  if (done && result?.prompt != null && result.updated_at !== promptSyncedAt) {
    setPromptSyncedAt(result.updated_at);
    setPromptEdit(result.prompt);
  }

  async function analyze() {
    if (!asrDone) {
      showToast('请先让沈括完成采集');
      return;
    }
    // items 为空也放行：后端「直接拆解」会改用沈括采集的全部原文分析（不强制选评论）。
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
    // items 为空也放行：无评论时后端用沈括原文 + 分析自由出题（不逐条锚定评论）。
    setBusyAction(true);
    // 乐观清空：点击即进入 generating 态、清掉下面的选题结果，不等网络往返。
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

  // N 选 1：选定一个选题 → 交柳永出稿。
  async function chooseTopic(t: GuiguziTopic) {
    if (!asrDone) {
      showToast('请先完成沈括的采集与转写');
      return;
    }
    setChosenTitle(t.title);
    try {
      localStorage.setItem(guiguziChosenStorageKey(jobId), JSON.stringify(t));
    } catch {
      /* 忽略 */
    }
    try {
      await api.runNode(jobId, 'rw');
      onConfirmed?.();
    } catch (e) {
      showToast('启动出稿失败，请稍后再试');
      console.error('[GuiguziPanel] runNode(rw) 失败', e);
    }
  }

  const hasAnalysis = !!(
    result?.analysis &&
    (result.analysis.opus?.analysis || result.analysis.deepseek?.analysis)
  );
  const hasTopics =
    done &&
    ((result?.candidates?.opus?.topics?.length ?? 0) > 0 ||
      (result?.candidates?.deepseek?.topics?.length ?? 0) > 0);

  // 变化检测（作用于第二步选题）：当前已选评论 vs 已出题覆盖的评论（按评论文本）。
  const coveredComments = new Set<string>();
  if (done) {
    for (const m of ['opus', 'deepseek'] as const) {
      for (const t of result?.candidates?.[m]?.topics ?? []) {
        if (t.anchor_comment) coveredComments.add(t.anchor_comment);
      }
    }
  }
  const currentComments = items.map((it) => it.comment);
  const newCount = currentComments.filter((c) => !coveredComments.has(c)).length;
  const removedCount = [...coveredComments].filter((c) => !currentComments.includes(c)).length;
  // 仅评论驱动时检测变化；无评论「直接拆解」(items 为空)不参与增量比对。
  const changed = items.length > 0 && hasTopics && (newCount > 0 || removedCount > 0);

  // 「分析爆款原因」按钮：有评论时在右上角；无评论时挪到空状态里（文案「直接拆解爆款原因」）。
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
      {/* A. 已选评论 + 分析按钮 */}
      <div className="panel-section">
        <div className="panel-section-title" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <MessageCircle size={14} strokeWidth={1.8} />
          <span style={{ flex: 1 }}>选题参考 · 已选 {items.length}/5 条评论</span>
          {/* 选了评论才在右上角显示；没选评论时按钮在下方空状态里（「直接拆解」） */}
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

      {/* 分析中 / 出题中：评论区下方居中转圈 */}
      {running && (
        <div className="panel-section">
          <div className="gg-loading">
            <Loader2 size={30} strokeWidth={1.8} className="spin" />
            <div className="dim-mono">
              {result?.progress ||
                (stage === 'analyzing' ? '双模型并行分析爆款原因中…' : 'opus / deepseek 双模型并行出题中…')}
            </div>
          </div>
        </div>
      )}

      {/* B. 爆款原因分析（双栏可编辑）+ 2 选 1 出选题 —— 仅 analyzed 阶段 */}
      {analyzed && (
        <div className="panel-section">
          <div className="panel-section-title">
            <Lightbulb size={14} strokeWidth={1.8} /> 爆款原因 · 双模型 · 改一改、选一个出选题
          </div>
          {/* 移动端选项卡：桌面端由 CSS 隐藏，双栏仍并列 */}
          <div className="gg-tabs">
            <button
              className={`gg-tab${activeAnalysisTab === 'opus' ? ' active' : ''}`}
              onClick={() => setActiveAnalysisTab('opus')}
            >
              Opus 4.8
            </button>
            <button
              className={`gg-tab${activeAnalysisTab === 'deepseek' ? ' active' : ''}`}
              onClick={() => setActiveAnalysisTab('deepseek')}
            >
              DeepSeek
            </button>
          </div>
          <div className={`guiguzi-cols tab-active-${activeAnalysisTab}`}>
            <AnalysisColumn
              name="opus"
              label="Opus 4.8"
              col={result?.analysis?.opus}
              value={edits.opus ?? {}}
              onChange={(a) => setEdits((p) => ({ ...p, opus: a }))}
              onUse={() => generate(edits.opus ?? {}, { force: true })}
              disabled={busy}
            />
            <AnalysisColumn
              name="deepseek"
              label="DeepSeek"
              col={result?.analysis?.deepseek}
              value={edits.deepseek ?? {}}
              onChange={(a) => setEdits((p) => ({ ...p, deepseek: a }))}
              onUse={() => generate(edits.deepseek ?? {}, { force: true })}
              disabled={busy}
            />
          </div>
        </div>
      )}

      {/* C. 选题产出（双栏 N 选 1）—— done 阶段 */}
      {done && (
        <div className="panel-section">
          <div className="panel-section-title" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <PenLine size={14} strokeWidth={1.8} />
            <span style={{ flex: 1 }}>选题产出 · 双模型 · 点选一个交柳永</span>
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
          {/* 选题提示词（含 $source 原文占位，可编辑）：改完点上方「重新选题」就用它 */}
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
          <div className="guiguzi-cols">
            <ModelColumn name="opus" label="Opus 4.8" cand={result?.candidates?.opus} chosenTitle={chosenTitle} onChoose={chooseTopic} />
            <ModelColumn name="deepseek" label="DeepSeek" cand={result?.candidates?.deepseek} chosenTitle={chosenTitle} onChoose={chooseTopic} />
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
  col,
  value,
  onChange,
  onUse,
  disabled,
}: {
  name: string;
  label: string;
  col?: GuiguziAnalysisColumn;
  value: GuiguziAnalysis;
  onChange: (a: GuiguziAnalysis) => void;
  onUse: () => void;
  disabled: boolean;
}) {
  if (col?.error) {
    return (
      <div className={`guiguzi-col model-${name}`}>
        <div className="guiguzi-col-head">{label}</div>
        <div className="guiguzi-col-error">模型不可用：{col.error}</div>
      </div>
    );
  }
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
  // 按潜力分倒序：分最高的在最上面（缺分按 0 计垫底）。
  const topics = [...(cand?.topics ?? [])].sort((a, b) => (b.potential ?? 0) - (a.potential ?? 0));
  return (
    <div className={`guiguzi-col model-${name}`}>
      <div className="guiguzi-col-head">{label}</div>
      {cand?.error ? (
        <div className="guiguzi-col-error">模型不可用：{cand.error}</div>
      ) : topics.length === 0 ? (
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
                {chosen ? '已选 · 交柳永' : '选这个'}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}
