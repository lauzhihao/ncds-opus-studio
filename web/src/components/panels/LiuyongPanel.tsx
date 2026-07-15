// 柳永（rw 出稿）面板：4 模型 tab，每 tab 显示该模型出的 **markdown 候选稿**。
// 候选稿默认可编辑（textarea + 防抖落盘到 draft.md），失败/不可用模型显示原因。
//
// 按钮可用性：
//   - 编辑 toggle：仅当本 tab 已 done（有 draft 内容）才能切到编辑
//   - 「重写本模型」(ghost RefreshCw)：仅当本模型 status !== running 且整体节点 not running
//   - 「整体重新执行」(顶部)：仅当整体节点 status === done（所有 4 模型都 done/failed）
//   - 「定稿交给吴道子」(primary Play)：本模型 success + 节点 done
//
// 注：本阶段 RW 不直接出视觉结构；lines 仍是底层 preflight，由吴道子入口隐藏承接。

import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, FileText, Play, RefreshCw, Sparkles, Square } from 'lucide-react';

import { api } from '../../api/client';
import type { NodeState, PipelineNodeDef, RwDraft } from '../../api/types';
import { ConfirmDialog } from '../ConfirmDialog';
import { useToast } from '../Toast';

interface Props {
  jobId: string;
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
  onAdvanced?: () => void;
  /** 外部主动触发 SSE 重连，防止 doRun 期间 SSE 事件冲入造成竞态。 */
  onReconnectSSE?: () => void;
}

const NEXT_NODE = 'lines';

// 体裁 profile 已废：写作由作品垂类标签(domain)的写作方法独家驱动，柳永不再让用户选体裁。

const RUBRIC_DIMS = ['节奏', '真实性', '精炼度', '直接性', '信任度'];

// 五边形雷达图：5 维度各满分 10（总分 50）。顶点从正上方起、顺时针每 72° 一个。
// 画 4 圈网格 + 5 条轴 + 数据多边形 + 顶点标注（维度名 + 分值）。
function QcRadar({ dims, max = 10, size = 180 }: { dims: Record<string, number>; max?: number; size?: number }) {
  const cx = size / 2;
  const cy = size / 2;
  const R = size / 2 - 26; // 留出外圈标注空间
  const n = RUBRIC_DIMS.length;
  const ang = (i: number) => -Math.PI / 2 + (i * 2 * Math.PI) / n;
  const pt = (i: number, r: number): [number, number] => [cx + r * Math.cos(ang(i)), cy + r * Math.sin(ang(i))];
  const poly = (r: number) => RUBRIC_DIMS.map((_, i) => pt(i, r).join(',')).join(' ');

  const dataPts = RUBRIC_DIMS.map((d, i) => {
    const v = Math.max(0, Math.min(max, dims[d] ?? 0));
    return pt(i, R * (v / max));
  });

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ overflow: 'visible' }}>
      {/* 网格圈 */}
      {[0.25, 0.5, 0.75, 1].map((f) => (
        <polygon key={f} points={poly(R * f)} fill="none" stroke="var(--border, rgba(0,0,0,0.1))" strokeWidth={1} />
      ))}
      {/* 轴线 */}
      {RUBRIC_DIMS.map((_, i) => {
        const [x, y] = pt(i, R);
        return <line key={i} x1={cx} y1={cy} x2={x} y2={y} stroke="var(--border, rgba(0,0,0,0.1))" strokeWidth={1} />;
      })}
      {/* 数据多边形 */}
      <polygon
        points={dataPts.map((p) => p.join(',')).join(' ')}
        fill="var(--accent)"
        fillOpacity={0.16}
        stroke="var(--accent)"
        strokeWidth={1.5}
      />
      {dataPts.map((p, i) => (
        <circle key={i} cx={p[0]} cy={p[1]} r={2.5} fill="var(--accent)" />
      ))}
      {/* 顶点标注：维度名 + 分值 */}
      {RUBRIC_DIMS.map((d, i) => {
        const [lx, ly] = pt(i, R + 13);
        const anchor = Math.abs(lx - cx) < 1 ? 'middle' : lx > cx ? 'start' : 'end';
        return (
          <text key={d} x={lx} y={ly} textAnchor={anchor} fontSize={9} fill="var(--ink-2)">
            <tspan x={lx} dy={0}>{d}</tspan>
            <tspan x={lx} dy={11} fontWeight={600} fill="var(--ink-1)">{dims[d] ?? '-'}</tspan>
          </text>
        );
      })}
    </svg>
  );
}

// 柳永质检报告：AI 味 verdict + rubric 5 维度雷达图（对齐 app liuyong 详情页 _QCReport）。
// 质检字段（qc/qc_rubric）由后端质检闸门产出；还在后台跑时字段未到，整块不渲染。
// AI 味 verdict 只在「仍超标」(fail) 时提示：pass 是自动打回重写后的常态终判，
// 对用户无信息量、反像系统自言自语，故 pass 时静默（只留质量等级 pill）。
function countChars(text: string): number {
  return text.replace(/\s/g, '').length;
}

