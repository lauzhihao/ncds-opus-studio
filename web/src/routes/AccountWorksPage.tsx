// 长期任务 · 某对标账号的作品列表。点某作品 → 创建"平台衍生作品"画布（seed 源链接）。
// 数据接 GET /accounts/{sec_uid}/posts（沈括已采集的 all_posts.json）。

import { useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { AlertCircle, ArrowLeft, Clock, Heart, Loader2, MessageCircle, Play, RotateCw, Share2, Sparkles, Star, UserMinus } from 'lucide-react';

import { api } from '../api/client';
import type { AccountPost, SubscriptionAuthor } from '../api/types';
import { ThemeSwitcher } from '../components/ThemeSwitcher';
import { useToast } from '../components/Toast';
import { CoverImage } from '../components/WorkCards';
import { formatCount, timeAgo } from '../utils/format';

export function AccountWorksPage() {
  const { secUid } = useParams<{ secUid: string }>();
  const nav = useNavigate();
  const location = useLocation();
  // 作者信息从上一页（账号卡）带过来，免再拉一次
  const headerAuthor = (location.state as { author?: SubscriptionAuthor } | null)?.author;
  const authorName = headerAuthor?.nickname || headerAuthor?.note || '账号';
  const { showToast } = useToast();
  const [posts, setPosts] = useState<AccountPost[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [creating, setCreating] = useState<string | null>(null);

  useEffect(() => {
    if (!secUid) return;
    setLoading(true);
    setError(null);
    api
      .getAccountPosts(secUid)
      .then((r) => setPosts(r.posts ?? []))
      .catch((e: unknown) => {
        console.error('load account posts failed', e);
        setError('加载作品失败，请稍后重试。');
      })
      .finally(() => setLoading(false));
  }, [secUid, reloadKey]);

  // 基于源作品创建衍生画布：建 job 后把源链接 seed 进 input 节点（沈括的采集源）。
  async function deriveFrom(post: AccountPost) {
    setCreating(post.aweme_id);
    try {
      const state = await api.createJob({
        pipeline_id: 'paper_card_talk_015',
        title: post.desc ? post.desc.slice(0, 24) : `衍生作品 ${post.aweme_id.slice(-6)}`,
        inputs: {},
      });
      // seed 源链接到 input 节点 outputs（沈括 InputPanel 从 outputs.shares 还原）
      await api.updateInputs(state.job_id, {
        urls: [post.share_url],
        shares: [{ url: post.share_url, originalUrl: post.share_url, title: post.desc, tags: [] }],
      });
      nav(`/jobs/${state.job_id}`);
    } catch (e: unknown) {
      showToast('创建衍生作品失败，请稍后再试');
      console.error('[AccountWorksPage] deriveFrom 失败', e);
    } finally {
      setCreating(null);
    }
  }

  const [unfollowing, setUnfollowing] = useState(false);
  async function unfollow() {
    if (!secUid) return;
    setUnfollowing(true);
    try {
      const cfg = await api.getSubscriptions();
      await api.putSubscriptions({ ...cfg, authors: cfg.authors.filter((a) => a.sec_uid !== secUid) });
      showToast(`已取消关注 ${authorName}`);
      nav('/');
    } catch (e: unknown) {
      console.error('[AccountWorksPage] unfollow 失败', e);
      showToast('取消关注失败，请稍后再试');
    } finally {
      setUnfollowing(false);
    }
  }

  const platformLabel = headerAuthor?.platform === 'tiktok' ? 'TikTok' : '抖音';

  return (
    <div className="page">
      <div className="topbar">
        <button className="btn ghost sm" onClick={() => nav('/')} title="返回" aria-label="返回">
          <ArrowLeft size={14} strokeWidth={1.6} /> 返回
        </button>
        <div className="spacer" />
        <ThemeSwitcher />
      </div>

      {/* 作者信息条：头像 + 平台/监控状态/更新时间 + 数据 + 取消关注（从上一页带过来） */}
      {headerAuthor && (
        <div className="works-author-header">
          {headerAuthor.avatar && (
            <img
              className="wah-avatar"
              src={headerAuthor.avatar}
              alt=""
              onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
            />
          )}
          <div className="wah-info">
            <div className="wah-tags">
              <span className="wah-name">{authorName}</span>
              <span className={`platform-badge ${headerAuthor.platform === 'tiktok' ? 'tiktok' : 'douyin'}`}>{platformLabel}</span>
            </div>
            <div className="wah-uid-row">
              <span className="wah-uid mono">{headerAuthor.unique_id ? `@${headerAuthor.unique_id}` : secUid}</span>
              {headerAuthor.refreshed_at != null && (
                <span className="account-updated"><Clock size={11} strokeWidth={1.7} /> {timeAgo(headerAuthor.refreshed_at)}更新</span>
              )}
            </div>
          </div>
          <div className="spacer" />
          {headerAuthor.follower_count != null && (
            <div className="account-stats" style={{ margin: 0 }}>
              <span><b>{formatCount(headerAuthor.follower_count || 0)}</b> 粉丝</span>
              <span><b>{formatCount(headerAuthor.like_count || 0)}</b> 获赞</span>
              <span><b>{formatCount(headerAuthor.works_count || 0)}</b> 作品</span>
            </div>
          )}
          <button className="btn ghost sm wah-unfollow" disabled={unfollowing} onClick={unfollow} title="取消关注该账号">
            {unfollowing ? <Loader2 size={13} strokeWidth={2} className="spin" /> : <UserMinus size={13} strokeWidth={1.8} />}
            取消关注
          </button>
        </div>
      )}

      {error && (
        <div className="load-error-banner" role="alert">
          <AlertCircle size={15} strokeWidth={1.8} />
          <span className="load-error-text">{error}</span>
          <button type="button" className="btn ghost sm" onClick={() => setReloadKey((k) => k + 1)}>
            <RotateCw size={13} strokeWidth={1.8} /> 重试
          </button>
        </div>
      )}

      <div className="section-title" style={{ marginTop: 'var(--s-6)' }}>
        <span className="label">作品</span>
        <span className="count">{posts.length.toString().padStart(2, '0')}</span>
        <span className="line" />
      </div>

      <div className="tpl-grid">
        {loading ? (
          Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="tpl-card" style={{ pointerEvents: 'none', opacity: 0.5 }}>
              <div className="cover" />
              <div className="body"><div className="name">加载中…</div></div>
            </div>
          ))
        ) : posts.length === 0 ? (
          <div className="empty-state">
            该账号还没有已采集的作品。等沈括采集（state/benchmark/author_{secUid}/）后这里会列出。
          </div>
        ) : (
          posts.map((p) => (
            <PostCard key={p.aweme_id} post={p} creating={creating === p.aweme_id} onDerive={() => deriveFrom(p)} />
          ))
        )}
      </div>

      {/* 底部与页面留白 + 分割线 */}
      <div className="works-foot-divider" />
    </div>
  );
}

