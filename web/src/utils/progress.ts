export function friendlyProgressText(name: string, progress: string | null | undefined): string {
  const msg = (progress ?? '').trim();
  if (!msg) return '';

  if (name === 'lines' && /台词结构|结构化为 beats|beats|Opus|opus|AGY|DeepSeek|SCodex|模型/.test(msg)) {
    return '正在准备视觉方案...';
  }
  if (name === 'storyboard' && /director agent|分镜|beats|Opus|opus|AGY|DeepSeek|SCodex|模型/.test(msg)) {
    return '正在生成视觉方案...';
  }
  if (name === 'image') {
    return friendlyImageProgress(msg);
  }
  return msg;
}

function friendlyImageProgress(msg: string): string {
  if (/图片服务响应超时|图片服务请求过于频繁|图片服务暂时不可用|图片生成失败/.test(msg)) {
    return msg;
  }
  if (/read operation timed out|TimeoutError|timed out|timeout/i.test(msg)) {
    return '图片服务响应超时，系统会自动重试或继续处理其他场景。';
  }
  if (/HTTP 429|rate limit/i.test(msg)) {
    return '图片服务请求过于频繁，系统会自动重试或继续处理其他场景。';
  }
  if (/HTTP 50[0234]|upstream/i.test(msg)) {
    return '图片服务暂时不可用，系统会自动重试或继续处理其他场景。';
  }
  if (/gpt-image|Traceback|File ".*", line|ssl\.py|\^\^\^\^\^/.test(msg)) {
    return '图片生成暂时失败，系统会自动重试或继续处理其他场景。';
  }
  return msg;
}
