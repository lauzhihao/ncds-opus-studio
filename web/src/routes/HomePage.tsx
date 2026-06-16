// 首页：长期任务（对标账号）在上、临时任务（作品）在下，同一页。
// 每区最多 2 行卡片，超出折起（展开/收起）。每区一个虚线"+"新增框点击弹窗：
// 长期任务的新增框在末位，临时任务的新增框始终在首位。

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertCircle, Check, Image as ImageIcon, Link2, Loader2, Plus, Radar, RotateCw, Sparkles, Trash2, UserPlus } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { api } from '../api/client';
import type { AccountResolveResult, JobSummary, SubscriptionAuthor, WorkResolveResult } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Modal } from '../components/Modal';
import { Select } from '../components/Select';
import { ThemeSwitcher } from '../components/ThemeSwitcher';
import { AccountCard, JobCard } from '../components/WorkCards';
import { useToast } from '../components/Toast';
import { formatCount } from '../utils/format';

// 测量 .tpl-grid 当前列数（auto-fill 响应式），随容器尺寸变化重算。
function useGridColumns(ref: React.RefObject<HTMLElement>): number {
  const [cols, setCols] = useState(1);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const tmpl = getComputedStyle(el).gridTemplateColumns;
      const n = tmpl.split(' ').filter(Boolean).length;
      setCols(Math.max(1, n));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return cols;
}

function CardSection({
  title,
  icon: Icon,
  dataCount,
  cards,
  addLabel,
  onAdd,
  loading,
  rows = 2,
}: {
  title: string;
  icon: LucideIcon;
  dataCount: number;
  cards: ReactNode[];
  addLabel: string;
  onAdd: () => void;
  loading?: boolean;
  rows?: number;
}) {
  const gridRef = useRef<HTMLDivElement>(null);
  const cols = useGridColumns(gridRef);
  const [expanded, setExpanded] = useState(false);

  const capacity = cols * rows; // N 行总格数
  const overflow = cards.length > capacity;
  const visible = expanded ? cards : cards.slice(0, capacity);

  return (
    <section className="card-section">
      <div className="section-title">
        <Icon size={14} strokeWidth={1.6} className="dim" />
        <span className="label">{title}</span>
        <span className="count">{dataCount.toString().padStart(2, '0')}</span>
        {/* 主题色新增按钮：紧贴 02/03 右侧、分割线最左侧；点击行为不变（开弹窗） */}
        <button type="button" className="section-add" onClick={onAdd}>
          <Plus size={17} strokeWidth={2} />
          <span>{addLabel}</span>
        </button>
        {overflow && (
          <button type="button" className="section-toggle" onClick={() => setExpanded((e) => !e)}>
            {expanded ? '收起' : `展开全部 ${dataCount}`}
          </button>
        )}
        <span className="line" />
      </div>
      <div ref={gridRef} className="tpl-grid">
        {loading
          ? Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="tpl-card" style={{ pointerEvents: 'none', opacity: 0.5 }}>
                <div className="cover" />
                <div className="body"><div className="name">加载中…</div></div>
              </div>
            ))
          : visible}
      </div>
    </section>
  );
}

