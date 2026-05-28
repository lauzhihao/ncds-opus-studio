// rw 节点产物面板：4 模型 tab，每 tab 显示该模型出的 **markdown 候选稿**。
// 候选稿默认可编辑（textarea + 防抖落盘到 draft.md），失败/不可用模型显示原因。
//
// 按钮可用性：
//   - 编辑 toggle：仅当本 tab 已 done（有 draft 内容）才能切到编辑
//   - 「重写本模型」(ghost RefreshCw)：仅当本模型 status !== running 且整体节点 not running
//   - 「整体重新执行」(顶部)：仅当整体节点 status === done（所有 4 模型都 done/failed）
//   - 「用此模型 · 下一步」(primary Play)：本模型 success + 节点 done
//
// 注：本阶段（A）RW 不再出 beats JSON；LINES 节点接收 02_rw/draft.md 后调 LLM 把它结构化。

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  Play,
  RefreshCw,
  Square,
  XCircle,
} from 'lucide-react';

import { api } from '../../api/client';
import type { NodeState, PipelineNodeDef, RwDraft } from '../../api/types';
import { ConfirmDialog } from '../ConfirmDialog';

// 进程状态行（RW 4 模型 / ASR 4 阶段共用）。直角信息框 + 状态配色 + 状态标签。
export type ProcStatus = 'pending' | 'running' | 'done' | 'failed' | 'unavailable';
export interface ProcRow {
  id: string;
  label: string;
  status: ProcStatus;
  detail?: string; // hover tooltip 显示的完整信息（如失败原因全文）
}

const PROC_MAP: Record<ProcStatus, { icon: (s: number) => React.ReactNode; text: string }> = {
  pending: { icon: (s) => <Circle size={s} strokeWidth={1.7} className="rw-ms-pending" />, text: '等待中' },
  running: { icon: () => <span className="rw-ms-blink-dot" aria-label="执行中" />, text: '执行中' },
  done: { icon: (s) => <CheckCircle2 size={s} strokeWidth={2} className="rw-ms-done" />, text: '完成' },
  failed: { icon: (s) => <XCircle size={s} strokeWidth={2} className="rw-ms-failed" />, text: '错误' },
  unavailable: { icon: (s) => <AlertTriangle size={s} strokeWidth={2} className="rw-ms-warn" />, text: '不可用' },
};

export function ProcStatusRow({ row, runningText }: { row: ProcRow; runningText?: string }) {
  const m = PROC_MAP[row.status] ?? PROC_MAP.pending;
  const text = row.status === 'running' && runningText ? runningText : m.text;
  // hover 显示完整信息：有 detail（如失败原因全文）优先，否则 "标签 · 状态"
  const title = row.detail ? `${row.label} · ${row.detail}` : `${row.label} · ${text}`;
  return (
    <div className={`proc-row proc-${row.status}`} title={title}>
      <span className="proc-row-icon">{m.icon(14)}</span>
      <span className="proc-row-label">{row.label}</span>
      <span className="proc-row-badge">{text}</span>
    </div>
  );
}

interface Props {
  jobId: string;
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
  onAdvanced?: () => void;
}

const NEXT_NODE = 'lines';

// RW 体裁选项（对应飞书 /rw -p 参数）。freestyle = 无固定提示词，模型自由发挥。
const RW_PROFILES: { id: string; label: string }[] = [
  { id: 'toutiao', label: '头条图文' },
  { id: 'caijing', label: '抖音财经' },
  { id: 'jitang', label: '心灵鸡汤' },
  { id: 'freestyle', label: '自由发挥' },
];
const DEFAULT_RW_PROFILE = 'freestyle';

// 模型展示名按 model_id 在前端映射 —— label 是展示层，改这里立即对所有 job（含历史 job）
// 生效，不依赖后端 outputs 里存的旧 label。outputs.label 仅作未知 id 的兜底。
const MODEL_LABELS: Record<string, string> = {
  opus: 'Claude Opus 4.7',
  gpt5: 'GPT-5.5',
  gemini_local: 'GEMINI-3.5 FLASH',
  deepseek: 'DeepSeek V4 Pro',
};
const modelLabel = (id: string, fallback: string): string => MODEL_LABELS[id] ?? fallback;

