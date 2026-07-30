// 首页：长期任务（对标账号）在上、临时任务（作品）在下，同一页。
// 每区最多 2 行卡片，超出折起（展开/收起）。每区一个虚线"+"新增框点击弹窗：
// 长期任务的新增框在末位，临时任务的新增框始终在首位。

import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { AlertCircle, Check, Clapperboard, Link2, Loader2, Menu, Plus, Radar, RotateCw, Sparkles, Trash2, UserPlus, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { api } from '../api/client';
import type { AccountResolveResult, JobSummary, SubscriptionAuthor, WorkResolveResult } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Modal } from '../components/Modal';
import { AppsMenu } from '../components/AppsMenu';
import { AuthUserMenu } from '../components/AuthGate';
import { ThemeSwitcher } from '../components/ThemeSwitcher';
import { AccountCard, JobCard } from '../components/WorkCards';
import { GlobalLoading } from '../components/GlobalLoading';
import { useToast } from '../components/Toast';
import { DEFAULT_DOMAIN, DOMAINS, domainByKey, type DomainKey } from '../config/domains';
import { formatCount } from '../utils/format';
import { clearMockJobClientState, isStudioMockMode, withMockQuery } from '../utils/mockMode';
import { platformBadgeClass, platformDisplayName } from '../utils/platform';

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

// 是否窄视口(≤640px)：移动端取消"两行折叠 + 展开全部"，改为单列全量流式展示。
// matchMedia 同步取初值(无首帧闪烁)，再监听 change 跟随窗口缩放。
function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(max-width: 768px)').matches,
  );
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 768px)');
    const on = () => setMobile(mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return mobile;
}

// 赛道过滤标签组：桌面端内联在分区标题行，移动端复用进顶栏汉堡面板。
function DomainFilter({ selected, onSelect }: { selected: string | null; onSelect: (key: string | null) => void }) {
  return (
    <>
      <button className={`domain-tab${selected === null ? ' active' : ''}`} onClick={() => onSelect(null)}>
        不限
      </button>
      {DOMAINS.map((d) => (
        <button
          key={d.key}
          className={`domain-tab${selected === d.key ? ' active' : ''}`}
          onClick={() => onSelect(d.key)}
        >
          {d.label}
        </button>
      ))}
    </>
  );
}

function CardSection({
  title,
  icon: Icon,
  dataCount,
  cards,
  addLabel,
  onAdd,
  rows = 2,
  filters,
}: {
  title: string;
  icon: LucideIcon;
  dataCount: number;
  cards: ReactNode[];
  addLabel: string;
  onAdd: () => void;
  rows?: number;
  filters?: ReactNode;
}) {
  const gridRef = useRef<HTMLDivElement>(null);
  const cols = useGridColumns(gridRef);
  const isMobile = useIsMobile();
  const [expanded, setExpanded] = useState(false);

  const capacity = cols * rows; // N 行总格数
  // 移动端单列流式全量展示(不折叠)；桌面端保留两行折叠 + 展开全部
  const overflow = !isMobile && cards.length > capacity;
  const visible = expanded || isMobile ? cards : cards.slice(0, capacity);

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
        {filters && <div className="section-filters">{filters}</div>}
        <span className="line" />
      </div>
      <div ref={gridRef} className="tpl-grid">
        {visible}
      </div>
    </section>
  );
}

const homeLoadingOverlayStyle: CSSProperties = {
  position: 'fixed',
  inset: 0,
  zIndex: 90,
  display: 'grid',
  placeItems: 'center',
  background: 'color-mix(in srgb, var(--bg-canvas) 78%, rgba(255,255,255,0.18))',
  backdropFilter: 'blur(8px)',
  WebkitBackdropFilter: 'blur(8px)',
};

function HomeLoadingOverlay() {
  return (
    <div style={homeLoadingOverlayStyle} role="status" aria-label="加载中">
      <GlobalLoading size={72} thickness={12} coreColor="var(--bg-canvas)" />
    </div>
  );
}

