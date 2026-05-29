// input 节点抽屉：粘抖音分享文本 → onChange 实时正则解析 → 卡片列表 → 「开始流程」。
//
// 抖音分享原文示例：
//   2.00 07/07 kpD:/ M@j.cN :3pm 地球𝗻𝗽𝗰的在校生活🌎 # 校园vlog# 女高# 08# 双子
//   # 2026高考  https://v.douyin.com/smN8ZVkEzvM/  复制此链接，打开Dou音搜索…
//
// 一段原文里就含作者(URL 前最后一个非 ASCII token) / hashtag (#xxx) / URL。
// 不调任何接口，前端正则一次抽完。

import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ExternalLink,
  Film,
  Hash,
  Link2,
  Lock,
  Play,
  Plus,
  Trash2,
  User,
} from 'lucide-react';

import { api } from '../../api/client';
import { useToast } from '../Toast';
import type { NodeState, ParsedShare, PipelineNodeDef } from '../../api/types';

interface Props {
  jobId: string;
  nodeDef: PipelineNodeDef;
  nodeState: NodeState;
  // 兄弟 asr 节点的 status；done/running/queued 时本 panel 锁编辑，
  // 防止用户在已下游产物的 job 上换 URL（要换就派生新 job）。
  asrStatus?: NodeState['status'];
  onStarted: () => void;
}

const HINT = '请输入抖音分享链接（支持多个）';