function QcReport({
  draft,
  onRefine,
  refining,
  canRefine,
  wordCount,
}: {
  draft?: RwDraft;
  onRefine?: () => void;
  refining?: boolean;
  canRefine?: boolean;
  wordCount?: number;
}) {
  if (!draft || draft.status !== 'success') return null;
  const qc = draft.qc;
  const rub = draft.qc_rubric;
  if (!qc && !rub) return null;
  const showFail = qc?.verdict === 'fail';
  return (
    <div className="rw-qc-report">
      {/* 顶部一行：字数标签（左）+ AI 味告警（左）+ 质量等级 pill（右） */}
      <div className="rw-qc-head">
        <span className="rw-qc-meta-left">
          <span className="rw-qc-grade rw-qc-wordcount">
            <FileText size={12} strokeWidth={1.6} />
            {wordCount ?? '-'}
          </span>
          {showFail && (
            <span className="rw-qc-verdict fail">
              <AlertTriangle size={14} />
              AI 味仍超标
            </span>
          )}
        </span>
        {rub?.grade && <span className="rw-qc-grade">{rub.grade}</span>}
      </div>

      {rub?.available ? (
        <div className="rw-qc-chart">
          <QcRadar dims={rub.dims ?? {}} />
          <span className="rw-qc-score">
            质量分 <b>{rub.total}</b> / 50
            {rub.judge_model && (
              <span className="rw-qc-judge">（judge: {rub.judge_model}）</span>
            )}
          </span>
        </div>
      ) : rub ? (
        <span className="rw-qc-skip">质量分跳过（{rub.skipped}）</span>
      ) : null}

      {rub?.issues && rub.issues.length > 0 && (
        <div className="rw-qc-issues">
          <div className="rw-qc-issues-head">
            <span className="rw-qc-issues-label">优化建议</span>
            {onRefine && (
              <button
                type="button"
                className="btn sm primary rw-qc-refine-btn"
                disabled={!canRefine}
                onClick={onRefine}
              >
                <Sparkles size={11} strokeWidth={1.9} />
                {refining ? '优化中…' : '立即优化'}
              </button>
            )}
          </div>
          {rub.issues.slice(0, 3).map((it, i) => (
            <span key={i} className="rw-qc-issue">
              {it}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// 模型展示名按 model_id 在前端映射 —— label 是展示层，改这里立即对所有 job（含历史 job）
// 生效，不依赖后端 outputs 里存的旧 label。outputs.label 仅作未知 id 的兜底。
// 泛化：对外只暴露「改写方案 A/B/C/D」，不泄露真实模型身份（与后端 MODEL_CANDIDATES.label 一致）。
const MODEL_LABELS: Record<string, string> = {
  opus: '改写方案 A',
  deepseek: '改写方案 B',
  agy: '改写方案 C',
  codex: '改写方案 D',
};
const modelLabel = (id: string, fallback: string): string => MODEL_LABELS[id] ?? fallback;

export function LiuyongPanel({ jobId, nodeDef, nodeState, onAdvanced, onReconnectSSE }: Props) {
  const { showToast } = useToast();
  const drafts = (nodeState.outputs?.drafts as RwDraft[] | undefined) ?? [];
  // 下方 tabs 只渲染成功的稿件；失败/不可用模型只在上面的状态行展示。
  const successDrafts = drafts.filter((d) => d.status !== 'failed');
  const selectedModelId =
    (nodeState.outputs?.selected_model_id as string | null | undefined) ?? null;
  const status = nodeState.status;

  // 节点真正离开 running/queued（SSE 翻状态/取消落地/失败）后，清掉乐观「停止中」态。
  useEffect(() => {
    if (status !== 'running' && status !== 'queued') setStopping(false);
  }, [status]);

  const [tab, setTab] = useState<string>(successDrafts[0]?.model_id ?? '');
  const [cache, setCache] = useState<Record<string, string>>({});
  const [loadingTab, setLoadingTab] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [rewriteBusy, setRewriteBusy] = useState(false);
  const [refineBusy, setRefineBusy] = useState(false);
  const [pendingRerun, setPendingRerun] = useState(false);
  // 乐观「停止中…」过渡态：cancel 是协作式的，POST 秒回 {cancelled:true} 只是信号已收下，
  // 节点要等 worker 跑到检查点真正停下、SSE 把 status 翻出 running 才落地。点「停止」即进入此态，
  // 给即时反馈（不伪造 idle——进程确实还在跑）；status 离开 running/queued 后自动清。
  const [stopping, setStopping] = useState(false);

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
    fetch(`/jobs/${jobId}/files/${d.draft_relpath}`, { credentials: 'same-origin' })
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
    // 断开 SSE，避免 runNode 期间后端先发的 SSE 事件与 POST 返回产生竞态
    onReconnectSSE?.();
    try {
      await api.runNode(jobId, nodeDef.name);
    } catch (e) {
      showToast('启动失败，请稍后再试');
      console.error('[LiuyongPanel] 启动失败', e);
    } finally {
      // 重连 SSE，确保后续事件可正常推送
      onReconnectSSE?.();
      setActionBusy(false);
    }
  }

  async function doCancel() {
    setActionBusy(true);
    setStopping(true); // 乐观进入「停止中…」：真正 idle 等 SSE 翻 status（见 stopping 注释）
    try {
      await api.cancelNode(jobId, nodeDef.name);
    } catch (e) {
      setStopping(false); // 取消请求本身失败，回滚过渡态
      showToast('停止失败，请稍后再试');
      console.error('[LiuyongPanel] 停止失败', e);
    } finally {
      setActionBusy(false);
    }
  }

  async function doRewriteTab() {
    setRewriteBusy(true);
    try {
      await flushDrafts();
      const modelIds = drafts.map((d) => d.model_id);
      for (const mid of modelIds) {
        try {
          await api.rewriteRwModel(jobId, mid);
        } catch (e) {
          console.error(`[LiuyongPanel] 模型 ${mid} 改写失败`, e);
        }
      }
      setCache((c) => {
        const next = { ...c };
        for (const mid of modelIds) delete next[mid];
        return next;
      });
    } catch (e) {
      console.error('[LiuyongPanel] 改写异常', e);
    } finally {
      setRewriteBusy(false);
    }
  }

  // 「按建议优化」：把所有模型的稿连同 rubric issues 交给后端，按质检建议最小改动优化。
  // 后端改完会重跑质检并 emit 新 qc_rubric，前端清缓存重拉 draft.md。
  async function doRefineTab() {
    if (!tab) return;
    setRefineBusy(true);
    try {
      await flushDrafts();
      await api.refineRwModel(jobId, tab);
      setCache((c) => {
        const next = { ...c };
        delete next[tab];
        return next;
      });
    } catch (e) {
      console.error(`[LiuyongPanel] 模型 ${tab} 优化失败`, e);
      showToast(`优化失败：${(e as Error)?.message ?? '未知错误'}`);
    } finally {
      setRefineBusy(false);
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
      showToast('交给吴道子失败，请稍后再试');
      console.error('[LiuyongPanel] advance to wudaozi failed', e);
    } finally {
      setActionBusy(false);
    }
  }

  function renderActionBtn() {
    if (status === 'running' || status === 'queued') {
      return (
        <button className="btn primary sm" disabled={actionBusy || stopping} onClick={doCancel}>
          <Square size={11} strokeWidth={2.2} fill="currentColor" /> {stopping ? '停止中…' : '停止'}
        </button>
      );
    }
    if (status === 'done') {
      return (
        <button
          className="btn primary sm"
          title="清空 4 个模型 draft 及下游产物后重新跑"
          disabled={actionBusy || refineBusy}
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
  // 成功的稿件默认可编辑（textarea 直接改 + 防抖落盘）；失败/加载中不可编辑
  const editable = currentDraft?.status === 'success' && !loading && !refineBusy;
  // 全部模型重写：节点 done 且无其他进行中操作
  const canRewriteThisTab =
    !rewriteBusy &&
    !refineBusy &&
    !actionBusy &&
    status === 'done';
  // 「按建议优化」可用：节点 done + 有 rubric issues + 无其他进行中操作
  const canRefineThisTab =
    !refineBusy &&
    !rewriteBusy &&
    !actionBusy &&
    status === 'done' &&
    successDrafts.some((d) => (d.qc_rubric?.issues?.length ?? 0) > 0);

  let hint: { tone: 'info' | 'error'; text: string } | null = null;
  if (drafts.length === 0 && status === 'idle') {
    hint = { tone: 'info', text: '点击下方「开始改写」启动，顶级模型改写。' };
  }

  return (
    <div className="rw-panel-root liuyong-panel-root">
      {hint && <div className={`panel-hint panel-hint-${hint.tone}`}>{hint.text}</div>}

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
              title={rewriteBusy ? '全部重写中…' : canRewriteThisTab ? '全部模型重写' : '需先等执行完成'}
              disabled={!canRewriteThisTab}
              onClick={doRewriteTab}
            >
              <RefreshCw size={12} strokeWidth={1.7} />
            </button>
            <button
              type="button"
              className="btn sm icon-only primary"
              title="定稿交给吴道子画面工作台"
              disabled={actionBusy || refineBusy || status !== 'done' || !tab}
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
          {/* 质检报告放在改写正文下方 */}
          <QcReport
            draft={currentDraft}
            onRefine={doRefineTab}
            refining={refineBusy}
            canRefine={canRefineThisTab}
            wordCount={body ? countChars(body) : undefined}
          />
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
