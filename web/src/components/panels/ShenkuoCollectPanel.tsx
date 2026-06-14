// 沈括采集结果面板 —— 对齐 app ShenkuoCollectPanel（shenkuo_collect_panel.dart）：
// 封面+标题+话题+播放数据 / 工序状态点 / 提取文案（可展开）/ 声音素材（原声·人声·伴奏，
// 内联播放）/ 高赞评论（可折叠）/ 抠图素材（横滑）。不放原视频。
//
// 数据来自 asr 节点 outputs.collected（沈括 collect_one 产出），媒体相对路径走 /artifacts/files/。
// 采集自动触发（input 的「开始创作」→ runNode('asr')），本面板纯展示、无手动按钮；
// 音轨/抠图由后台第二趟补，字段后到（前端按字段在否渲染，缺失即不显示该区块）。

import { useState, type CSSProperties, type ReactNode } from 'react';
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  ExternalLink,
  Heart,
  MessageCircle,
  Music,
  Play,
  Quote,
  Share2,
  Star,
  User,
  XCircle,
} from 'lucide-react';

import type { NodeState, PipelineNodeDef, ShenkuoComment, ShenkuoEntry } from '../../api/types';

interface Props {
  jobId: string;
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
  // 推进到鬼谷子选题（由 AgentDrawer 映射）。
  onAdvanced?: () => void;
}

function fileUrl(rel?: string | null): string | undefined {
  return rel ? `/artifacts/files/${rel}` : undefined;
}

// 抖音口径数字：<1w 原样，<1亿 用 w，≥1亿 用 亿。
function shenkuoCount(n: number): string {
  if (n >= 1e8) return `${(n / 1e8).toFixed(1)}亿`;
  if (n >= 1e4) return `${(n / 1e4).toFixed(1)}w`;
  return String(n);
}

// 标题：剥掉 desc 内嵌的 #话题（下面有专门 chips，不重复）。
function cleanTitle(e: ShenkuoEntry): string {
  let t = e.desc || '';
  for (const tag of e.hashtags ?? []) t = t.replaceAll(`#${tag}`, '');
  const cleaned = t.split(/\s+/).filter(Boolean).join(' ').trim();
  return cleaned || e.desc || e.aweme_id || '?';
}

export function ShenkuoCollectPanel({ nodeState, onAdvanced }: Props) {
  const collected = (nodeState.outputs?.collected as ShenkuoEntry[] | undefined) ?? [];
  const status = nodeState.status;
  const ok = collected.filter((e) => !e.error);

  let hint: { tone: 'info' | 'error'; text: string } | null = null;
  if (collected.length === 0 && status === 'idle') {
    hint = { tone: 'info', text: '在「采集源」粘贴对标作品链接并点「开始创作」，沈括会自动采集。' };
  } else if (collected.length === 0 && (status === 'running' || status === 'queued')) {
    hint = { tone: 'info', text: nodeState.progress || '采集中…' };
  } else if (collected.length === 0 && status === 'done') {
    hint = { tone: 'info', text: '没有采到内容。' };
  }

  return (
    <div className="shenkuo-panel-root">
      {hint && <div className={`panel-hint panel-hint-${hint.tone}`}>{hint.text}</div>}

      {(status === 'running' || status === 'queued') && collected.length > 0 && (
        <div className="dim-mono" style={{ marginBottom: 'var(--s-3)' }}>
          {nodeState.progress || '采集中…（音轨/抠图会后台补齐）'}
        </div>
      )}

      {collected.length > 0 && (
        <div
          className={`section-h${status === 'running' || status === 'queued' ? ' loading' : ''}`}
          style={{ margin: 'var(--s-3) 0' }}
        >
          采集结果 · {ok.length} 条
        </div>
      )}

      {collected.map((e, i) => (
        <EntryCard key={e.aweme_id || e.url || i} entry={e} />
      ))}

      {ok.length > 0 && status === 'done' && onAdvanced && (
        <div style={{ marginTop: 'var(--s-4)', display: 'flex', justifyContent: 'flex-end' }}>
          <button className="btn primary sm" onClick={onAdvanced} title="完成采集，交鬼谷子选题">
            <Play size={12} strokeWidth={2} fill="currentColor" /> 交鬼谷子选题
          </button>
        </div>
      )}
    </div>
  );
}

const STAGES: Array<[string, string]> = [
  ['download', '下载'],
  ['transcribe', '转写'],
  ['audio', '声音'],
  ['cutout', '抠图'],
  ['comments', '评论'],
];

