// PREVIEW 节点 body：全屏 iframe，final_preview 模板负责播放/检查当前 episode。
// E 模式在 iframe 内按新 visual.finalPreview 配置做成品构图微调。

import { useCallback, useEffect, useRef } from 'react';

interface Props {
  jobId: string;
  onEditModeChange?: (editing: boolean) => void;
}

export function PreviewIframePanel({ jobId, onEditModeChange }: Props) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);

  const focusIframe = useCallback(() => {
    try {
      iframeRef.current?.contentWindow?.focus();
    } catch {
      iframeRef.current?.focus();
    }
  }, []);

  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      const data = event.data as { type?: string; editing?: unknown } | null;
      if (!data || data.type !== 'final-preview-edit-mode') return;
      onEditModeChange?.(data.editing === true);
    };
    window.addEventListener('message', handleMessage);
    return () => {
      window.removeEventListener('message', handleMessage);
      onEditModeChange?.(false);
    };
  }, [onEditModeChange]);

  return (
    <iframe
      ref={iframeRef}
      className="preview-iframe-full"
      src={`/preview/${jobId}/final-preview.html`}
      title="成品检查预览"
      loading="lazy"
      onLoad={() => {
        onEditModeChange?.(false);
        focusIframe();
      }}
    />
  );
}
