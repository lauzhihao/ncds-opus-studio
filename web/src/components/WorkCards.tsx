// 共享卡片：封面图（先显示 CSS 占位，图片成功加载后覆盖）+ 作品卡（JobCard）。
// 从旧 TemplatesPage 抽出，供临时任务/作品列表复用，沿用 .tpl-card 样式。

import { useEffect, useState } from 'react';
import { ChevronRight, Clock, Loader2, PenBox, Plus, Trash2 } from 'lucide-react';

import type { JobSummary, SubscriptionAuthor } from '../api/types';
import { jobProgress } from '../config/agents';
import { domainByKey } from '../config/domains';
import { formatCount, formatDuration, timeAgo } from '../utils/format';
import { platformBadgeClass, platformDisplayName } from '../utils/platform';
import { parseTitleTags } from '../utils/title';

// 虚线"+"新增框：与作品卡同尺寸（grid stretch 对齐行高），点击触发弹窗。
export function AddCard({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" className="add-card" onClick={onClick} title={label} aria-label={label}>
      <Plus size={28} strokeWidth={1.4} />
      <span className="add-card-label">{label}</span>
    </button>
  );
}

// 监控对标账号卡（长期任务）：显示作者名称 + 数据 + 头像/封面。
export function AccountCard({ author, onOpen }: { author: SubscriptionAuthor; onOpen: () => void }) {
  const name = author.nickname || author.note || '未命名对标号';
  const marker = author.sec_uid.replace(/[^a-zA-Z0-9]/g, '').slice(-2).toUpperCase() || 'AC';
  const hasStats = author.follower_count != null;
  const platformLabel = platformDisplayName(author.platform);
  const domain = domainByKey(author.domain); // 领域 profile：徽标显示在平台徽标右侧
  return (
    <article className="tpl-card account-card" onClick={onOpen}>
      <div className="body">
        <div className="account-head">
          <AvatarCircle src={author.avatar} marker={marker} />
          <div className="account-ident">
            <div className="name">{name}</div>
            <div className="account-uid mono">
              {author.unique_id ? `@${author.unique_id}` : author.sec_uid}
            </div>
          </div>
        </div>
        {hasStats && (
          <div className="account-stats">
            <span><b>{formatCount(author.follower_count || 0)}</b> 粉丝</span>
            <span><b>{formatCount(author.like_count || 0)}</b> 获赞</span>
            <span><b>{formatCount(author.works_count || 0)}</b> 作品</span>
          </div>
        )}
        {author.refreshed_at != null && (
          <div className="account-updated">
            <Clock size={11} strokeWidth={1.7} />
            {timeAgo(author.refreshed_at)}更新
          </div>
        )}
        <div className="footer">
          <span className={`platform-badge ${platformBadgeClass(author.platform)}`}>{platformLabel}</span>
          {domain && <span className={`domain-badge ${domain.colorClass}`}>{domain.label}</span>}
          <div style={{ flex: 1 }} />
          <button className="btn sm icon-only accent" aria-label="查看作品" onClick={(e) => { e.stopPropagation(); onOpen(); }}>
            <ChevronRight size={14} strokeWidth={1.9} />
          </button>
        </div>
      </div>
    </article>
  );
}

// 账号卡作者头像：小圆圈展示（作者头像本就是小尺寸，用 object-fit:cover 铺满会糊）。
// 无头像或图片 404 时回退 sec_uid 末两位字母 marker 作 monogram 占位。
function AvatarCircle({ src, marker }: { src?: string | null; marker: string }) {
  const [ok, setOk] = useState(true);
  return src && ok ? (
    <img
      className="account-avatar"
      src={src}
      alt=""
      loading="lazy"
      draggable={false}
      onError={() => setOk(false)}
    />
  ) : (
    <div className="account-avatar account-avatar-fallback" aria-label="暂无头像">
      <span className="account-avatar-mark">{marker}</span>
    </div>
  );
}

// 封面图：默认先给 CSS 占位，真实封面成功加载后再覆盖，避免 404 / 慢响应期间空白。
export function CoverImage({ src }: { src?: string | null; marker?: string }) {
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setLoaded(false);
    setFailed(false);
  }, [src]);

  const shouldLoad = !!src && !failed;
  return (
    <>
      <div className="cover-fallback" aria-label="暂无封面">暂无封面</div>
      {shouldLoad && (
        <img
          className={`cover-img${loaded ? ' is-loaded' : ''}`}
          src={src}
          alt=""
          loading="lazy"
          draggable={false}
          onLoad={() => setLoaded(true)}
          onError={() => setFailed(true)}
        />
      )}
    </>
  );
}

// 封面右下角视频时长徽标：放在 position:relative 的封面容器里；秒数缺失/<=0 不渲染（拿不到就不显示）。
export function DurationBadge({ seconds }: { seconds?: number | null }) {
  if (!seconds || seconds <= 0) return null;
  return <span className="duration-badge">{formatDuration(seconds)}</span>;
}

export function JobCard({
  job,
  onOpen,
  onDelete,
}: {
  job: JobSummary;
  onOpen: () => void;
  onDelete: () => void;
}) {
  const updated = new Date(job.updated_at * 1000).toLocaleString('zh-CN', { hour12: false });
  const marker = (() => {
    const m = /(\d{2,})/.exec(job.pipeline_id);
    return m ? m[1] : job.job_id.slice(0, 2).toUpperCase();
  })();
  const progress = jobProgress(job.node_status);
  // 「作品 + 话题标签」：JobSummary 只有 title 字符串，话题从中正则提取（60 字后被截断的标签提取不到）
  const { title, tags } = parseTitleTags(job.title);
  const coverSrc = `/jobs/${job.job_id}/cover?v=${encodeURIComponent(String(job.updated_at || 0))}`;
  return (
    <article className={`tpl-card work-card${job.running ? ' is-running' : ''}`} onClick={onOpen}>
      <div className="cover">
        <CoverImage src={coverSrc} marker={marker} />
        {job.running && (
          <>
            <span className="run-pill"><span className="run-dot" />执行中</span>
            <div className="tpl-running-mask" aria-label="执行中">
              <Loader2 size={22} strokeWidth={2} className="spin" />
            </div>
          </>
        )}
      </div>
      <div className="body">
        <div className="name">{title || '未命名作品'}</div>
        <div className="name-tags">
          {tags.map((t) => (
            <span key={t} className="title-tag">#{t}</span>
          ))}
        </div>
        <div className="desc">
          <Clock size={11} strokeWidth={1.6} style={{ verticalAlign: '-2px', marginRight: 4 }} />
          上次更新 {updated}
        </div>
        <div className="footer">
          <span className={`progress-light ${progress.light}`} title={`设计进度：${progress.agentName}`}>
            <span className="dot" />
            {progress.agentName}
          </span>
          <div style={{ flex: 1 }} />
          <button
            className="btn sm icon-only accent"
            aria-label="进入画布"
            onClick={(e) => { e.stopPropagation(); onOpen(); }}
          >
            <PenBox size={13} strokeWidth={1.9} />
          </button>
          <button
            className="btn sm icon-only danger"
            aria-label={job.running ? '执行中，暂不可删除' : '删除'}
            disabled={!!job.running}
            onClick={(e) => { e.stopPropagation(); if (job.running) return; onDelete(); }}
          >
            <Trash2 size={12} strokeWidth={1.6} />
          </button>
        </div>
      </div>
    </article>
  );
}