export function RwResultPanel({ jobId, nodeDef, nodeState, onAdvanced }: Props) {
  const drafts = (nodeState.outputs?.drafts as RwDraft[] | undefined) ?? [];
  // 下方 tabs 只渲染成功的稿件；失败/不可用模型只在上面的状态行展示。
  const successDrafts = drafts.filter((d) => d.status !== 'failed');
  const selectedModelId =
    (nodeState.outputs?.selected_model_id as string | null | undefined) ?? null;
  const status = nodeState.status;
  // 体裁 profile：idle 时用户可选（本地 state）；跑过后 outputs.profile 是实际用的体裁
  const ranProfile = nodeState.outputs?.profile as string | undefined;
  const [profile, setProfile] = useState<string>(ranProfile || DEFAULT_RW_PROFILE);
  // 跑完后同步成实际用的体裁（done/running 时锁定显示）
  useEffect(() => {
    if (ranProfile) setProfile(ranProfile);
  }, [ranProfile]);
  const profileLocked = status === 'running' || status === 'queued' || status === 'done';

  const [tab, setTab] = useState<string>(successDrafts[0]?.model_id ?? '');
  const [cache, setCache] = useState<Record<string, string>>({});
  const [loadingTab, setLoadingTab] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [rewriteBusy, setRewriteBusy] = useState(false);
  const [pendingRerun, setPendingRerun] = useState(false);

  // tab 未设或当前 tab 已不在成功列表里（如增量过程中），落到第一个成功稿
  useEffect(() => {
    if (successDrafts.length === 0) return;
    if (!tab || !successDrafts.some((d) => d.model_id === tab)) {
      setTab(successDrafts[0].model_id);
    }
  }, [successDrafts, tab]);

  // RW 整体重跑后 finished_at 变化 → draft.md 内容已换一批，清空缓存强制重新 fetch，
  // 否则会一直显示上一轮的旧产物（之前用户看到"还是 JSON"就是这个 bug）。
  useEffect(() => {
    setCache({});
    pendingRef.current.clear();
  }, [nodeState.finished_at]); // eslint-disable-line react-hooks/exhaustive-deps

  // 切 tab 时若没缓存就 fetch；failed 的 tab 直接展示 reason
  useEffect(() => {
    if (!tab) return;
    if (cache[tab] !== undefined) return;
    const d = drafts.find((x) => x.model_id === tab);
    if (!d) return;
    if (d.status === 'failed' || !d.draft_relpath) {
      setCache((c) => ({ ...c, [tab]: `（${d.reason || '模型不可用'}）` }));
      return;
    }
    setLoadingTab(tab);
    fetch(`/jobs/${jobId}/files/${d.draft_relpath}`)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((text) => setCache((c) => ({ ...c, [tab]: text })))
      .catch((e) =>
        setCache((c) => ({ ...c, [tab]: `加载失败: ${(e as Error).message}` })),
      )
      .finally(() => setLoadingTab(null));
  }, [tab, jobId, drafts, cache]);

  // 防抖落盘：modelId → 待写文本
  const pendingRef = useRef<Map<string, string>>(new Map());
  const debounceTimer = useRef<number | null>(null);
  const [, forceRender] = useState(0);

  const flushDrafts = useCallback(async (): Promise<void> => {
    if (debounceTimer.current != null) {
      window.clearTimeout(debounceTimer.current);
      debounceTimer.current = null;
    }
    const entries = Array.from(pendingRef.current.entries());
    if (entries.length === 0) return;
    pendingRef.current.clear();
    forceRender((x) => x + 1);
    await Promise.all(
      entries.map(([modelId, text]) => {
        const d = drafts.find((x) => x.model_id === modelId);
        if (!d || !d.draft_relpath || d.status === 'failed') return Promise.resolve();
        return api.writeFile(jobId, d.draft_relpath, text).catch((e) => {
          pendingRef.current.set(modelId, text);
          console.error('[rw] save draft failed', modelId, e);
        });
      }),
    );
    forceRender((x) => x + 1);
  }, [drafts, jobId]);

  const onEdit = useCallback(
    (modelId: string, text: string) => {
      setCache((c) => ({ ...c, [modelId]: text }));
      pendingRef.current.set(modelId, text);
      forceRender((x) => x + 1);
      if (debounceTimer.current != null) window.clearTimeout(debounceTimer.current);
      debounceTimer.current = window.setTimeout(() => {
        void flushDrafts();
      }, 600);
    },
    [flushDrafts],
  );

  async function doRun() {
    setActionBusy(true);
    try {
      await api.runNode(jobId, nodeDef.name, { profile });
    } catch (e) {
      alert(`启动失败: ${(e as Error).message}`);
    } finally {
      setActionBusy(false);
    }
  }

  async function doCancel() {
    setActionBusy(true);
    try {
      await api.cancelNode(jobId, nodeDef.name);
    } catch (e) {
      alert(`停止失败: ${(e as Error).message}`);
    } finally {
      setActionBusy(false);
    }
  }

  async function doRewriteTab() {
    if (!tab) return;
    setRewriteBusy(true);
    try {
      await flushDrafts(); // 先 flush 当前编辑
      await api.rewriteRwModel(jobId, tab);
      setCache((c) => {
        const next = { ...c };
        delete next[tab];
        return next;
      });
    } catch (e) {
      alert(`重写失败: ${(e as Error).message}`);
    } finally {
      setRewriteBusy(false);
    }
  }

  async function doAdvance() {
    if (!tab) return;
    setActionBusy(true);
    try {
      await flushDrafts();
      await api.selectRwModel(jobId, tab);
      await api.runNode(jobId, NEXT_NODE);
      onAdvanced?.();
    } catch (e) {
      alert(`进入下一步失败: ${(e as Error).message}`);
    } finally {
      setActionBusy(false);
    }
  }

  function renderActionBtn() {
    if (status === 'running' || status === 'queued') {
      return (
        <button className="btn primary sm" disabled={actionBusy} onClick={doCancel}>
          <Square size={11} strokeWidth={2.2} fill="currentColor" /> 停止
        </button>
      );
    }
    if (status === 'done') {
      return (
        <button
          className="btn primary sm"
          title="清空 4 个模型 draft 及下游产物后重新跑"
          disabled={actionBusy}
          onClick={() => setPendingRerun(true)}
        >
          <RefreshCw size={12} strokeWidth={1.9} /> 重新执行
        </button>
      );
    }
    return (
      <button className="btn primary sm" disabled={actionBusy} onClick={doRun}>
        <Play size={12} strokeWidth={2} /> 开始改写
      </button>
    );
  }

  const statusBadge =
    status === 'running'
      ? ' · RUNNING'
      : status === 'queued'
        ? ' · QUEUED'
        : status === 'failed'
          ? ' · FAILED'
          : '';
  const body = cache[tab];
  const loading = loadingTab === tab;
  const currentDraft = drafts.find((d) => d.model_id === tab);
  // 4 模型状态行：running 期间用后端推的 outputs.model_progress；done 后从 drafts 派生。
  // 这样 4 行状态框在 running / done 都常驻显示（done 后不消失）。
  const modelProgress = nodeState.outputs?.model_progress as
    | Record<string, { model_id: string; label: string; status: ProcStatus }>
    | undefined;
  const statusRows: ProcRow[] =
    modelProgress && Object.keys(modelProgress).length > 0
      ? Object.values(modelProgress).map((m) => ({ id: m.model_id, label: modelLabel(m.model_id, m.label), status: m.status }))
      : drafts.map((d) => ({
          id: d.model_id,
          label: modelLabel(d.model_id, d.label),
          status:
            d.status === 'success'
              ? 'done'
              : (d.reason ?? '').includes('模型不可用')
                ? 'unavailable'
                : 'failed',
        }));
  // 成功的稿件默认可编辑（textarea 直接改 + 防抖落盘）；失败/加载中不可编辑
  const editable = currentDraft?.status === 'success' && !loading;
  // 单模型重试：节点不在跑 + 本模型当前不在 rewrite + (本模型 success 或 failed)
  const canRewriteThisTab =
    !rewriteBusy &&
    !actionBusy &&
    status === 'done' &&
    currentDraft != null &&
    currentDraft.status !== undefined;

  let hint: { tone: 'info' | 'error'; text: string } | null = null;
  if (status === 'failed' && nodeState.error) {
    hint = { tone: 'error', text: `失败：${nodeState.error}` };
  } else if (drafts.length === 0 && status === 'idle') {
    hint = { tone: 'info', text: '点击下方「开始改写」启动，顶级模型改写。' };
  }

  return (
    <div className="rw-panel-root">
      {hint && <div className={`panel-hint panel-hint-${hint.tone}`}>{hint.text}</div>}

      {/* 体裁选项（-p）：idle 可选，running/done 锁定显示实际用的体裁 */}
      <div className="rw-profile-bar">
        <span className="rw-profile-label">体裁</span>
        {RW_PROFILES.map((p) => (
          <button
            key={p.id}
            type="button"
            className={`rw-profile-chip${profile === p.id ? ' active' : ''}`}
            disabled={profileLocked}
            onClick={() => setProfile(p.id)}
            title={p.id === 'freestyle' ? '无固定提示词，模型按原文自由发挥' : `-p ${p.id}`}
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* 4 模型状态框：放在「RW 改写」标题分割线上方；running / done 都常驻（done 后不消失） */}
      {statusRows.length > 0 && (
        <div className="proc-rows" style={{ marginBottom: 'var(--s-3)' }}>
          {statusRows.map((r) => (
            <ProcStatusRow key={r.id} row={r} runningText="改写中" />
          ))}
        </div>
      )}

      <div className="rw-panel-header">
        <div
          className={`section-h${status === 'running' || status === 'queued' ? ' loading' : ''}`}
          style={{ margin: 0, flex: 1 }}
        >
          RW 改写 · {drafts.length} 个模型{statusBadge}
        </div>
        {renderActionBtn()}
      </div>

      {successDrafts.length === 0 ? null : (
        <>
          <nav className="asr-tabs">
            {successDrafts.map((d) => (
              <button
                key={d.model_id}
                type="button"
                className={`asr-tab${tab === d.model_id ? ' active' : ''}`}
                onClick={() => setTab(d.model_id)}
                title={modelLabel(d.model_id, d.label)}
              >
                {modelLabel(d.model_id, d.label)}
                {selectedModelId === d.model_id && (
                  <CheckCircle2
                    size={11}
                    strokeWidth={2}
                    style={{ marginLeft: 4, color: 'var(--accent)', verticalAlign: '-1px' }}
                  />
                )}
              </button>
            ))}
            <span style={{ flex: 1 }} />
            <button
              type="button"
              className="btn sm icon-only ghost"
              title={rewriteBusy ? '重写中…' : canRewriteThisTab ? '重写本模型' : '需先等本模型完成'}
              disabled={!canRewriteThisTab}
              onClick={doRewriteTab}
            >
              <RefreshCw size={12} strokeWidth={1.7} />
            </button>
            <button
              type="button"
              className="btn sm icon-only primary"
              title="用此模型 · 下一步（启动 lines）"
              disabled={actionBusy || status !== 'done' || !tab}
              onClick={doAdvance}
            >
              <Play size={12} strokeWidth={2} fill="currentColor" />
            </button>
          </nav>
          {loading ? (
            <div className="article-pane dim-mono">加载中…</div>
          ) : (
            <textarea
              key={`edit-${tab}`}
              className="code-pane editable rw-textarea"
              value={body ?? ''}
              placeholder="（无内容）"
              readOnly={!editable}
              onChange={(e) => editable && onEdit(tab, e.target.value)}
              spellCheck={false}
            />
          )}
        </>
      )}

      <ConfirmDialog
        open={pendingRerun}
        title="重新执行 RW？"
        message={<>会清空 4 个模型 draft 以及所有下游节点的状态与产物，然后重新跑。</>}
        confirmLabel="重新执行"
        danger
        onConfirm={async () => {
          await doRun();
          setPendingRerun(false);
        }}
        onCancel={() => setPendingRerun(false)}
      />
    </div>
  );
}