export function HomePage() {
  const nav = useNavigate();
  const { showToast } = useToast();
  const [authors, setAuthors] = useState<SubscriptionAuthor[]>([]);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [loadingAcc, setLoadingAcc] = useState(true);
  const [loadingJobs, setLoadingJobs] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const [showAddAccount, setShowAddAccount] = useState(false);
  const [showAddTemp, setShowAddTemp] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<JobSummary | null>(null);

  useEffect(() => {
    setLoadingAcc(true);
    api
      .getSubscriptions()
      .then((cfg) => setAuthors(cfg.authors ?? []))
      .catch((e: unknown) => { console.error('load subscriptions failed', e); setError('加载监控账号失败。'); })
      .finally(() => setLoadingAcc(false));
  }, [reloadKey]);

  useEffect(() => {
    setLoadingJobs(true);
    api
      .listJobs()
      .then((r) => setJobs(r.jobs))
      .catch((e: unknown) => { console.error('load jobs failed', e); setError('加载作品失败。'); })
      .finally(() => setLoadingJobs(false));
  }, [reloadKey]);

  // 补拉缺快照的老账号档案（加快照功能前添加的，没有头像/数据）：
  // 用其身份构造主页链接走 resolve 拉一次，补上头像/@号/数据并回写订阅（一次性，之后有缓存）。
  const backfilled = useRef<Set<string>>(new Set());
  useEffect(() => {
    const missing = authors.filter(
      (a) =>
        (a.follower_count == null || a.refreshed_at == null) && // 缺数据 或 缺"最近更新"时间
        !backfilled.current.has(a.sec_uid) &&
        (a.platform !== 'tiktok' || a.unique_id), // tiktok 没存 handle 的没法补，跳过
    );
    if (missing.length === 0) return;
    missing.forEach((a) => backfilled.current.add(a.sec_uid));
    let cancelled = false;
    (async () => {
      const got = new Map<string, AccountResolveResult>();
      await Promise.all(
        missing.map(async (a) => {
          const url =
            a.platform === 'tiktok'
              ? `https://www.tiktok.com/@${a.unique_id}`
              : `https://www.douyin.com/user/${a.sec_uid}`;
          try {
            const p = await api.resolveAccount(url);
            if (p.sec_uid === a.sec_uid || (a.unique_id && p.unique_id === a.unique_id)) got.set(a.sec_uid, p);
          } catch { /* 单个失败忽略 */ }
        }),
      );
      if (cancelled || got.size === 0) return;
      const merge = (a: SubscriptionAuthor): SubscriptionAuthor => {
        const p = got.get(a.sec_uid);
        return p
          ? {
              ...a,
              nickname: p.nickname || a.nickname,
              avatar: p.avatar || a.avatar,
              unique_id: p.unique_id || a.unique_id,
              follower_count: p.follower_count,
              like_count: p.like_count,
              works_count: p.works_count,
              refreshed_at: Math.floor(Date.now() / 1000),
            }
          : a;
      };
      setAuthors((prev) => prev.map(merge));
      // 回写订阅做缓存（合并到最新配置）
      try {
        const cfg = await api.getSubscriptions();
        await api.putSubscriptions({ ...cfg, authors: cfg.authors.map(merge) });
      } catch { /* 持久化失败不影响本次显示 */ }
    })();
    return () => { cancelled = true; };
  }, [authors]);

  // 有作品在执行时轮询刷新
  const anyRunning = useMemo(() => jobs.some((j) => j.running), [jobs]);
  useEffect(() => {
    if (!anyRunning) return;
    const id = window.setInterval(() => {
      api.listJobs().then((r) => setJobs(r.jobs)).catch(() => {});
    }, 4000);
    return () => window.clearInterval(id);
  }, [anyRunning]);

  async function confirmDelete() {
    if (!pendingDelete) return;
    const id = pendingDelete.job_id;
    try {
      await api.deleteJob(id);
      setJobs((prev) => prev.filter((j) => j.job_id !== id));
    } catch (e: unknown) {
      showToast('删除失败，请稍后再试');
      console.error('[HomePage] deleteJob 失败', e);
    } finally {
      setPendingDelete(null);
    }
  }

  const accountCards = authors.map((a) => (
    <AccountCard
      key={a.sec_uid}
      author={a}
      onOpen={() => nav(`/accounts/${encodeURIComponent(a.sec_uid)}`, { state: { author: a } })}
    />
  ));
  const jobCards = jobs.map((j) => (
    <JobCard key={j.job_id} job={j} onOpen={() => nav(`/jobs/${j.job_id}`)} onDelete={() => setPendingDelete(j)} />
  ));

  return (
    <div className="page">
      <div className="topbar">
        <div className="brand">
          <span className="mark">NCDS Opus Studio</span>
        </div>
        <div className="spacer" />
        <ThemeSwitcher />
      </div>

      {error && (
        <div className="load-error-banner" role="alert">
          <AlertCircle size={15} strokeWidth={1.8} />
          <span className="load-error-text">{error}</span>
          <button type="button" className="btn ghost sm" onClick={() => { setError(null); setReloadKey((k) => k + 1); }}>
            <RotateCw size={13} strokeWidth={1.8} /> 重试
          </button>
        </div>
      )}

      <div style={{ marginTop: 'var(--s-6)' }}>
        <CardSection
          title="长期任务 · 监控对标账号"
          icon={Radar}
          dataCount={authors.length}
          cards={accountCards}
          addLabel="对标账号"
          onAdd={() => setShowAddAccount(true)}
          loading={loadingAcc}
        />
      </div>

      <CardSection
        title="临时任务 · 分享链接作品"
        icon={Link2}
        dataCount={jobs.length}
        cards={jobCards}
        addLabel="临时作品"
        onAdd={() => setShowAddTemp(true)}
        loading={loadingJobs}
      />

      {showAddAccount && (
        <AddAccountModal
          existing={authors}
          onClose={() => setShowAddAccount(false)}
          onAdded={() => { setShowAddAccount(false); setReloadKey((k) => k + 1); }}
        />
      )}
      {showAddTemp && (
        <AddTempTaskModal onClose={() => setShowAddTemp(false)} onCreated={(jobId) => nav(`/jobs/${jobId}`)} />
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        title="删除作品？"
        message={
          <>
            将删除 <strong>{pendingDelete?.title || '未命名作品'}</strong>
            （<span className="mono">{pendingDelete?.job_id.slice(0, 8)}</span>），此操作不可恢复。
          </>
        }
        confirmLabel="删除"
        danger
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}

// —— 新增对标账号弹窗：粘贴主页分享链接/口令 → 后台解析 sec_uid → 确认添加 ——
// 待加入列表的一条：loading 占位 或 解析完成的账号档案 + 选择的更新频率（小时）
interface StagedAccount {
  id: string;
  status: 'loading' | 'done';
  profile?: AccountResolveResult;
  interval: number;
}

const FREQ_OPTIONS = [1, 3, 12, 24];
const MAX_STAGED = 5; // 一次最多添加 5 个账号

// 必须包含有效 URL 才触发解析：http(s) 链接，或 (www.)?douyin|tiktok.com/ 路径。
const URL_RE = /https?:\/\/[^\s]+|(?:www\.)?(?:douyin|tiktok)\.com\/[^\s]+/i;
function containsUrl(s: string): boolean {
  return URL_RE.test(s);
}

function AddAccountModal({
  existing,
  onClose,
  onAdded,
}: {
  existing: SubscriptionAuthor[];
  onClose: () => void;
  onAdded: () => void;
}) {
  const { showToast } = useToast();
  const [text, setText] = useState('');
  const [staged, setStaged] = useState<StagedAccount[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const idRef = useRef(0);

  async function startResolve(raw: string) {
    if (staged.length >= MAX_STAGED) {
      setErr(`一次最多添加 ${MAX_STAGED} 个账号，请先「添加监控」或移除部分`);
      return;
    }
    const id = String(++idRef.current);
    setText(''); // 清空输入框
    setErr(null);
    // 立即在列表顶部插入一行 loading（最新在最上面）；updater 内再兜一道硬上限防并发越界。
    // 若被上限挡掉，该 id 不入列，后续完成时按 id 的 map/filter 自然 no-op。
    setStaged((prev) => (prev.length >= MAX_STAGED ? prev : [{ id, status: 'loading', interval: 3 }, ...prev]));
    try {
      const r = await api.resolveAccount(raw);
      if (existing.some((a) => a.sec_uid === r.sec_uid)) {
        setStaged((prev) => prev.filter((s) => s.id !== id));
        setErr(`「${r.nickname || r.sec_uid.slice(-6)}」已在监控列表`);
        return;
      }
      setStaged((prev) => {
        // 列表里已有同账号的 done 行 -> 静默丢弃本 loading 行
        if (prev.some((s) => s.id !== id && s.profile?.sec_uid === r.sec_uid)) {
          return prev.filter((s) => s.id !== id);
        }
        return prev.map((s) => (s.id === id ? { ...s, status: 'done', profile: r } : s));
      });
    } catch (e: unknown) {
      console.error('[AddAccountModal] resolveAccount 失败', e);
      setStaged((prev) => prev.filter((s) => s.id !== id)); // 失败移除该 loading 行
      setErr('解析失败：请确认是抖音/TikTok 主页链接');
    }
  }

  // 输入变化即解析（防抖 350ms，接近即时）；必须包含有效 URL 才触发，否则不新增、不请求。
  useEffect(() => {
    const raw = text.trim();
    if (!raw || !containsUrl(raw)) return;
    const t = setTimeout(() => { void startResolve(raw); }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  function setFreq(id: string, v: number) {
    setStaged((prev) => prev.map((s) => (s.id === id ? { ...s, interval: v } : s)));
  }
  function removeStaged(id: string) {
    setStaged((prev) => prev.filter((s) => s.id !== id));
  }

  const ready = staged.filter((s) => s.status === 'done' && s.profile);

  async function doAddAll() {
    if (ready.length === 0) return;
    setBusy(true);
    setErr(null);
    try {
      // GET 当前配置后整体 append（PUT 是覆盖写）；跳过已存在的。
      const cfg = await api.getSubscriptions();
      const have = new Set(cfg.authors.map((a) => a.sec_uid));
      const toAdd = ready
        .filter((s) => !have.has(s.profile!.sec_uid)) // 已存在按 sec_uid 去重，忽略
        .map((s) => {
          const p = s.profile!;
          return {
            sec_uid: p.sec_uid,
            note: p.nickname || null,
            enabled: true,
            platform: p.platform,
            interval_hours: s.interval,
            // 展示快照：卡片直接用 + 下次 resolve 命中复用
            nickname: p.nickname || null,
            avatar: p.avatar || null,
            unique_id: p.unique_id || null,
            follower_count: p.follower_count,
            like_count: p.like_count,
            works_count: p.works_count,
            refreshed_at: Math.floor(Date.now() / 1000),
          };
        });
      await api.putSubscriptions({ ...cfg, authors: [...cfg.authors, ...toAdd] });
      onAdded();
    } catch (e: unknown) {
      console.error('[AddAccountModal] putSubscriptions 失败', e);
      setErr('添加失败，请稍后再试');
      showToast('添加监控账号失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="新增监控对标账号" onClose={onClose}>
      <div className="modal-form">
        <label className="modal-field">
          <span className="modal-label">对标账号主页地址</span>
          <textarea
            className="modal-input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="请输入对标账号主页地址"
            rows={2}
            spellCheck={false}
            autoFocus
          />
        </label>
        {err && <div className="modal-err">{err}</div>}

        {staged.map((s) => {
          if (s.status === 'loading') {
            return (
              <div key={s.id} className="resolved-account is-loading">
                <div className="resolved-row">
                  <div className="resolved-avatar skeleton" />
                  <div className="resolved-meta">
                    <div className="skeleton-bar" style={{ width: '55%' }} />
                    <div className="skeleton-bar sm" style={{ width: '38%' }} />
                  </div>
                </div>
                <div className="resolved-mask">
                  <Loader2 size={20} strokeWidth={2} className="spin" />
                </div>
              </div>
            );
          }
          const p = s.profile!;
          const label = p.platform === 'tiktok' ? 'TikTok' : '抖音';
          return (
            <div key={s.id} className="resolved-account">
              <div className="resolved-row">
                {p.avatar && (
                  <img
                    className="resolved-avatar"
                    src={p.avatar}
                    alt=""
                    onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
                  />
                )}
                <div className="resolved-meta">
                  <div className="resolved-name">
                    <span className={`platform-badge ${p.platform === 'tiktok' ? 'tiktok' : 'douyin'}`}>{label}</span>
                    <span className="resolved-nick">{p.nickname || '已解析账号'}</span>
                  </div>
                  <div className="resolved-uid mono">{p.unique_id ? `@${p.unique_id}` : p.sec_uid}</div>
                </div>
                <div className="resolved-stats">
                  <span><b>{formatCount(p.follower_count)}</b><i>粉丝</i></span>
                  <span><b>{formatCount(p.like_count)}</b><i>获赞</i></span>
                  <span><b>{formatCount(p.works_count)}</b><i>作品</i></span>
                </div>
                <div className="resolved-controls">
                  <Select
                    ariaLabel="更新频率"
                    value={s.interval}
                    options={FREQ_OPTIONS.map((h) => ({ value: h, label: `${h}h` }))}
                    onChange={(v) => setFreq(s.id, v)}
                  />
                  <button
                    className="btn sm icon-only ghost"
                    title="从列表移除"
                    aria-label="移除"
                    onClick={() => removeStaged(s.id)}
                  >
                    <Trash2 size={14} strokeWidth={1.7} />
                  </button>
                </div>
              </div>
            </div>
          );
        })}

        <div className="modal-actions">
          <button className="btn ghost sm" onClick={onClose} disabled={busy}>关闭</button>
          <button className="btn primary sm" onClick={doAddAll} disabled={busy || ready.length === 0}>
            {busy ? <Loader2 size={13} strokeWidth={2} className="spin" /> : <Radar size={13} strokeWidth={1.8} />}
            {busy ? '添加中…' : `添加监控${ready.length ? ` (${ready.length})` : ''}`}
          </button>
        </div>
      </div>
    </Modal>
  );
}

// —— 新建临时任务弹窗：粘贴作品分享链接 → 智能解析作品卡（封面/标题/话题/数据/作者）——
// 每卡可「关注ta」（把作者加入对标）/「删除」（移除该结果）；底部「新建作品」用解析结果建生产任务。
interface StagedWork {
  id: string;
  status: 'loading' | 'done';
  work?: WorkResolveResult;
  followed?: boolean; // 已「关注ta」加入对标
}

const MAX_STAGED_WORK = 5; // 一次最多解析 5 个作品

function AddTempTaskModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (jobId: string) => void;
}) {
  const { showToast } = useToast();
  const [text, setText] = useState('');
  const [staged, setStaged] = useState<StagedWork[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const idRef = useRef(0);

  async function startResolve(raw: string) {
    if (staged.length >= MAX_STAGED_WORK) {
      setErr(`一次最多解析 ${MAX_STAGED_WORK} 个作品，请先移除部分`);
      return;
    }
    const id = String(++idRef.current);
    setText(''); // 清空输入框
    setErr(null);
    // 立即在列表顶部插入 loading 占位；updater 内再兜一道硬上限防并发越界。
    setStaged((prev) => (prev.length >= MAX_STAGED_WORK ? prev : [{ id, status: 'loading' }, ...prev]));
    try {
      const r = await api.resolveWork(raw);
      setStaged((prev) => {
        // 列表里已有同作品的 done 行 -> 静默丢弃本 loading 行（去重）
        if (prev.some((s) => s.id !== id && s.work?.aweme_id === r.aweme_id)) {
          return prev.filter((s) => s.id !== id);
        }
        return prev.map((s) => (s.id === id ? { ...s, status: 'done', work: r } : s));
      });
    } catch (e: unknown) {
      console.error('[AddTempTaskModal] resolveWork 失败', e);
      setStaged((prev) => prev.filter((s) => s.id !== id)); // 失败移除该 loading 行
      setErr('解析失败：请确认是抖音作品分享链接');
    }
  }

  // 输入变化即解析（防抖 350ms）；必须含合法 URL 才触发（复用账号弹窗的 containsUrl）。
  useEffect(() => {
    const raw = text.trim();
    if (!raw || !containsUrl(raw)) return;
    const t = setTimeout(() => { void startResolve(raw); }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  function removeStaged(id: string) {
    setStaged((prev) => prev.filter((s) => s.id !== id));
  }

  // 关注ta：仅在 UI 上标记意图（变"已关注"）；真正写入对标延迟到「新建作品」提交时异步触发。
  function markFollow(s: StagedWork) {
    if (!s.work?.author.sec_uid) {
      showToast('该作品无法获取作者信息');
      return;
    }
    setStaged((prev) => prev.map((x) => (x.id === s.id ? { ...x, followed: true } : x)));
  }

  const ready = staged.filter((s) => s.status === 'done' && s.work);

  // 提交时异步触发关注：把标记了 followed 的作者加入对标订阅。
  // 已在列表的忽略（不报错）；整体 fire-and-forget，不阻塞作品创建/导航。
  async function followMarkedAuthors() {
    const wanted = ready
      .filter((s) => s.followed && s.work?.author.sec_uid)
      .map((s) => s.work!.author);
    if (wanted.length === 0) return;
    try {
      const cfg = await api.getSubscriptions();
      const have = new Set(cfg.authors.map((a) => a.sec_uid));
      const seen = new Set<string>();
      const toAdd: SubscriptionAuthor[] = [];
      for (const a of wanted) {
        if (have.has(a.sec_uid) || seen.has(a.sec_uid)) continue; // 已在对标 / 本批重复 -> 忽略
        seen.add(a.sec_uid);
        toAdd.push({
          sec_uid: a.sec_uid,
          note: a.nickname || null,
          enabled: true,
          platform: a.platform || 'douyin',
          nickname: a.nickname || null,
          avatar: a.avatar || null,
          unique_id: a.unique_id || null,
        });
      }
      if (toAdd.length === 0) return; // 全部已在对标列表，忽略
      await api.putSubscriptions({ ...cfg, authors: [...cfg.authors, ...toAdd] });
    } catch (e: unknown) {
      console.error('[AddTempTaskModal] 关注作者失败', e); // 静默，不打扰用户
    }
  }

  // 新建作品：用解析出的作品构造 shares[] 喂给生产任务（结构化，优于原 raw_text）。
  async function doCreate() {
    if (ready.length === 0) return;
    setBusy(true);
    setErr(null);
    try {
      const shares = ready.map((s) => {
        const w = s.work!;
        return { url: w.share_url, title: w.title, author: w.author.nickname, tags: w.hashtags };
      });
      const firstTitle = ready[0].work!.title?.trim().slice(0, 24);
      const state = await api.createJob({
        pipeline_id: 'paper_card_talk_015',
        title:
          ready.length === 1 && firstTitle
            ? firstTitle
            : `临时任务 ${new Date().toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })}`,
        inputs: {},
      });
      await api.updateInputs(state.job_id, { shares });
      void followMarkedAuthors(); // 关注跟随提交异步触发（不阻塞导航）
      onCreated(state.job_id);
    } catch (e: unknown) {
      console.error('[AddTempTaskModal] createJob 失败', e);
      setErr('创建失败，请稍后再试');
      showToast('创建临时任务失败，请稍后再试');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="用分享链接新建临时任务" onClose={onClose}>
      <div className="modal-form">
        <label className="modal-field">
          <span className="modal-label">抖音作品分享链接 / 分享文本</span>
          <textarea
            className="modal-input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="粘贴抖音作品分享链接或整段分享文本，自动智能解析"
            rows={2}
            spellCheck={false}
            autoFocus
          />
        </label>
        {err && <div className="modal-err">{err}</div>}

        {staged.map((s) => {
          if (s.status === 'loading') {
            return (
              <div key={s.id} className="resolved-work is-loading">
                <div className="resolved-work-cover skeleton" />
                <div className="resolved-work-meta">
                  <div className="skeleton-bar" style={{ width: '72%' }} />
                  <div className="skeleton-bar sm" style={{ width: '46%' }} />
                </div>
                <div className="resolved-mask">
                  <Loader2 size={20} strokeWidth={2} className="spin" />
                </div>
              </div>
            );
          }
          const w = s.work!;
          return (
            <div key={s.id} className="resolved-work">
              {w.cover_url ? (
                <img
                  className="resolved-work-cover"
                  src={w.cover_url}
                  alt=""
                  loading="lazy"
                  onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = 'hidden'; }}
                />
              ) : (
                <div className="resolved-work-cover resolved-work-cover-empty">
                  <ImageIcon size={20} strokeWidth={1.5} />
                </div>
              )}
              <div className="resolved-work-meta">
                <div className="resolved-work-title">{w.title || '无标题作品'}</div>
                {w.hashtags.length > 0 && (
                  <div className="resolved-work-tags">
                    {w.hashtags.map((t, i) => (
                      <span key={i} className="work-tag">#{t}</span>
                    ))}
                  </div>
                )}
                <div className="resolved-work-statrow">
                  <div className="resolved-work-stats">
                    <span><b>{formatCount(w.digg)}</b><i>点赞</i></span>
                    <span><b>{formatCount(w.comment)}</b><i>评论</i></span>
                    <span><b>{formatCount(w.share)}</b><i>分享</i></span>
                    <span><b>{formatCount(w.collect)}</b><i>收藏</i></span>
                  </div>
                  <div className="resolved-work-side">
                    <div className="resolved-work-author">
                      {w.author.avatar && (
                        <img
                          className="rwa-avatar"
                          src={w.author.avatar}
                          alt=""
                          onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
                        />
                      )}
                      <span className="rwa-name">{w.author.nickname || '未知作者'}</span>
                    </div>
                    <button
                      className="btn sm ghost"
                      disabled={s.followed || !w.author.sec_uid}
                      title={w.author.sec_uid ? '把作者加入对标账号（提交作品时执行）' : '该作品无作者信息'}
                      onClick={() => markFollow(s)}
                    >
                      {s.followed ? (
                        <><Check size={13} strokeWidth={2} /> 已关注</>
                      ) : (
                        <><UserPlus size={13} strokeWidth={1.8} /> 关注ta</>
                      )}
                    </button>
                  </div>
                </div>
              </div>
              <button
                className="btn sm icon-only ghost resolved-work-del"
                title="移除该结果"
                aria-label="移除"
                onClick={() => removeStaged(s.id)}
              >
                <Trash2 size={14} strokeWidth={1.7} />
              </button>
            </div>
          );
        })}

        <div className="modal-actions">
          <button className="btn ghost sm" onClick={onClose} disabled={busy}>取消</button>
          <button className="btn primary sm" onClick={doCreate} disabled={busy || ready.length === 0}>
            {busy ? <Loader2 size={13} strokeWidth={2} className="spin" /> : <Sparkles size={13} strokeWidth={1.8} />}
            {busy ? '创建中…' : `新建作品${ready.length ? ` (${ready.length})` : ''}`}
          </button>
        </div>
      </div>
    </Modal>
  );
}