function EntryCard({ entry }: { entry: ShenkuoEntry }) {
  const [textOpen, setTextOpen] = useState(false);
  const [audioOpen, setAudioOpen] = useState(false);
  const [commentsOpen, setCommentsOpen] = useState(false);

  if (entry.error) {
    return (
      <article className="shenkuo-entry failed" style={cardStyle}>
        <div style={{ color: 'var(--danger, #e5484d)', fontSize: 'var(--text-sm)' }}>
          作品 {entry.index ?? '?'} 采集失败：{entry.error}
        </div>
        {entry.url && <UrlLink url={entry.url} />}
      </article>
    );
  }

  const cover = fileUrl(entry.cover);
  const hashtags = entry.hashtags ?? [];
  const audio = entry.audio ?? {};
  const audioRows = [
    { key: 'original', label: '原声', note: '完整音轨' },
    { key: 'vocals', label: '人声·口播', note: 'Demucs 分离' },
    { key: 'bgm', label: '伴奏·BGM', note: 'Demucs 分离' },
  ].filter((r) => audio[r.key]);
  const comments = entry.top_comments ?? [];
  const cutouts = (entry.cutouts ?? []).map(fileUrl).filter(Boolean) as string[];

  return (
    <article className="shenkuo-entry" style={cardStyle}>
      {/* 封面 + 标题 + 话题 + 播放数据 */}
      <div style={{ display: 'flex', gap: 'var(--s-3)', alignItems: 'flex-start' }}>
        {cover && (
          <img
            src={cover}
            alt=""
            style={{ width: 84, height: 112, objectFit: 'cover', borderRadius: 8, flexShrink: 0 }}
          />
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 'var(--text-sm)', lineHeight: 1.4 }}>{cleanTitle(entry)}</div>
          {entry.author && (
            <div className="dim-mono" style={{ marginTop: 2 }}>
              <User size={11} strokeWidth={1.7} style={{ verticalAlign: '-1px', marginRight: 2 }} />
              {entry.author}
            </div>
          )}
          {hashtags.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6 }}>
              {hashtags.map((t) => (
                <span key={t} style={tagStyle}>#{t}</span>
              ))}
            </div>
          )}
          <StatsLine stats={entry.stats ?? {}} digg={entry.digg} />
        </div>
      </div>

      {/* 工序状态点 */}
      <StageDots status={entry.status} />

      {/* 提取文案 */}
      {entry.text && (
        <div style={{ marginTop: 'var(--s-3)' }}>
          <SectionHead
            icon={<Quote size={13} />}
            label={`提取文案 · ${entry.text.length} 字`}
            right={
              <button type="button" style={linkBtnStyle} onClick={() => setTextOpen((v) => !v)}>
                {textOpen ? '收起' : '展开全文'}
              </button>
            }
          />
          <div
            style={{
              fontSize: 'var(--text-sm)',
              lineHeight: 1.6,
              color: 'var(--ink-2)',
              ...(textOpen ? {} : clamp6),
            }}
          >
            {entry.text}
          </div>
        </div>
      )}

      {/* 声音素材（原声/人声/伴奏，内联播放） */}
      {audioRows.length > 0 && (
        <Disclosure
          open={audioOpen}
          onToggle={() => setAudioOpen((v) => !v)}
          icon={<Music size={13} />}
          label={`声音素材 · ${audioRows.length} 轨`}
        >
          {audioRows.map((r) => (
            <div key={r.key} style={{ marginBottom: 8 }}>
              <div className="dim-mono" style={{ marginBottom: 2 }}>
                {r.label} · {r.note}
              </div>
              <audio controls preload="none" src={fileUrl(audio[r.key])} style={{ width: '100%', height: 32 }} />
            </div>
          ))}
        </Disclosure>
      )}

      {/* 高赞评论 */}
      {comments.length > 0 && (
        <Disclosure
          open={commentsOpen}
          onToggle={() => setCommentsOpen((v) => !v)}
          icon={<MessageCircle size={13} />}
          label={`高赞评论 · Top ${comments.length}`}
        >
          {comments.map((c, i) => (
            <CommentRow key={i} idx={i} c={c} />
          ))}
        </Disclosure>
      )}

      {/* 抠图素材（横滑，点击新标签看大图） */}
      {cutouts.length > 0 && (
        <div style={{ marginTop: 'var(--s-3)' }}>
          <div className="dim-mono" style={{ marginBottom: 4 }}>抠图素材 · {cutouts.length}</div>
          <div style={{ display: 'flex', gap: 6, overflowX: 'auto', paddingBottom: 4 }}>
            {cutouts.map((u, i) => (
              <a key={i} href={u} target="_blank" rel="noopener noreferrer" style={{ flexShrink: 0 }}>
                <img src={u} alt="" style={{ height: 54, borderRadius: 6, display: 'block' }} />
              </a>
            ))}
          </div>
        </div>
      )}

      {entry.url && <UrlLink url={entry.url} />}
    </article>
  );
}