const URL_RE = /https?:\/\/[^\s)）」』】]+/g;
const URL_TAIL_TRIM = /[，。、,.!?;；:：）\]\}>"'"'’」』】»…]+$/u;
const TAG_RE = /#\s*([^\s#，,。、]+)/g;
const NON_ASCII = /[^\x00-\x7F]/;
// 手机分享：看看【xxx 的作品】title 后面是 url
const PHONE_RE = /【([^】]+?)】([\s\S]*?)(?=#|$)/;

// 从整段原文里抽出多条 ParsedShare，兼容三种形态：
//   1) 桌面分享     "杂数据 作者 #t1 #t2 URL ..."
//   2) 手机分享     "看看【作者的作品】title URL ..."
//   3) 纯 URL       只有 URL，author/title/tags 都识别不到
// 识别不到的字段返回 undefined / 空数组，让 UI 自己决定是否隐藏。
export function parseShares(raw: string): ParsedShare[] {
  if (!raw) return [];
  const matches: { url: string; start: number; end: number }[] = [];
  for (const m of raw.matchAll(URL_RE)) {
    const u = m[0].replace(URL_TAIL_TRIM, '').trim();
    if (!u) continue;
    matches.push({ url: u, start: m.index!, end: m.index! + u.length });
  }
  const result: ParsedShare[] = [];
  const seen = new Set<string>();
  let prevEnd = 0;
  for (const { url, start, end } of matches) {
    if (seen.has(url)) { prevEnd = end; continue; }
    seen.add(url);
    const before = raw.slice(prevEnd, start);

    // tags
    const tags: string[] = [];
    for (const tm of before.matchAll(TAG_RE)) tags.push(tm[1].trim());

    let author: string | undefined;
    let title: string | undefined;

    // 1) 手机格式：【xxx 的作品】title
    const phone = before.match(PHONE_RE);
    if (phone) {
      let a = phone[1].trim();
      if (a.endsWith('的作品')) a = a.slice(0, -3).trim();
      if (a) author = a;
      const tPart = phone[2]
        .trim()
        .replace(/^[，,。、\s:：]+|[，,。、\s:：]+$/gu, '');
      if (tPart) title = tPart;
    }

    // 2) 桌面格式作者兜底：去掉 【...】 + hashtag 后，取最后一个 token，需含非 ASCII
    if (!author) {
      const cleaned = before
        .replace(/【[^】]*】/g, '')
        .replace(/#\s*[^\s#]+/g, '');
      const tokens = cleaned.trim().split(/\s+/).filter(Boolean);
      const last = tokens[tokens.length - 1] ?? '';
      if (last.length >= 2 && NON_ASCII.test(last)) author = last;
    }

    result.push({ url, originalUrl: url, author, title, tags });
    prevEnd = end;
  }
  return result;
}

export function InputPanel({ jobId, nodeState, asrStatus, onStarted }: Props) {
  const nav = useNavigate();
  const { showToast } = useToast();
  // 锁定条件：asr 不是 idle 也不是 failed —— 即 queued/running/done。
  // failed 时仍允许编辑，方便用户改 URL 重试。
  const locked = asrStatus === 'queued' || asrStatus === 'running' || asrStatus === 'done';
  const [forking, setForking] = useState(false);

  async function doFork() {
    setForking(true);
    try {
      const state = await api.createJob({
        pipeline_id: 'paper_card_talk_015',
        title: `作品 ${new Date().toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })}`,
        inputs: { url: '' },
      });
      nav(`/jobs/${state.job_id}`);
    } catch (e: unknown) {
      showToast('新建作品失败，请稍后再试');
      console.error('[InputPanel] createJob 失败', e);
    } finally {
      setForking(false);
    }
  }

  // shares 是首要状态：从持久化的 outputs.shares 还原；缺失时按 urls 兜底成最小卡片
  const initialShares = useMemo<ParsedShare[]>(() => {
    const out = nodeState.outputs ?? {};
    const raw = out.shares;
    if (Array.isArray(raw)) {
      return (raw as ParsedShare[]).filter((s) => s && typeof s.url === 'string');
    }
    const urls = out.urls;
    if (Array.isArray(urls)) {
      return (urls as string[]).map((u) => ({ url: u, originalUrl: u, tags: [] }));
    }
    return [];
  }, [nodeState.outputs]);
  const [shares, setShares] = useState<ParsedShare[]>(initialShares);
  const [text, setText] = useState('');
  const [starting, setStarting] = useState(false);
  // 刚加入的 url 集合，用于触发 .flash CSS 动画；1.2s 动画结束后自动剔除
  const [flashUrls, setFlashUrls] = useState<Set<string>>(new Set());

  const canStart = shares.length > 0 && !starting && !locked;

  // textarea 变化时：解析到新链接 → 按 url 去重合并入 shares → 清空 textarea
  // 没识别到链接的内容保留在 textarea，让用户能看到原文继续编辑
  useEffect(() => {
    if (!text) return;
    const parsed = parseShares(text);
    if (parsed.length === 0) return;
    const seen = new Set(shares.map((s) => s.url));
    const fresh = parsed.filter((s) => !seen.has(s.url));
    setText('');
    if (fresh.length === 0) return;
    const freshUrls = fresh.map((s) => s.url);
    setShares((prev) => [...prev, ...fresh]);
    setFlashUrls((prev) => new Set([...prev, ...freshUrls]));
    const t = window.setTimeout(() => {
      setFlashUrls((prev) => {
        const next = new Set(prev);
        freshUrls.forEach((u) => next.delete(u));
        return next;
      });
    }, 1300);
    return () => window.clearTimeout(t);
  }, [text, shares]);

  // 防抖持久化：shares 变化 600ms 后 PUT；raw_text 显式置空，避免后端残留旧粘贴
  const persistTimer = useRef<number | null>(null);
  const skipFirstPersist = useRef(true);
  useEffect(() => {
    if (skipFirstPersist.current) {
      skipFirstPersist.current = false;
      return;
    }
    if (persistTimer.current != null) window.clearTimeout(persistTimer.current);
    persistTimer.current = window.setTimeout(() => {
      api.updateInputs(jobId, {
        urls: shares.map((s) => s.url),
        raw_text: '',
        shares,
      }).catch(console.error);
    }, 600);
    return () => {
      if (persistTimer.current != null) window.clearTimeout(persistTimer.current);
    };
  }, [shares, jobId]);

  function removeShare(idx: number) {
    setShares((prev) => prev.filter((_, i) => i !== idx));
  }

  async function doStart() {
    if (!canStart) return;
    setStarting(true);
    try {
      // 立即把当前状态 flush 给后端，避免还在 debounce 里
      await api.updateInputs(jobId, {
        urls: shares.map((s) => s.url),
        raw_text: '',
        shares,
      });
      await api.runNode(jobId, 'asr');
      onStarted();
    } catch (e: unknown) {
      showToast('启动失败，请稍后再试');
      console.error('[InputPanel] 启动 asr 失败', e);
    } finally {
      setStarting(false);
    }
  }

  return (
    <div>
      {locked && (
        <div className="input-locked-banner">
          <Lock size={14} strokeWidth={1.7} />
          <span className="banner-text">
            {asrStatus === 'done'
              ? 'ASR 已完成，本作品的输入已锁定。要换素材请新建作品。'
              : 'ASR 进行中，输入已锁定。'}
          </span>
          <button
            className="btn primary sm"
            disabled={forking}
            onClick={doFork}
            style={{ marginLeft: 'auto' }}
          >
            <Plus size={12} strokeWidth={1.8} />
            {forking ? '新建中…' : '新建作品'}
          </button>
        </div>
      )}

      {/* —— 粘贴区 —— */}
      <div className="section-h">
        <Link2 size={12} strokeWidth={1.7} /> 粘贴抖音分享文本 · 自动识别作者 / 标签 / 链接
      </div>
      <div className="form-row">
        <textarea
          className="field"
          value={text}
          placeholder={HINT}
          rows={6}
          autoFocus={!locked}
          disabled={locked}
          readOnly={locked}
          onChange={(e) => setText(e.target.value)}
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 'var(--text-sm)',
            lineHeight: 1.6,
            minHeight: 140,
            whiteSpace: 'pre-wrap',
            opacity: locked ? 0.55 : 1,
            cursor: locked ? 'not-allowed' : 'text',
          }}
        />
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 4 }}>
          <span className="dim-mono">
            {locked
              ? '已锁定（asr 已开始或完成）'
              : text.trim()
                ? '未识别出抖音链接'
                : '粘贴抖音分享文本，识别后自动并入下方列表'}
          </span>
        </div>
      </div>

      {/* —— 解析结果卡片列表 —— */}
      {shares.length > 0 && (
        <>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--s-3)',
              margin: 'var(--s-5) 0 var(--s-3)',
            }}
          >
            <div className="section-h" style={{ margin: 0, flex: 1 }}>
              <Film size={12} strokeWidth={1.7} /> 已识别作品 · {shares.length}
            </div>
            <button
              className="btn primary sm"
              disabled={!canStart}
              onClick={doStart}
            >
              <Play size={12} strokeWidth={1.8} />
              {starting
                ? '启动中…'
                : shares.length > 1
                  ? `开始创作（${shares.length} 条）`
                  : '开始创作'}
            </button>
          </div>
          <div className="parsed-list">
            {shares.map((s, i) => (
              <ParsedCard
                key={s.url}
                index={i + 1}
                item={s}
                onRemove={() => removeShare(i)}
                flash={flashUrls.has(s.url)}
                locked={locked}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function ParsedCard({
  index,
  item,
  onRemove,
  flash,
  locked,
}: {
  index: number;
  item: ParsedShare;
  onRemove: () => void;
  flash?: boolean;
  locked?: boolean;
}) {
  const href = item.originalUrl || item.url;
  return (
    <article className={`parsed-card${flash ? ' flash' : ''}`}>
      <div className="parsed-card-num mono">{index.toString().padStart(2, '0')}</div>
      <div className="parsed-card-body">
        {item.title && (
          <div className="parsed-card-title">{item.title}</div>
        )}
        {item.author && (
          <div
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              fontSize: 'var(--text-xs)',
              color: 'var(--ink-2)',
            }}
          >
            <User size={11} strokeWidth={1.7} style={{ color: 'var(--ink-3)' }} />
            {item.author}
          </div>
        )}
        {item.tags.length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {item.tags.map((t) => (
              <span
                key={t}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 2,
                  fontSize: 'var(--text-2xs)',
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--accent)',
                  background: 'var(--accent-tint)',
                  padding: '1px 6px',
                  borderRadius: 'var(--r-pill)',
                }}
              >
                <Hash size={9} strokeWidth={1.7} />{t}
              </span>
            ))}
          </div>
        )}
        <a
          className="parsed-card-link"
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          title={href}
        >
          <span className="link-text">{href}</span>
          <ExternalLink size={11} strokeWidth={1.7} />
        </a>
      </div>
      <button
        className="btn sm icon-only ghost danger"
        onClick={onRemove}
        title={locked ? '已锁定，无法移除' : '从粘贴框中移除'}
        disabled={locked}
      >
        <Trash2 size={12} strokeWidth={1.6} />
      </button>
    </article>
  );
}
