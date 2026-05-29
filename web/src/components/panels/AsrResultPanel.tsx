// asr 节点产物面板：手风琴 + 「文章整理」单视图。
// header 分割线右侧单按钮按节点状态变形（仅控制 ASR 本节点）：
//   IDLE/FAILED → Play  + 开始转写  → runNode('asr')
//   RUNNING/QUEUED → Square + 停止 → cancelNode('asr')
//   DONE         → RefreshCw + 重新执行（弹确认）→ runNode('asr')（清下游 + 重跑）
// 触发 RW 的 play 按钮放在文章整理面板的复制按钮旁。
// 注：「听写稿」tab 已下线 —— 阿里 ASR 输出稳定，用户需要原稿可去 transcript_relpath。
//     「精华稿」tab 也已下线 —— 爆款精华提取转由 rw 节点的多模型并行改写承担。

import { useEffect, useState } from 'react';
import {
  ChevronDown,
  ChevronRight,
  Copy,
  ExternalLink,
  Play,
  RefreshCw,
  Square,
  User,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';

import { api } from '../../api/client';
import type { AsrItem, NodeState, PipelineNodeDef } from '../../api/types';
import { ConfirmDialog } from '../ConfirmDialog';
import { useToast } from '../Toast';
import { ProcStatusRow, type ProcStatus } from './RwResultPanel';

interface Props {
  jobId: string;
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
  // 触发 RW 成功后由父组件关闭抽屉，让用户看见画布的脉冲线
  onAdvanced?: () => void;
}


// 作品级进度：后端 _execute_asr 把每条 URL 的 {status, stage} 实时推到 outputs.item_progress
interface AsrItemProgress {
  index: number;
  title: string;
  url: string;
  status: ProcStatus;
  stage: string;
  error?: string;
}

export function AsrResultPanel({ jobId, nodeDef, nodeState, onAdvanced }: Props) {
  const { showToast } = useToast();
  const items = (nodeState.outputs?.items as AsrItem[] | undefined) ?? [];
  const status = nodeState.status;
  const [openIdx, setOpenIdx] = useState<number>(0);
  const [pendingRerun, setPendingRerun] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);

  // 作品级状态行：每条 URL 一行（后端推的 item_progress），running 时显示实时阶段
  const itemProgress = nodeState.outputs?.item_progress as
    | Record<string, AsrItemProgress>
    | undefined;
  const itemRows = itemProgress ? Object.values(itemProgress) : [];

  async function doRun() {
    setActionBusy(true);
    try {
      await api.runNode(jobId, nodeDef.name);
    } catch (e) {
      showToast('启动失败，请稍后再试');
      console.error('[AsrResultPanel] 启动失败', e);
    } finally {
      setActionBusy(false);
    }
  }

  async function doCancel() {
    setActionBusy(true);
    try {
      await api.cancelNode(jobId, nodeDef.name);
    } catch (e) {
      showToast('停止失败，请稍后再试');
      console.error('[AsrResultPanel] 停止失败', e);
    } finally {
      setActionBusy(false);
    }
  }

  // 文章整理面板 play 按钮触发：直接启动 RW
  const [advanceBusy, setAdvanceBusy] = useState(false);
  async function doAdvanceToRw() {
    setAdvanceBusy(true);
    try {
      await api.runNode(jobId, 'rw');
      onAdvanced?.();
    } catch (e) {
      showToast('启动 RW 失败，请稍后再试');
      console.error('[AsrResultPanel] 启动 RW 失败', e);
    } finally {
      setAdvanceBusy(false);
    }
  }

  function renderActionBtn() {
    if (status === 'running' || status === 'queued') {
      return (
        <button
          className="btn primary sm"
          disabled={actionBusy}
          onClick={doCancel}
        >
          <Square size={11} strokeWidth={2.2} fill="currentColor" /> 停止
        </button>
      );
    }
    if (status === 'done') {
      return (
        <button
          className="btn primary sm"
          title="清空 ASR 及下游产物后重新跑"
          disabled={actionBusy}
          onClick={() => setPendingRerun(true)}
        >
          <RefreshCw size={12} strokeWidth={1.9} /> 重新执行
        </button>
      );
    }
    return (
      <button
        className="btn primary sm"
        disabled={actionBusy}
        onClick={doRun}
      >
        <Play size={12} strokeWidth={2} /> 开始转写
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

  // 统一提示 banner（标题上方，带色条）：idle/空产物时引导，failed 时显示错误
  let hint: { tone: 'info' | 'error'; text: string } | null = null;
  if (status === 'failed' && nodeState.error) {
    hint = { tone: 'error', text: `失败：${nodeState.error}` };
  } else if (items.length === 0 && status === 'idle') {
    hint = { tone: 'info', text: '点击下方「开始转写」启动。' };
  } else if (items.length === 0 && status === 'done') {
    hint = { tone: 'info', text: '暂无产物。' };
  }

  return (
    <div className="asr-panel-root">
      {hint && <div className={`panel-hint panel-hint-${hint.tone}`}>{hint.text}</div>}

      {/* 作品状态行：放在「ASR 产物」标题分割线上方。running 时显示进度；
          failed 时也显示，失败作品 badge=错误，hover 看完整错误信息。 */}
      {itemRows.length > 0 && (status === 'running' || status === 'queued' || status === 'failed') && (
        <div className="proc-rows" style={{ marginBottom: 'var(--s-3)' }}>
          {itemRows.map((it) => (
            <ProcStatusRow
              key={it.index}
              row={{
                id: String(it.index),
                label: it.title || shortUrl(it.url) || `作品 ${it.index}`,
                status: it.status,
                detail: it.error || undefined,
              }}
              runningText={it.stage || '处理中'}
            />
          ))}
        </div>
      )}
      {(status === 'running' || status === 'queued') && itemRows.length === 0 && (
        <div className="dim-mono" style={{ marginBottom: 'var(--s-3)' }}>
          {nodeState.progress || '正在启动…'}
        </div>
      )}

      <div
        className="asr-panel-header"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--s-3)',
          margin: 'var(--s-3) 0',
        }}
      >
        <div
          className={`section-h${status === 'running' || status === 'queued' ? ' loading' : ''}`}
          style={{ margin: 0, flex: 1 }}
        >
          ASR 产物 · {items.length} 条视频{statusBadge}
        </div>
        {renderActionBtn()}
      </div>

      {items.length > 0 && (
        <div className="asr-acc">
          {items.map((item, idx) => (
            <AsrItemRow
              key={item.url || idx}
              jobId={jobId}
              item={item}
              expanded={openIdx === idx}
              onToggle={() => setOpenIdx(openIdx === idx ? -1 : idx)}
              onAdvanceToRw={doAdvanceToRw}
              advanceBusy={advanceBusy}
            />
          ))}
        </div>
      )}

      <ConfirmDialog
        open={pendingRerun}
        title="重新执行 ASR？"
        message={<>会清空 ASR 节点及所有下游节点的状态与产物，然后重新跑。</>}
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

function AsrItemRow({
  jobId,
  item,
  expanded,
  onToggle,
  onAdvanceToRw,
  advanceBusy,
}: {
  jobId: string;
  item: AsrItem;
  expanded: boolean;
  onToggle: () => void;
  onAdvanceToRw: () => void | Promise<void>;
  advanceBusy: boolean;
}) {
  // 只剩 article 一个 tab；保留 body/loading 命名风格便于未来再加 tab
  const [body, setBody] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!expanded) return;
    if (body !== undefined) return;
    const relpath = item.article_relpath;
    if (!relpath) {
      setBody('(无内容)');
      return;
    }
    setLoading(true);
    fetch(`/jobs/${jobId}/files/${relpath}`)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((text) => setBody(text))
      .catch((e) => setBody(`加载失败: ${(e as Error).message}`))
      .finally(() => setLoading(false));
  }, [expanded, jobId, item, body]);

  const failed = !!item.error;

  return (
    <article className={`asr-item${expanded ? ' open' : ''}${failed ? ' failed' : ''}`}>
      <header className="asr-item-head asr-item-head-2lines" onClick={onToggle}>
        {expanded ? (
          <ChevronDown size={14} strokeWidth={1.7} />
        ) : (
          <ChevronRight size={14} strokeWidth={1.7} />
        )}
        <span className="asr-item-num mono">
          {String(item.index ?? '?').padStart(2, '0')}
        </span>
        <div className="asr-item-meta">
          <div className="asr-item-meta-row">
            {item.title && (
              <span className="asr-item-title" title={item.title}>
                {item.title}
              </span>
            )}
            {item.author && (
              <span className="dim-mono asr-item-author">
                <User size={11} strokeWidth={1.7} style={{ verticalAlign: '-1px', marginRight: 2 }} />
                {item.author}
              </span>
            )}
          </div>
          {item.url && (
            <a
              className="asr-item-url"
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              title={item.url}
            >
              <span className="link-text">{shortUrl(item.url)}</span>
              <ExternalLink size={11} strokeWidth={1.7} />
            </a>
          )}
        </div>
      </header>

      {expanded && !failed && (
        <div className="asr-item-body">
          <nav className="asr-tabs">
            <span className="asr-tab active">文章整理</span>
            <span style={{ flex: 1 }} />
            <button
              type="button"
              className="btn sm icon-only ghost"
              title="复制全文"
              disabled={!body}
              onClick={() => body && navigator.clipboard.writeText(body)}
            >
              <Copy size={12} strokeWidth={1.7} />
            </button>
            <button
              type="button"
              className="btn sm icon-only primary"
              title="基于整理后的文章启动改写（RW）"
              disabled={advanceBusy || !body}
              onClick={onAdvanceToRw}
            >
              <Play size={12} strokeWidth={2} fill="currentColor" />
            </button>
          </nav>
          {body ? (
            <div className="article-pane">
              <ReactMarkdown>{body}</ReactMarkdown>
            </div>
          ) : (
            <pre className="code-pane">{loading ? '加载中…' : ''}</pre>
          )}
        </div>
      )}

      {expanded && failed && (
        <div className="asr-item-body asr-error">{item.error}</div>
      )}
    </article>
  );
}

function shortUrl(u: string): string {
  try {
    const x = new URL(u);
    const tail = x.pathname.length > 1 ? x.pathname : '';
    return x.host + tail;
  } catch {
    return u;
  }
}