function PostCard({ post, creating, onDerive }: { post: AccountPost; creating: boolean; onDerive: () => void }) {
  const marker = post.aweme_id.slice(-2).toUpperCase();
  return (
    <article className="tpl-card" onClick={onDerive}>
      <div className="cover">
        {post.cover_url ? (
          <CoverImage src={post.cover_url} marker={marker} />
        ) : (
          <div className="cover-fallback" aria-hidden>
            <Sparkles size={26} strokeWidth={1.4} />
            <span className="cover-fallback-mark">{marker}</span>
          </div>
        )}
        {post.collected && <span className="run-pill" style={{ background: 'var(--accent)' }}>已采集</span>}
      </div>
      <div className="body">
        <div className="name" style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
          {post.desc || `作品 ${post.aweme_id.slice(-6)}`}
        </div>
        <div className="post-stats">
          <span><Heart size={12} strokeWidth={1.7} />{formatCount(post.digg)}</span>
          <span><MessageCircle size={12} strokeWidth={1.7} />{formatCount(post.comment)}</span>
          <span><Share2 size={12} strokeWidth={1.7} />{formatCount(post.share)}</span>
          <span><Star size={12} strokeWidth={1.7} />{formatCount(post.collect)}</span>
        </div>
        <div className="footer">
          <button
            className="btn ghost sm"
            onClick={(e) => { e.stopPropagation(); window.open(post.share_url, '_blank', 'noopener,noreferrer'); }}
          >
            <Play size={12} strokeWidth={1.8} /> 观看视频
          </button>
          <div style={{ flex: 1 }} />
          <button className="btn primary sm" disabled={creating} onClick={(e) => { e.stopPropagation(); onDerive(); }}>
            {creating ? <Loader2 size={12} strokeWidth={2} className="spin" /> : <Sparkles size={12} strokeWidth={1.8} />}
            {creating ? '创建中…' : '开始创作'}
          </button>
        </div>
      </div>
    </article>
  );
}
