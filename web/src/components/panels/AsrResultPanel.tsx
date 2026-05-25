// asr 节点产物面板：手风琴 + 三 tab；精华稿默认可编辑（textarea + 自动防抖保存）。
// header 分割线右侧单按钮按节点状态变形（仅控制 ASR 本节点）：
//   IDLE/FAILED → Play  + 开始转写  → runNode('asr')
//   RUNNING/QUEUED → Square + 停止 → cancelNode('asr')
//   DONE         → RefreshCw + 重新执行（弹确认）→ runNode('asr')（清下游 + 重跑）
// 触发 RW 的 play 按钮单独放在精华稿 tab 的复制按钮旁，点击前会 flush 草稿再 runNode('rw')。

import { useCallback, useEffect, useRef, useState } from 'react';
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

import { api } from '../../api/client';
import type { AsrItem, NodeState, PipelineNodeDef } from '../../api/types';
import { ConfirmDialog } from '../ConfirmDialog';

interface Props {
  jobId: string;
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
  // 触发 RW 成功后由父组件关闭抽屉，让用户看见画布的脉冲线
  onAdvanced?: () => void;
}

type TabKey = 'transcript' | 'article' | 'highlight';

const TABS: { key: TabKey; label: string; relpathKey: keyof AsrItem }[] = [
  { key: 'transcript', label: '听写稿', relpathKey: 'transcript_relpath' },
  { key: 'article', label: '文章解析', relpathKey: 'article_relpath' },
  { key: 'highlight', label: '精华稿', relpathKey: 'highlight_relpath' },
];

