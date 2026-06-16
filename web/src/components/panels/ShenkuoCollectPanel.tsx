// 沈括采集结果面板 —— 对齐 app ShenkuoCollectPanel（shenkuo_collect_panel.dart）：
// 封面+标题+话题+播放数据 / 工序状态点 / 提取文案（可展开）/ 声音素材（原声·人声·伴奏，
// 内联播放）/ 高赞评论（可折叠）/ 抠图素材（横滑）。不放原视频。
//
// 数据来自 asr 节点 outputs.collected（沈括 collect_one 产出），媒体相对路径走 /artifacts/files/。
// 采集在进画布时自动触发（JobCanvasPage 进画布即 runNode('asr')，后端有采集缓存），本面板纯展示、无手动按钮；
// 音轨/抠图由后台第二趟补，字段后到（前端按字段在否渲染，缺失即不显示该区块）。

import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import {
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Circle,
  ExternalLink,
  Heart,
  Lightbulb,
  MessageCircle,
  Music,
  Play,
  Quote,
  Share2,
  Star,
  User,
  X,
  XCircle,
} from 'lucide-react';

import type { GuiguziItem, NodeState, PipelineNodeDef, ShenkuoComment, ShenkuoEntry } from '../../api/types';
import { GUIGUZI_MAX_ITEMS, guiguziItemsStorageKey } from '../../config/agents';
import { DurationBadge } from '../WorkCards';

// 沈括「备选题评论」选择态：选中的高赞评论（连同所属作品提取文案）存 localStorage（按 jobId），
// 鬼谷子面板读它出选题。沈括/鬼谷子是不同 agent 抽屉、各自挂载，用 localStorage 跨面板传递。
interface CommentSelection {
  isSelected: (text: string, comment: string) => boolean;
  toggle: (text: string, comment: string, digg?: number) => void;
  count: number;
  full: boolean;
}

function useCommentSelection(jobId: string): CommentSelection {
  const key = guiguziItemsStorageKey(jobId);
  const [items, setItems] = useState<GuiguziItem[]>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw ? (JSON.parse(raw) as GuiguziItem[]) : [];
    } catch {
      return [];
    }
  });

  const persist = useCallback(
    (next: GuiguziItem[]) => {
      setItems(next);
      try {
        localStorage.setItem(key, JSON.stringify(next));
      } catch {
        /* localStorage 不可用时忽略 */
      }
    },
    [key],
  );

  const isSelected = useCallback(
    (text: string, comment: string) => items.some((it) => it.text === text && it.comment === comment),
    [items],
  );

  const toggle = useCallback(
    (text: string, comment: string, digg?: number) => {
      const exists = items.some((it) => it.text === text && it.comment === comment);
      if (exists) {
        persist(items.filter((it) => !(it.text === text && it.comment === comment)));
      } else if (items.length < GUIGUZI_MAX_ITEMS) {
        persist([...items, { text, comment, digg }]);
      }
      // 超额（>=5 且未选中）静默忽略：UI 上 full 态把图标置灰
    },
    [items, persist],
  );

  return { isSelected, toggle, count: items.length, full: items.length >= GUIGUZI_MAX_ITEMS };
}

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