function StatsLine({ stats, digg }: { stats: Record<string, number>; digg?: number }) {
  const items: Array<{ Icon: typeof Heart; n: number; color: string }> = [];
  const d = stats.digg ?? digg;
  if (d != null) items.push({ Icon: Heart, n: d, color: '#e5484d' });
  if (stats.comment != null) items.push({ Icon: MessageCircle, n: stats.comment, color: 'var(--ink-3)' });
  if (stats.share != null) items.push({ Icon: Share2, n: stats.share, color: 'var(--ink-3)' });
  if (stats.collect != null) items.push({ Icon: Star, n: stats.collect, color: '#f5a623' });
  if (items.length === 0) return null;
  return (
    <div style={{ display: 'flex', gap: 10, marginTop: 6 }}>
      {items.map(({ Icon, n, color }, i) => (
        <span
          key={i}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 'var(--text-xs)', color, fontWeight: 600 }}
        >
          <Icon size={11} /> {shenkuoCount(n)}
        </span>
      ))}
    </div>
  );
}

function StageDots({ status }: { status?: Record<string, string> }) {
  const s = status ?? {};
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 'var(--s-3)' }}>
      {STAGES.map(([key, label]) => {
        const v = s[key];
        const { Icon, color } = !v
          ? { Icon: Circle, color: 'var(--ink-3)' }
          : v === 'ok' || v === 'cached'
            ? { Icon: CheckCircle2, color: '#3aa55d' }
            : { Icon: XCircle, color: '#e5484d' };
        return (
          <span
            key={key}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 'var(--text-2xs)', color }}
            title={v ? `${label}: ${v}` : `${label}: 未做`}
          >
            <Icon size={11} /> {label}
          </span>
        );
      })}
    </div>
  );
}

function SectionHead({ icon, label, right }: { icon: ReactNode; label: string; right?: ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 4,
        marginBottom: 6,
        fontSize: 'var(--text-xs)',
        color: 'var(--ink-2)',
        fontWeight: 600,
      }}
    >
      {icon}
      <span style={{ flex: 1 }}>{label}</span>
      {right}
    </div>
  );
}

function Disclosure({
  open,
  onToggle,
  icon,
  label,
  children,
}: {
  open: boolean;
  onToggle: () => void;
  icon: ReactNode;
  label: string;
  children: ReactNode;
}) {
  return (
    <div style={{ marginTop: 'var(--s-3)' }}>
      <button
        type="button"
        onClick={onToggle}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 4,
          width: '100%',
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          padding: 0,
          color: 'var(--ink-2)',
          fontSize: 'var(--text-xs)',
          fontWeight: 500,
        }}
      >
        {icon}
        <span style={{ flex: 1, textAlign: 'left' }}>{label}</span>
        {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
      </button>
      {open && <div style={{ marginTop: 8 }}>{children}</div>}
    </div>
  );
}

function CommentRow({ idx, c }: { idx: number; c: ShenkuoComment }) {
  return (
    <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
      <span
        style={{
          width: 16,
          textAlign: 'right',
          fontWeight: 700,
          fontSize: 'var(--text-2xs)',
          color: idx < 3 ? 'var(--accent)' : 'var(--ink-3)',
        }}
      >
        {idx + 1}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 'var(--text-sm)', color: 'var(--ink-2)' }}>{c.text}</div>
        <div style={{ display: 'flex', gap: 8, marginTop: 2, fontSize: 'var(--text-2xs)', color: 'var(--ink-3)' }}>
          {c.nickname && <span>{c.nickname}</span>}
          {c.ip && <span>{c.ip}</span>}
          <span style={{ flex: 1 }} />
          {c.digg != null && (
            <span style={{ color: '#e5484d', display: 'inline-flex', alignItems: 'center', gap: 2 }}>
              <Heart size={10} /> {shenkuoCount(c.digg)}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function UrlLink({ url }: { url: string }) {
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="dim-mono"
      style={{ display: 'inline-flex', alignItems: 'center', gap: 4, marginTop: 'var(--s-3)', fontSize: 'var(--text-2xs)' }}
    >
      <ExternalLink size={11} /> 原作品
    </a>
  );
}

const cardStyle: CSSProperties = {
  padding: 14,
  border: '1px solid var(--line, rgba(0,0,0,0.08))',
  borderRadius: 10,
  marginBottom: 'var(--s-3)',
};

const tagStyle: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 2,
  fontSize: 'var(--text-2xs)',
  fontFamily: 'var(--font-mono)',
  color: 'var(--accent)',
  background: 'var(--accent-tint)',
  padding: '1px 6px',
  borderRadius: 'var(--r-pill)',
};

const linkBtnStyle: CSSProperties = {
  background: 'none',
  border: 'none',
  cursor: 'pointer',
  padding: 0,
  color: 'var(--accent)',
  fontSize: 'var(--text-xs)',
  fontWeight: 600,
};

const clamp6: CSSProperties = {
  display: '-webkit-box',
  WebkitLineClamp: 6,
  WebkitBoxOrient: 'vertical',
  overflow: 'hidden',
};