export function AsrResultPanel({ jobId, nodeDef, nodeState, onAdvanced }: Props) {
  const items = (nodeState.outputs?.items as AsrItem[] | undefined) ?? [];
  const status = nodeState.status;
  const [openIdx, setOpenIdx] = useState<number>(0);
  const [pendingRerun, setPendingRerun] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);

  // 所有 item 的精华稿未落盘草稿；relpath -> content
  // 用 ref 而非 state，防抖 timer 直接读最新值不触发 re-render
  const pendingDraftsRef = useRef<Map<string, string>>(new Map());
  const debounceTimer = useRef<number | null>(null);
  const [saveTick, setSaveTick] = useState(0); // 仅用于触发"保存中…"指示

  // 防抖 flush：onHighlightChange 触发后 600ms 内没新变化就落盘
  const onHighlightChange = useCallback(
    (relpath: string, content: string) => {
      pendingDraftsRef.current.set(relpath, content);
      setSaveTick((t) => t + 1);
      if (debounceTimer.current != null) window.clearTimeout(debounceTimer.current);
      debounceTimer.current = window.setTimeout(() => {
        void flushHighlightDrafts();
      }, 600);
    },
    [], // eslint-disable-line react-hooks/exhaustive-deps
  );

  async function flushHighlightDrafts(): Promise<void> {
    if (debounceTimer.current != null) {
      window.clearTimeout(debounceTimer.current);
      debounceTimer.current = null;
    }
    const entries = Array.from(pendingDraftsRef.current.entries());
    if (entries.length === 0) return;
    pendingDraftsRef.current.clear();
    setSaveTick((t) => t + 1);
    await Promise.all(
      entries.map(([relpath, content]) =>
        api.writeFile(jobId, relpath, content).catch((e) => {
          // 失败回填到 pending，下次 flush 重试
          pendingDraftsRef.current.set(relpath, content);
          console.error('[asr] save highlight failed', relpath, e);
        }),
      ),
    );
    setSaveTick((t) => t + 1);
  }

  async function doRun() {
    setActionBusy(true);
    try {
      await api.runNode(jobId, nodeDef.name);
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

  // 精华稿 tab 内 play 按钮触发：先 flush 当前所有 highlight 草稿，再启动 RW
  const [advanceBusy, setAdvanceBusy] = useState(false);
  async function doAdvanceToRw() {
    setAdvanceBusy(true);
    try {
      await flushHighlightDrafts();
      await api.runNode(jobId, 'rw');
      onAdvanced?.();
    } catch (e) {
      alert(`启动 RW 失败: ${(e as Error).message}`);
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

  const hasPending = pendingDraftsRef.current.size > 0;

  return (
    <div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--s-3)',
          margin: 'var(--s-3) 0',
        }}
      >
        <div className="section-h" style={{ margin: 0, flex: 1 }}>
          ASR 产物 · {items.length} 条视频{statusBadge}
          {hasPending && (
            <span className="dim-mono" style={{ marginLeft: 6, fontSize: 'var(--text-2xs)' }}>
              · 保存中…
            </span>
          )}
        </div>
        {renderActionBtn()}
      </div>

      {status === 'running' && (
        <div className="dim-mono" style={{ marginBottom: 'var(--s-3)' }}>
          {nodeState.progress || '正在跑…'}
        </div>
      )}
      {status === 'failed' && nodeState.error && (
        <div className="asr-error" style={{ marginBottom: 'var(--s-3)' }}>
          失败：{nodeState.error}
        </div>
      )}

      {items.length === 0 ? (
        <div className="dim-mono">
          {status === 'idle' || status === 'failed'
            ? '尚未跑过 ASR，点击右上「确认」启动。'
            : status === 'running' || status === 'queued'
              ? '产物生成中…'
              : '暂无产物'}
        </div>
      ) : (
        <div className="asr-acc">
          {items.map((item, idx) => (
            <AsrItemRow
              key={item.url || idx}
              jobId={jobId}
              item={item}
              expanded={openIdx === idx}
              onToggle={() => setOpenIdx(openIdx === idx ? -1 : idx)}
              onHighlightChange={onHighlightChange}
              onAdvanceToRw={doAdvanceToRw}
              advanceBusy={advanceBusy}
              // saveTick 仅作为 re-render 触发器，让"保存中"指示能反映最新 pending 状态
              saveTick={saveTick}
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
  onHighlightChange,
  onAdvanceToRw,
  advanceBusy,
}: {
  jobId: string;
  item: AsrItem;
  expanded: boolean;
  onToggle: () => void;
  onHighlightChange: (relpath: string, content: string) => void;
  onAdvanceToRw: () => void | Promise<void>;
  advanceBusy: boolean;
  saveTick: number;
}) {
  const [tab, setTab] = useState<TabKey>('transcript');
  const [cache, setCache] = useState<Partial<Record<TabKey, string>>>({});
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!expanded) return;
    if (cache[tab] !== undefined) return;
    const relpath = item[TABS.find((t) => t.key === tab)!.relpathKey] as
      | string
      | undefined;
    if (!relpath) {
      setCache((c) => ({ ...c, [tab]: '(无内容)' }));
      return;
    }
    setLoading(true);
    fetch(`/jobs/${jobId}/files/${relpath}`)
      .then((r) =>
        r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`)),
      )
      .then((text) => setCache((c) => ({ ...c, [tab]: text })))
      .catch((e) =>
        setCache((c) => ({ ...c, [tab]: `加载失败: ${(e as Error).message}` })),
      )
      .finally(() => setLoading(false));
  }, [expanded, tab, jobId, item, cache]);

  const failed = !!item.error;
  const body = cache[tab];
  const isHighlight = tab === 'highlight';

  return (
    <article className={`asr-item${expanded ? ' open' : ''}${failed ? ' failed' : ''}`}>
      <header className="asr-item-head" onClick={onToggle}>
        {expanded ? (
          <ChevronDown size={14} strokeWidth={1.7} />
        ) : (
          <ChevronRight size={14} strokeWidth={1.7} />
        )}
        <span className="asr-item-num mono">
          {String(item.index ?? '?').padStart(2, '0')}
        </span>
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
        {item.url && (
          <a
            className={`asr-item-url${item.title ? '' : ' primary'}`}
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
      </header>

      {expanded && !failed && (
        <div className="asr-item-body">
          <nav className="asr-tabs">
            {TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                className={`asr-tab${tab === t.key ? ' active' : ''}`}
                onClick={() => setTab(t.key)}
              >
                {t.label}
              </button>
            ))}
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
            {isHighlight && (
              <button
                type="button"
                className="btn sm icon-only primary"
                title="用此精华稿启动改写（RW）"
                disabled={advanceBusy || !body}
                onClick={onAdvanceToRw}
              >
                <Play size={12} strokeWidth={2} fill="currentColor" />
              </button>
            )}
          </nav>
          {isHighlight ? (
            <textarea
              className="code-pane editable"
              value={body ?? (loading ? '' : '')}
              placeholder={loading ? '加载中…' : ''}
              onChange={(e) => {
                const v = e.target.value;
                setCache((c) => ({ ...c, highlight: v }));
                if (item.highlight_relpath) onHighlightChange(item.highlight_relpath, v);
              }}
              rows={16}
            />
          ) : (
            <pre className="code-pane">{loading ? '加载中…' : body ?? ''}</pre>
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