export function HomePage() {
  const nav = useNavigate();
  const [searchParams] = useSearchParams();
  const { showToast } = useToast();
  const mockMode = isStudioMockMode(searchParams);
  const [authors, setAuthors] = useState<SubscriptionAuthor[]>([]);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [loadingAcc, setLoadingAcc] = useState(true);
  const [loadingJobs, setLoadingJobs] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [selectedDomain, setSelectedDomain] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false); // 移动端顶栏赛道筛选汉堡面板

  const [showAddAccount, setShowAddAccount] = useState(false);
  const [showAddTemp, setShowAddTemp] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<JobSummary | null>(null);

  // 加载失败横幅不占满整行：右边缘对齐到第一个 section 新增按钮("关注作者")的右缘。
  // 按钮右缘由标题行左侧内容(图标/label/count/按钮)决定、与视口宽无关，故实测一次即可；
  // 用 ResizeObserver 盯标题行，覆盖 web font 异步加载后 label 宽度变化导致的 reflow。
  const bannerRef = useRef<HTMLDivElement>(null);
  const [bannerWidth, setBannerWidth] = useState<number>();
  useLayoutEffect(() => {
    if (!error) { setBannerWidth(undefined); return; }
    const banner = bannerRef.current;
    const addBtn = document.querySelector<HTMLElement>('.section-add');
    const titleRow = addBtn?.closest('.section-title');
    if (!banner || !addBtn || !titleRow) return;
    const measure = () => {
      const w = addBtn.getBoundingClientRect().right - banner.getBoundingClientRect().left;
      if (w > 0) setBannerWidth(w);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(titleRow);
    return () => ro.disconnect();
  }, [error, loadingAcc]);

  useEffect(() => {
    setLoadingAcc(true);
    api
      .getSubscriptions(selectedDomain || undefined)
      .then((cfg) => setAuthors(cfg.authors ?? []))
      .catch((e: unknown) => { console.error('load subscriptions failed', e); setError('加载监控账号失败。'); })
      .finally(() => setLoadingAcc(false));
  }, [reloadKey, selectedDomain]);

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

  async function openDemoJob() {
    try {
      const demo = await api.ensureMock();
      clearMockJobClientState(demo.job_id);
      nav(withMockQuery(`/jobs/${demo.job_id}`, true));
    } catch (e: unknown) {
      showToast('初始化演示作品失败，请稍后再试');
      console.error('[HomePage] ensureMock 失败', e);
    }
  }

  function openJob(jobId: string) {
    if (mockMode) {
      void openDemoJob();
      return;
    }
    nav(`/jobs/${jobId}`);
  }

  const accountCards = authors.map((a) => (
    <AccountCard
      key={a.sec_uid}
      author={a}
      onOpen={() => nav(withMockQuery(`/accounts/${encodeURIComponent(a.sec_uid)}`, mockMode), { state: { author: a } })}
    />
  ));
  const jobCards = jobs.map((j) => (
    <JobCard key={j.job_id} job={j} onOpen={() => openJob(j.job_id)} onDelete={() => setPendingDelete(j)} />
  ));
  const loadingHomeData = loadingAcc || loadingJobs;

  return (
    <div className="page">
      {loadingHomeData && <HomeLoadingOverlay />}

      <div className="topbar">
        <div className="brand">
          <span className="mark">NCDS Opus Studio</span>
        </div>
        <div className="spacer" />
        <button type="button" className="btn ghost sm film-entry" onClick={() => nav('/film')}>
          <Clapperboard size={14} strokeWidth={1.8} /> 影视本地化
        </button>
        <AppsMenu />
        <ThemeSwitcher />
        <AuthUserMenu />
        {/* 移动端：赛道筛选收进右上角汉堡菜单（桌面端 CSS 隐藏，筛选仍内联在分区标题行） */}
        <div className="topbar-filter-menu">
          <button
            type="button"
            className="topbar-hamburger"
            aria-label="赛道筛选"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((o) => !o)}
          >
            {menuOpen ? <X size={20} strokeWidth={1.8} /> : <Menu size={20} strokeWidth={1.8} />}
          </button>
          {menuOpen && (
            <>
              <div className="topbar-menu-backdrop" onClick={() => setMenuOpen(false)} />
              <div className="topbar-menu-panel" role="menu">
                <div className="topbar-menu-filters">
                  <DomainFilter
                    selected={selectedDomain}
                    onSelect={(key) => { setSelectedDomain(key); setMenuOpen(false); }}
                  />
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {error && (
        <div
          className="load-error-banner"
          role="alert"
          ref={bannerRef}
          style={bannerWidth ? { width: `${bannerWidth}px` } : undefined}
        >
          <AlertCircle size={15} strokeWidth={1.8} />
          <span className="load-error-text">{error}</span>
          <button type="button" className="btn ghost sm" onClick={() => { setError(null); setReloadKey((k) => k + 1); }}>
            <RotateCw size={13} strokeWidth={1.8} /> 重试
          </button>
        </div>
      )}

      <div style={{ marginTop: 'var(--s-6)' }}>
        <CardSection
          title="时刻关注作者动态"
          icon={Radar}
          dataCount={authors.length}
          cards={accountCards}
          addLabel="优质作者"
          onAdd={() => setShowAddAccount(true)}
          filters={<DomainFilter selected={selectedDomain} onSelect={setSelectedDomain} />}
        />
      </div>

      <CardSection
        title="基于优质作品二创"
        icon={Link2}
        dataCount={jobs.length}
        cards={jobCards}
        addLabel="优质作品"
        onAdd={() => setShowAddTemp(true)}
      />

      {showAddAccount && (
        <AddAccountModal
          existing={authors}
          onClose={() => setShowAddAccount(false)}
          onAdded={() => { setShowAddAccount(false); setReloadKey((k) => k + 1); }}
        />
      )}
      {showAddTemp && (
        <AddTempTaskModal
          onClose={() => setShowAddTemp(false)}
          onCreated={(jobId) => { if (mockMode) void openDemoJob(); else nav(`/jobs/${jobId}`); }}
          onFilmLocalization={() => { setShowAddTemp(false); nav('/film'); }}
        />
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

// —— 关注优质作者弹窗：粘贴主页分享链接/口令 → 后台解析 sec_uid → 确认添加 ——
// 待加入列表的一条：loading 占位 或 解析完成的账号档案 + 选择的领域 profile。
// 更新频率不再逐账号选，统一吃后端全局默认（3h）。
interface StagedAccount {
  id: string;
  status: 'loading' | 'done';
  profile?: AccountResolveResult;
  domain: DomainKey;
}

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
      setErr(`一次最多添加 ${MAX_STAGED} 个账号，请先「添加关注」或移除部分`);
      return;
    }
    const id = String(++idRef.current);
    setText(''); // 清空输入框
    setErr(null);
    // 立即在列表顶部插入一行 loading（最新在最上面）；updater 内再兜一道硬上限防并发越界。
    // 若被上限挡掉，该 id 不入列，后续完成时按 id 的 map/filter 自然 no-op。
    setStaged((prev) => (prev.length >= MAX_STAGED ? prev : [{ id, status: 'loading', domain: DEFAULT_DOMAIN }, ...prev]));
    try {
      const r = await api.resolveAccount(raw);
      if (existing.some((a) => a.sec_uid === r.sec_uid)) {
        setStaged((prev) => prev.filter((s) => s.id !== id));
        setErr(`「${r.nickname || r.sec_uid.slice(-6)}」已在关注列表`);
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
      setErr('解析失败：请确认是抖音/TK 主页链接');
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

  function setDomain(id: string, v: DomainKey) {
    setStaged((prev) => prev.map((s) => (s.id === id ? { ...s, domain: v } : s)));
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
            domain: s.domain, // 领域 profile；更新频率不下发，吃后端全局默认 3h
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
      // 新增对标账号后立即触发一轮采集做首次初始化（不等下个周期）；
      // fire-and-forget 不阻塞关窗。已在库的被后端节流/works_repo 缓存挡掉，不会重采。
      if (toAdd.length > 0) void api.triggerTick();
      onAdded();
    } catch (e: unknown) {
      console.error('[AddAccountModal] putSubscriptions 失败', e);
      setErr('添加失败，请稍后再试');
      showToast('关注作者失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="关注优质作者" onClose={onClose}>
      <div className="modal-form">
        <label className="modal-field">
          <textarea
            className="modal-input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="可粘贴抖音或TK的优质创作者首页链接来完成自动解析"
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
          const label = platformDisplayName(p.platform);
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
                    <span className={`platform-badge ${platformBadgeClass(p.platform)}`}>{label}</span>
                    <span className="resolved-nick">{p.nickname || '已解析账号'}</span>
                  </div>
                  <div className="resolved-uid mono">{p.unique_id ? `@${p.unique_id}` : p.sec_uid}</div>
                </div>
                <div className="resolved-stats">
                  <span><b>{formatCount(p.follower_count)}</b><i>粉丝</i></span>
                  <span><b>{formatCount(p.like_count)}</b><i>获赞</i></span>
                  <span><b>{formatCount(p.works_count)}</b><i>作品</i></span>
                </div>
                {/* 领域单选：与数据/删除同行；选中的 profile 决定后续选题/撰稿提示词（占位待填） */}
                <div className="resolved-domains" role="radiogroup" aria-label="领域">
                  {DOMAINS.map((d) => (
                    <button
                      key={d.key}
                      type="button"
                      role="radio"
                      aria-checked={s.domain === d.key}
                      className={`domain-pill ${d.colorClass}${s.domain === d.key ? ' is-on' : ''}`}
                      onClick={() => setDomain(s.id, d.key)}
                    >
                      {d.label}
                    </button>
                  ))}
                </div>
                <div className="resolved-controls">
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
            {busy ? '添加中…' : `添加关注${ready.length ? ` (${ready.length})` : ''}`}
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
  // 赛道：resolve 响应带已存 domain（继承/之前选的）；无则用 DEFAULT_DOMAIN 展示，但不主动写回
  domain: DomainKey;
}

const MAX_STAGED_WORK = 5; // 一次最多解析 5 个作品

function AddTempTaskModal({
  onClose,
  onCreated,
  onFilmLocalization,
}: {
  onClose: () => void;
  onCreated: (jobId: string) => void;
  onFilmLocalization: () => void;
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
    // 立即在列表顶部插入 loading 占位；domain 先给 DEFAULT_DOMAIN，resolve 完后按响应覆盖
    setStaged((prev) => (prev.length >= MAX_STAGED_WORK ? prev : [{ id, status: 'loading', domain: DEFAULT_DOMAIN }, ...prev]));
    try {
      const r = await api.resolveWork(raw);
      // 继承已存 domain（作者采集时写入的），无则前端展示默认值（不主动写回 manifest）
      const resolvedDomain = (r.domain && domainByKey(r.domain) ? r.domain as DomainKey : DEFAULT_DOMAIN);
      setStaged((prev) => {
        // 列表里已有同作品的 done 行 -> 静默丢弃本 loading 行（去重）
        if (prev.some((s) => s.id !== id && s.work?.aweme_id === r.aweme_id)) {
          return prev.filter((s) => s.id !== id);
        }
        return prev.map((s) => (s.id === id ? { ...s, status: 'done', work: r, domain: resolvedDomain } : s));
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

  // 赛道选定：乐观更新 UI，同时写回 manifest（fire-and-forget；失败不阻塞用户）。
  // 写回是为了「刷新后保留」（AC#2）；生产创建时还会再从 staged 取 domain 带进 inputs。
  function setWorkDomain(s: StagedWork, domain: DomainKey) {
    setStaged((prev) => prev.map((x) => (x.id === s.id ? { ...x, domain } : x)));
    if (s.work) {
      api.saveWorkDomain(s.work.platform, s.work.aweme_id, domain).catch((e: unknown) => {
        console.error('[AddTempTaskModal] saveWorkDomain 失败', e);
      });
    }
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
  const hasFilmDomain = ready.some((s) => s.domain === 'film');

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
          domain: DEFAULT_DOMAIN, // 「关注ta」无领域选择 UI，先给默认 profile，保证每个订阅作者都有领域
          nickname: a.nickname || null,
          avatar: a.avatar || null,
          unique_id: a.unique_id || null,
        });
      }
      if (toAdd.length === 0) return; // 全部已在对标列表，忽略
      await api.putSubscriptions({ ...cfg, authors: [...cfg.authors, ...toAdd] });
      void api.triggerTick(); // 「关注ta」新加的账号也立即初始化采集（同 doAddAll，已在库的被节流跳过）
    } catch (e: unknown) {
      console.error('[AddTempTaskModal] 关注作者失败', e); // 静默，不打扰用户
    }
  }

  // 新建作品：用解析出的作品构造 shares[] 喂给生产任务（结构化，优于原 raw_text）。
  // domain 取第一条作品的赛道（多作品理论上同赛道，取首条保持简单）并放进 instance inputs，
  // 使其经 instance_runner._run_step 透传到每个 performer 的 params（task-2.2 已打通）。
  async function doCreate() {
    if (ready.length === 0) return;
    setBusy(true);
    setErr(null);
    try {
      const shares = ready.map((s) => {
        const w = s.work!;
        return { url: w.share_url, title: w.title, author: w.author.nickname, tags: w.hashtags };
      });
      // 取首条作品的赛道 key 送进引擎 inputs
      const domain = ready[0].domain;
      const firstTitle = ready[0].work!.title?.trim().slice(0, 60);
      const state = await api.createJob({
        pipeline_id: 'final_preview',
        title:
          ready.length === 1 && firstTitle
            ? firstTitle
            : `临时任务 ${new Date().toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })}`,
        inputs: { domain }, // 赛道送进 instance inputs，引擎透传到各 performer
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
          // 抖音/TK 才有稳定的分享/收藏口径；油管等平台缺字段时不要渲染成 "undefined"
          const showShareCollect = w.platform === 'douyin' || w.platform === 'tiktok';
          const authorName = (w.author?.nickname || '').trim();
          const authorSecUid = (w.author?.sec_uid || '').trim();
          // 无作者身份时整行不渲染（避免「未知作者 + 禁用关注」）；有昵称无 sec_uid 只显示名字
          const hasAuthor = Boolean(authorSecUid || authorName);
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
                <div className="resolved-work-cover resolved-work-cover-empty">暂无封面</div>
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
                <div className="resolved-work-bottom">
                  {hasAuthor && (
                    <div className="resolved-work-author-row">
                      <div className="resolved-work-author">
                        {w.author.avatar && (
                          <img
                            className="rwa-avatar"
                            src={w.author.avatar}
                            alt=""
                            onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
                          />
                        )}
                        <span className="rwa-name">{authorName || '未知作者'}</span>
                      </div>
                      {authorSecUid ? (
                        <button
                          className="btn sm ghost"
                          disabled={s.followed}
                          title="把作者加入对标账号（提交作品时执行）"
                          onClick={() => markFollow(s)}
                        >
                          {s.followed ? (
                            <><Check size={13} strokeWidth={2} /> 已关注</>
                          ) : (
                            <><UserPlus size={13} strokeWidth={1.8} /> 关注ta</>
                          )}
                        </button>
                      ) : null}
                    </div>
                  )}
                  <div className="resolved-work-stats">
                    {w.digg != null && <span><b>{formatCount(w.digg)}</b><i>点赞</i></span>}
                    {w.comment != null && <span><b>{formatCount(w.comment)}</b><i>评论</i></span>}
                    {showShareCollect && w.share != null && (
                      <span><b>{formatCount(w.share)}</b><i>分享</i></span>
                    )}
                    {showShareCollect && w.collect != null && (
                      <span><b>{formatCount(w.collect)}</b><i>收藏</i></span>
                    )}
                  </div>
                  {/* 赛道单选：选定后写回 manifest（AC#1 AC#2） */}
                  <div className="resolved-domains" role="radiogroup" aria-label="赛道">
                    {DOMAINS.map((d) => (
                      <button
                        key={d.key}
                        type="button"
                        role="radio"
                        aria-checked={s.domain === d.key}
                        className={`domain-pill ${d.colorClass}${s.domain === d.key ? ' is-on' : ''}`}
                        onClick={() => setWorkDomain(s, d.key)}
                      >
                        {d.label}
                      </button>
                    ))}
                  </div>
                  {s.domain === 'film' && (
                    <p className="film-domain-note">影视本地化仅处理你上传并确认授权的原片，分享链接不会导入原视频。</p>
                  )}
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
          <button
            className="btn primary sm"
            onClick={hasFilmDomain ? onFilmLocalization : doCreate}
            disabled={busy || ready.length === 0}
          >
            {busy ? <Loader2 size={13} strokeWidth={2} className="spin" /> : <Sparkles size={13} strokeWidth={1.8} />}
            {busy ? '创建中…' : hasFilmDomain ? '前往上传授权原片' : `新建作品${ready.length ? ` (${ready.length})` : ''}`}
          </button>
        </div>
      </div>
    </Modal>
  );
}
