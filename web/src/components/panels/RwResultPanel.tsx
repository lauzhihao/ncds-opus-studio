// rw 节点产物面板：两个模型并列 tab，每 tab 一个可编辑 textarea + 自动防抖保存。
// 分割线右侧顶层按钮（开始/停止/重新执行）操作整体；tab 内有"重写本模型"和"用此模型 · 下一步"。
// "下一步"会先 flush 草稿、调 selectRwModel 拷贝 episode、再 runNode('image')。

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  CheckCircle2,
  Play,
  RefreshCw,
  Square,
} from 'lucide-react';

import { api } from '../../api/client';
import type { NodeState, PipelineNodeDef, RwDraft } from '../../api/types';
import { ConfirmDialog } from '../ConfirmDialog';

interface Props {
  jobId: string;
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
  // "用此模型 · 下一步"成功触发 image 后由父组件关闭抽屉，露出 rw→image 脉冲线
  onAdvanced?: () => void;
}

const NEXT_NODE = 'image';

export function RwResultPanel({ jobId, nodeDef, nodeState, onAdvanced }: Props) {
  const drafts = (nodeState.outputs?.drafts as RwDraft[] | undefined) ?? [];
  const selectedModelId =
    (nodeState.outputs?.selected_model_id as string | null | undefined) ?? null;
  const status = nodeState.status;

  const [tab, setTab] = useState<string>(drafts[0]?.model_id ?? '');
  const [cache, setCache] = useState<Record<string, string>>({});
  const [loadingTab, setLoadingTab] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [rewriteBusy, setRewriteBusy] = useState(false);
  const [pendingRerun, setPendingRerun] = useState(false);

  // 当 drafts 变化（如 done 第一次回填）但 tab 未设，初始化到第一个
  useEffect(() => {
    if (!tab && drafts.length > 0) setTab(drafts[0].model_id);
  }, [drafts, tab]);

  // 切 tab 时若没缓存就 fetch
  useEffect(() => {
    if (!tab) return;
    if (cache[tab] !== undefined) return;
    const d = drafts.find((x) => x.model_id === tab);
    if (!d) return;
    setLoadingTab(tab);
    fetch(`/jobs/${jobId}/files/${d.draft_relpath}`)
      .then((r) =>
        r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`)),
      )
      .then((text) => setCache((c) => ({ ...c, [tab]: text })))
      .catch((e) =>
        setCache((c) => ({ ...c, [tab]: `加载失败: ${(e as Error).message}` })),
      )
      .finally(() => setLoadingTab(null));
  }, [tab, jobId, drafts, cache]);

  // 自动保存：modelId -> text
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
        if (!d) return Promise.resolve();
        return api.writeFile(jobId, d.draft_relpath, text).catch((e) => {
          pendingRef.current.set(modelId, text);
          console.error('[rw] save draft failed', modelId, e);
        });
      }),
    );
    forceRender((x) => x + 1);
  }, [drafts, jobId]);

  const onChangeDraft = useCallback(
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

  async function doRewriteTab() {
    if (!tab) return;
    setRewriteBusy(true);
    try {
      await api.rewriteRwModel(jobId, tab);
      // 清缓存让 useEffect 重新 fetch；同时清掉该 tab 的 pending（被覆写了）
      pendingRef.current.delete(tab);
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
          title="清空两个模型及下游产物后重新跑"
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
  const hasPending = pendingRef.current.size > 0;
  const body = cache[tab];
  const loading = loadingTab === tab;

  return (
    <div className="rw-panel-root">
      <div className="rw-panel-header">
        <div className="section-h" style={{ margin: 0, flex: 1 }}>
          RW 改写 · {drafts.length} 个模型{statusBadge}
          {hasPending && (
            <span className="dim-mono" style={{ marginLeft: 6, fontSize: 'var(--text-2xs)' }}>
              · 保存中…
            </span>
          )}
        </div>
        {renderActionBtn()}
      </div>

      {status === 'running' && (
        <div className="dim-mono">{nodeState.progress || '正在跑…'}</div>
      )}
      {status === 'failed' && nodeState.error && (
        <div className="asr-error">失败：{nodeState.error}</div>
      )}

      {drafts.length === 0 ? (
        <div className="dim-mono">
          {status === 'idle' || status === 'failed'
            ? '尚未跑过 RW，点击右上「开始改写」启动。'
            : status === 'running' || status === 'queued'
              ? '产物生成中…'
              : '暂无产物'}
        </div>
      ) : (
        <>
          <nav className="asr-tabs">
            {drafts.map((d) => (
              <button
                key={d.model_id}
                type="button"
                className={`asr-tab${tab === d.model_id ? ' active' : ''}`}
                onClick={() => setTab(d.model_id)}
              >
                {d.label}
                {selectedModelId === d.model_id && (
                  <CheckCircle2
                    size={11}
                    strokeWidth={2}
                    style={{ marginLeft: 4, color: 'var(--accent)', verticalAlign: '-1px' }}
                  />
                )}
              </button>
            ))}
          </nav>
          <textarea
            key={tab}
            className="code-pane editable rw-textarea"
            value={body ?? ''}
            placeholder={loading ? '加载中…' : ''}
            onChange={(e) => onChangeDraft(tab, e.target.value)}
          />
          <div className="rw-panel-actions">
            <button
              className="btn sm"
              disabled={rewriteBusy || actionBusy || !body}
              onClick={doRewriteTab}
            >
              <RefreshCw size={11} strokeWidth={1.8} />
              {rewriteBusy ? ' 重写中…' : ' 重写'}
            </button>
            <div style={{ flex: 1 }} />
            <button
              className="btn primary sm"
              disabled={actionBusy || status !== 'done' || !tab}
              onClick={doAdvance}
              title="把此模型的 episode 作为下游 image 入口"
            >
              <CheckCircle2 size={12} strokeWidth={2} /> 下一步
            </button>
          </div>
        </>
      )}

      <ConfirmDialog
        open={pendingRerun}
        title="重新执行 RW？"
        message={<>会清空 RW 两个模型 draft 以及所有下游节点的状态与产物，然后重新跑。</>}
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
