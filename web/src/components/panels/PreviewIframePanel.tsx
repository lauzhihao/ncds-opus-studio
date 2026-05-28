// PREVIEW 节点 body：全屏 iframe，015 模板自带 edit-mode + Inspector + Tweaks 抽屉。
//
// 抽屉里所有微调（拖 overlay / 改字号 / 切 palette / 加删 overlay）由 015 内置的
// edit-mode.js + inspector.jsx + tweaks.jsx 直接 fetch ./preview/{job_id}/__save_*
// 端点回写到 video-jobs/{job_id}/02_rw/episode.json。React 这边不再持有 episode
// 状态，不做表单——iframe 充满 body 即可。

interface Props {
  jobId: string;
}

export function PreviewIframePanel({ jobId }: Props) {
  return (
    <iframe
      className="preview-iframe-full"
      src={`/preview/${jobId}/015-draft.html`}
      title="015 预览 + 编辑"
      loading="lazy"
    />
  );
}
