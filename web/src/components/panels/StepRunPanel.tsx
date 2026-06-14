// 通用命令步面板：用于没有专属面板的 engine 节点（渲染出片 agent 的 审片确认 / 渲染 / 下载）。
// 显示节点状态 + 进度/错误 + 一个主操作按钮（运行/重跑），并扫产物里的媒体相对路径给出下载链接。

import { useMemo, useState } from 'react';
import { Download, Play, RefreshCw } from 'lucide-react';

import { api } from '../../api/client';
import type { NodeState, PipelineNodeDef } from '../../api/types';
import { useToast } from '../Toast';

interface Props {
  jobId: string;
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
  // idle/failed 时按钮文案；done 时若给 rerunLabel 用之，否则隐藏运行按钮。
  actionLabel: string;
  rerunLabel?: string;
  // 运行成功后推进（切下一成员/下一 agent）。
  onAdvanced?: () => void;
  locked?: boolean;
}

const MEDIA_RE = /\.(mp4|mov|webm|mp3|wav|m4a|png|jpg|jpeg)$/i;

// 从 outputs 里递归捞出形似相对路径的媒体文件，给下载链接。
function collectMedia(outputs: Record<string, unknown>): string[] {
  const found: string[] = [];
  const walk = (v: unknown) => {
    if (typeof v === 'string') {
      if (MEDIA_RE.test(v) && !v.startsWith('http')) found.push(v);
    } else if (Array.isArray(v)) {
      v.forEach(walk);
    } else if (v && typeof v === 'object') {
      Object.values(v as Record<string, unknown>).forEach(walk);
    }
  };
  walk(outputs);
  return Array.from(new Set(found));
}

export function StepRunPanel({ jobId, nodeDef, nodeState, actionLabel, rerunLabel, onAdvanced, locked }: Props) {
  const { showToast } = useToast();
  const [busy, setBusy] = useState(false);
  const isOutput = nodeDef.kind === 'output';
  const media = useMemo(() => collectMedia(nodeState.outputs || {}), [nodeState.outputs]);

  async function run() {
    setBusy(true);
    try {
      await api.runNode(jobId, nodeDef.name);
      onAdvanced?.();
    } catch (e: unknown) {
      showToast(`运行「${nodeDef.label}」失败，请稍后再试`);
      console.error('[StepRunPanel] runNode 失败', nodeDef.name, e);
    } finally {
      setBusy(false);
    }
  }

  const showRun = !isOutput && (nodeState.status !== 'done' || !!rerunLabel);
  const btnLabel = nodeState.status === 'done' ? rerunLabel ?? actionLabel : actionLabel;

  return (
    <div className="panel step-run-panel">
      <p className="dim" style={{ fontSize: 'var(--text-sm)' }}>{nodeDef.description}</p>

      {media.length > 0 && (
        <div className="step-run-artifacts">
          {media.map((rel) => (
            <a key={rel} className="btn ghost sm" href={`/jobs/${jobId}/files/${rel}`} download>
              <Download size={13} strokeWidth={1.8} /> {rel.split('/').pop()}
            </a>
          ))}
        </div>
      )}

      {showRun && (
        <div className="panel-footer">
          <button className="btn primary" disabled={busy || locked} onClick={run}>
            {nodeState.status === 'done' ? (
              <RefreshCw size={13} strokeWidth={1.8} />
            ) : (
              <Play size={13} strokeWidth={1.8} />
            )}
            {busy ? '运行中…' : btnLabel}
          </button>
        </div>
      )}
      {isOutput && (
        <div className="panel-footer">
          <button className="btn primary" onClick={() => onAdvanced?.()}>完成</button>
        </div>
      )}
    </div>
  );
}