export function ShenkuoCollectPanel({ jobId, nodeState, onAdvanced }: Props) {
  const collected = (nodeState.outputs?.collected as ShenkuoEntry[] | undefined) ?? [];
  const status = nodeState.status;
  const ok = collected.filter((e) => !e.error);
  const selection = useCommentSelection(jobId);

  let hint: { tone: 'info' | 'error'; text: string } | null = null;
  if (collected.length === 0 && status === 'idle') {
    hint = { tone: 'info', text: '采集会自动开始，请稍候…' };
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

      {collected.length > 0 && (
        <div className="dim-mono" style={{ margin: '0 0 var(--s-2)', display: 'flex', alignItems: 'center', gap: 4 }}>
          <Lightbulb size={11} strokeWidth={1.8} style={{ color: 'var(--accent)' }} />
          点评论选作选题方案，已选 {selection.count}/{GUIGUZI_MAX_ITEMS}（交鬼谷子出选题）
        </div>
      )}

      {collected.map((e, i) => (
        <EntryCard key={e.aweme_id || e.url || i} entry={e} selection={selection} />
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

function EntryCard({ entry, selection }: { entry: ShenkuoEntry; selection: CommentSelection }) {
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
    // 一条作品 = 一组独立卡片：作品信息 / 提取文案 / 声音素材 / 高赞评论 / 抠图各占一张卡，
    // 每张自带边框、独立区域，分块更清晰（不再塞进同一张大卡）。
    <div className="shenkuo-entry-group" style={{ marginBottom: 'var(--s-4)' }}>
      {/* 卡片：作品信息（封面 + 标题 + 话题 + 播放数据 + 工序状态点） */}
      <article className="shenkuo-entry shenkuo-card" style={cardStyle}>
        <div style={{ display: 'flex', gap: 'var(--s-3)', alignItems: 'flex-start' }}>
          {cover && (
            <div className="shenkuo-cover-wrap">
              <img
                src={cover}
                alt=""
                style={{ width: 84, height: 112, objectFit: 'cover', borderRadius: 8, display: 'block' }}
              />
              <DurationBadge seconds={entry.duration} />
            </div>
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
        {/* 工序状态点一行，原作品链接靠该行最右 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-3)' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <StageDots status={entry.status} />
          </div>
          {entry.url && <UrlLink url={entry.url} />}
        </div>
      </article>

      {/* 卡片：提取文案 */}
      {entry.text && (
        <article className="shenkuo-card" style={cardStyle}>
          <SectionHead
            icon={<Quote size={13} />}
            label={`提取文案 · ${entry.text.length} 字`}
            right={<CardToggle open={textOpen} onToggle={() => setTextOpen((v) => !v)} openText="收起" closedText="展开全文" />}
          />
          <div
            style={{
              fontSize: 'var(--text-sm)',
              lineHeight: 1.7,
              color: 'var(--ink-2)',
              // 清洗稿本身是空行分段的纯文本；不保留换行会被 HTML 折叠成一坨，pre-wrap 还原段落
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              ...(textOpen ? {} : clamp12),
            }}
          >
            {entry.text}
          </div>
        </article>
      )}

      {/* 卡片：声音素材（原声/人声/伴奏，内联播放） */}
      {audioRows.length > 0 && (
        <article className="shenkuo-card" style={cardStyle}>
          <SectionHead
            icon={<Music size={13} />}
            label={`声音素材 · ${audioRows.length} 轨`}
            right={<CardToggle open={audioOpen} onToggle={() => setAudioOpen((v) => !v)} />}
          />
          {audioOpen &&
            audioRows.map((r) => (
              <div key={r.key} style={{ marginBottom: 8 }}>
                <div className="dim-mono" style={{ marginBottom: 2 }}>
                  {r.label} · {r.note}
                </div>
                <audio controls preload="none" src={fileUrl(audio[r.key])} style={{ width: '100%', height: 32 }} />
              </div>
            ))}
        </article>
      )}

      {/* 卡片：高赞评论 —— 默认展示前 3 条，超过 3 条才给"展开全部"按钮 */}
      {comments.length > 0 && (
        <article className="shenkuo-card" style={{ ...cardStyle, position: 'relative' }}>
          <SectionHead
            icon={<MessageCircle size={13} />}
            label={`高赞评论 · Top ${comments.length}`}
            right={
              comments.length > 3 ? (
                <CardToggle
                  open={commentsOpen}
                  onToggle={() => setCommentsOpen((v) => !v)}
                  closedText={`展开全部 ${comments.length}`}
                />
              ) : undefined
            }
          />
          {/* 每条评论是一个可交互行：hover 放大 + 浅遮罩 + 选题图标，点击选作选题方案 */}
          <div className="shenkuo-comment-list">
            {(commentsOpen ? comments : comments.slice(0, 3)).map((c, i) => (
              <CommentRow
                key={i}
                idx={i}
                c={c}
                entryText={entry.text || ''}
                selection={selection}
              />
            ))}
          </div>
          {/* 展开/收起角标：骑在卡片下边框中点（绝对定位 + translateY(50%)，同色底切断边框） */}
          {comments.length > 3 && (
            <button
              type="button"
              className="dim-mono"
              onClick={() => setCommentsOpen((v) => !v)}
              style={{
                position: 'absolute',
                left: '50%',
                bottom: 0,
                transform: 'translate(-50%, 50%)',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 3,
                padding: '2px 10px',
                border: 'none',
                background: 'var(--bg-surface)',
                cursor: 'pointer',
                fontSize: 'var(--text-2xs)',
                lineHeight: 1.4,
                color: '#e5484d',
              }}
            >
              {commentsOpen ? '收起' : `展开全部 ${comments.length}`}
              {commentsOpen ? <ChevronDown size={12} style={{ transform: 'rotate(180deg)' }} /> : <ChevronDown size={12} />}
            </button>
          )}
        </article>
      )}

      {/* 卡片：抠图素材（网格 + 展开收起，点击进遮罩相册左右切换） */}
      {cutouts.length > 0 && (
        <article className="shenkuo-card" style={cardStyle}>
          <div className="dim-mono" style={{ marginBottom: 6 }}>抠图素材 · {cutouts.length}</div>
          <CutoutGrid urls={cutouts} />
        </article>
      )}
    </div>
  );
}

// 抠图网格：固定列数，默认只显示第一行（收起），展开显示全部；点击进遮罩相册。
const CUTOUT_COLS = 5;

function CutoutGrid({ urls }: { urls: string[] }) {
  const [open, setOpen] = useState(false);
  const [lightbox, setLightbox] = useState<number | null>(null);
  const hasMore = urls.length > CUTOUT_COLS;
  const shown = open ? urls : urls.slice(0, CUTOUT_COLS);
  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${CUTOUT_COLS}, 1fr)`, gap: 6 }}>
        {shown.map((u, i) => (
          <button
            key={i}
            type="button"
            onClick={() => setLightbox(i)}
            title="点击看大图"
            style={{ padding: 0, border: 'none', background: 'none', cursor: 'pointer', lineHeight: 0 }}
          >
            <img
              src={u}
              alt=""
              loading="lazy"
              style={{ width: '100%', aspectRatio: '1 / 1', objectFit: 'cover', borderRadius: 6, display: 'block' }}
            />
          </button>
        ))}
      </div>
      {hasMore && (
        <button
          type="button"
          className="dim-mono"
          onClick={() => setOpen((v) => !v)}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 3,
            marginTop: 8,
            padding: 0,
            border: 'none',
            background: 'none',
            cursor: 'pointer',
            fontSize: 'var(--text-2xs)',
          }}
        >
          {open ? <ChevronDown size={12} style={{ transform: 'rotate(180deg)' }} /> : <ChevronDown size={12} />}
          {open ? '收起' : `展开全部 ${urls.length}`}
        </button>
      )}
      {lightbox != null && (
        <CutoutLightbox urls={urls} startIndex={lightbox} onClose={() => setLightbox(null)} />
      )}
    </>
  );
}

// 抠图遮罩相册：fixed 遮罩 + 左右切换 + 底部缩略图条，复用 image gallery 的 .ig-* 样式。
function CutoutLightbox({ urls, startIndex, onClose }: { urls: string[]; startIndex: number; onClose: () => void }) {
  const clamp = (i: number) => Math.max(0, Math.min(urls.length - 1, i));
  const [cur, setCur] = useState(() => clamp(startIndex));

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') setCur((c) => Math.max(0, c - 1));
      else if (e.key === 'ArrowRight') setCur((c) => Math.min(urls.length - 1, c + 1));
      else if (e.key === 'Escape') { e.stopPropagation(); onClose(); }
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [urls.length, onClose]);

  // 门户挂到 body：脱离抽屉(可能带 transform)的包含块，保证 fixed 遮罩铺满视口
  return createPortal(
    <div className="ig-backdrop" onClick={onClose}>
      <div className="ig" onClick={(e) => e.stopPropagation()}>
        <button className="btn sm icon-only ghost ig-close" onClick={onClose} title="关闭 (Esc)">
          <X size={16} strokeWidth={1.7} />
        </button>

        <div className="ig-stage">
          <button
            type="button"
            className="ig-nav"
            disabled={cur <= 0}
            onClick={() => setCur((c) => Math.max(0, c - 1))}
            title="上一张 (←)"
          >
            <ChevronLeft size={24} strokeWidth={1.8} />
          </button>

          <div className="ig-main">
            <img src={urls[cur]} alt="" draggable={false} />
            <span className="ig-scene-id mono">{cur + 1} / {urls.length}</span>
          </div>

          <button
            type="button"
            className="ig-nav"
            disabled={cur >= urls.length - 1}
            onClick={() => setCur((c) => Math.min(urls.length - 1, c + 1))}
            title="下一张 (→)"
          >
            <ChevronRight size={24} strokeWidth={1.8} />
          </button>
        </div>

        <div className="ig-filmstrip">
          {urls.map((u, i) => (
            <button
              key={i}
              type="button"
              className={`ig-thumb${i === cur ? ' active' : ''}`}
              onClick={() => setCur(i)}
            >
              <img src={u} alt="" loading="lazy" draggable={false} />
            </button>
          ))}
        </div>
      </div>
    </div>,
    document.body,
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

function SectionHead({ icon, label, right, mb = 6 }: { icon: ReactNode; label: string; right?: ReactNode; mb?: number }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 4,
        marginBottom: mb,
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

// 卡片头部右侧的展开/收起按钮：文字 + chevron 指示展开态。
function CardToggle({
  open,
  onToggle,
  openText = '收起',
  closedText = '展开',
}: {
  open: boolean;
  onToggle: () => void;
  openText?: string;
  closedText?: string;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      style={{ ...linkBtnStyle, display: 'inline-flex', alignItems: 'center', gap: 2 }}
    >
      {open ? openText : closedText}
      {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
    </button>
  );
}

// 一条评论 = 一个可交互行（内部 3 列：序号 / 内容 / 点赞）。
// hover 放大 + 浅遮罩 + 选题图标；点击把它（连同所属作品提取文案）选作鬼谷子选题方案。
// 行内 3 列与底部虚线分割线（左实右淡的渐变虚线）用 SCSS（见 _node-panels.scss）。
function CommentRow({
  idx,
  c,
  entryText,
  selection,
}: {
  idx: number;
  c: ShenkuoComment;
  entryText: string;
  selection: CommentSelection;
}) {
  const top = idx < 3;
  const comment = c.text || '';
  const selected = comment ? selection.isSelected(entryText, comment) : false;
  // 已选满且当前未选中 → 不可再选（图标置灰）
  const disabled = !selected && selection.full;

  const tip = selected
    ? '已选作选题参考 · 点击取消'
    : disabled
      ? `已选满 ${GUIGUZI_MAX_ITEMS} 条`
      : '选作选题参考';

  return (
    <div
      className={`shenkuo-comment-row${selected ? ' selected' : ''}${disabled ? ' disabled' : ''}`}
      role="button"
      tabIndex={0}
      title={tip}
      onClick={() => comment && selection.toggle(entryText, comment, c.digg)}
      onKeyDown={(e) => {
        if ((e.key === 'Enter' || e.key === ' ') && comment) {
          e.preventDefault();
          selection.toggle(entryText, comment, c.digg);
        }
      }}
    >
      {/* 圆标签序号：前三名用 accent 实心，其余用浅底；已选则用 accent 实心打勾感 */}
      <span className={`shenkuo-comment-idx${top || selected ? ' top' : ''}`}>{idx + 1}</span>
      {/* 内容列：隐藏作者昵称与归属 IP，仅显示文案，自动换行 */}
      <div style={{ minWidth: 0, fontSize: 'var(--text-sm)', color: 'var(--ink-2)', overflowWrap: 'anywhere' }}>
        {comment}
      </div>
      {/* 点赞量列：水平垂直居中 */}
      <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 2, color: '#e5484d', fontSize: 'var(--text-2xs)', whiteSpace: 'nowrap' }}>
        {c.digg != null && (
          <>
            <Heart size={10} /> {shenkuoCount(c.digg)}
          </>
        )}
      </span>
      {/* hover/selected 遮罩 + 选题图标（pointer-events:none，点击穿透到行本身） */}
      <div className="shenkuo-comment-overlay">
        <Lightbulb size={13} strokeWidth={2} fill={selected ? 'currentColor' : 'none'} />
        {selected ? '已选' : '选题参考'}
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

// 折叠态提取文案高度：12 行（在原 6 行基础上翻倍）
const clamp12: CSSProperties = {
  display: '-webkit-box',
  WebkitLineClamp: 12,
  WebkitBoxOrient: 'vertical',
  overflow: 'hidden',
};
