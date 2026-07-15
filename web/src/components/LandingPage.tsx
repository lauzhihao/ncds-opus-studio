// 未登录落地页：打字机文案 + 横向登录按钮 + 左下角假终端引导登录。

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ClipboardEvent,
  type FormEvent,
  type KeyboardEvent,
} from 'react';

const TYPE_LINES = [
  '还在玩￥36/15秒的抽卡短视频？',
  '快来跟我一起',
  '成大事',
] as const;

const TERM_REPLIES = [
  '正在分析链接...',
  '正在下载作品..',
  '正在登录...',
] as const;

type Providers = { google: boolean; apple: boolean };

export function LandingPage({
  providers,
  onGoogle,
  onApple,
}: {
  providers: Providers;
  onGoogle: () => void;
  onApple: () => void;
}) {
  const [joinOpen, setJoinOpen] = useState(false);
  const [typeReady, setTypeReady] = useState(false);

  return (
    <div className="landing">
      <header className="landing-top">
        <div className="landing-brand" aria-label="NCDS Opus Studio">
          <span className="landing-logo" aria-hidden>
            <BrandMark />
          </span>
          <span className="landing-title">NCDS Opus Studio</span>
        </div>
      </header>

      <main className="landing-center">
        <TypewriterBlock lines={[...TYPE_LINES]} onReady={() => setTypeReady(true)} />
        <div className={`landing-auth-row${typeReady ? ' is-ready' : ''}`}>
          {providers.google && (
            <button type="button" className="landing-btn landing-btn-google" onClick={onGoogle}>
              <GoogleLogo />
              <span>Continue with Google</span>
            </button>
          )}
          {providers.apple && (
            <button type="button" className="landing-btn landing-btn-apple" onClick={onApple}>
              <AppleLogo />
              <span>Continue with Apple</span>
            </button>
          )}
        </div>
      </main>

      <aside className="landing-term-wrap">
        <TerminalPanel
          onNeedLogin={() => setJoinOpen(true)}
        />
      </aside>

      {joinOpen && (
        <JoinModal
          providers={providers}
          onGoogle={onGoogle}
          onApple={onApple}
          onClose={() => setJoinOpen(false)}
        />
      )}
    </div>
  );
}

function TypewriterBlock({ lines, onReady }: { lines: string[]; onReady: () => void }) {
  const [lineIdx, setLineIdx] = useState(0);
  const [charIdx, setCharIdx] = useState(0);
  const [done, setDone] = useState(false);
  const readyFired = useRef(false);
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;

  // 打字推进
  useEffect(() => {
    if (done) return;
    const current = lines[lineIdx] ?? '';
    if (charIdx < current.length) {
      const t = window.setTimeout(() => setCharIdx((c) => c + 1), 48 + Math.random() * 36);
      return () => window.clearTimeout(t);
    }
    // 本行打完 → 下一行
    if (lineIdx < lines.length - 1) {
      const t = window.setTimeout(() => {
        setLineIdx((i) => i + 1);
        setCharIdx(0);
      }, lineIdx === 0 ? 520 : 380);
      return () => window.clearTimeout(t);
    }
    // 最后一行打完
    setDone(true);
  }, [charIdx, lineIdx, lines, done]);

  // 「成大事」完成后单独触发登录按钮（避免 setDone 重跑 effect 清掉 setTimeout）
  useEffect(() => {
    if (!done || readyFired.current) return;
    readyFired.current = true;
    const t = window.setTimeout(() => onReadyRef.current(), 420);
    return () => window.clearTimeout(t);
  }, [done]);

  return (
    <div className="landing-type" lang="ja">
      {lines.map((line, i) => {
        if (i > lineIdx) return null;
        const text = i < lineIdx ? line : line.slice(0, charIdx);
        const isActive = i === lineIdx && !done;
        const isHero = i === lines.length - 1;
        return (
          <p
            key={line}
            className={`landing-type-line${isHero ? ' is-hero' : ''}${isActive ? ' is-active' : ''}`}
            lang="ja"
          >
            {text}
            {isActive && <span className="landing-caret" aria-hidden />}
          </p>
        );
      })}
    </div>
  );
}

function TerminalPanel({ onNeedLogin }: { onNeedLogin: () => void }) {
  const [input, setInput] = useState('');
  const [lines, setLines] = useState<string[]>([
    'ncds-opus ~ share',
    '粘贴作品分享链接，回车或点 Run',
  ]);
  const [busy, setBusy] = useState(false);
  const [focused, setFocused] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [lines]);

  const run = useCallback(
    async (raw: string) => {
      const link = raw.trim();
      if (!link || busy) return;
      setBusy(true);
      setInput('');
      setLines((prev) => [...prev, `> ${link.length > 64 ? `${link.slice(0, 64)}…` : link}`]);

      for (let i = 0; i < TERM_REPLIES.length; i++) {
        await sleep(650 + i * 180);
        setLines((prev) => [...prev, TERM_REPLIES[i]]);
      }
      await sleep(420);
      setBusy(false);
      onNeedLogin();
    },
    [busy, onNeedLogin],
  );

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void run(input);
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      e.preventDefault();
      void run(input);
    }
  }

  function onPaste(e: ClipboardEvent<HTMLInputElement>) {
    const text = e.clipboardData.getData('text');
    if (text.trim().length > 8) {
      // 粘贴后自动跑一轮（常见分享口令/链接）
      window.setTimeout(() => {
        void run(text);
      }, 80);
    }
  }

  return (
    <div
      className={`landing-term${focused ? ' is-focused' : ''}`}
      onClick={() => inputRef.current?.focus()}
      role="region"
      aria-label="分享链接终端"
    >
      <div className="landing-term-chrome">
        <span className="dot r" />
        <span className="dot y" />
        <span className="dot g" />
        <span className="landing-term-title">opus — bash</span>
      </div>
      <div className="landing-term-body">
        {lines.map((ln, i) => (
          <div key={`${i}-${ln.slice(0, 12)}`} className="landing-term-line">
            {ln}
          </div>
        ))}
        <form className="landing-term-input-row" onSubmit={onSubmit}>
          <span className="landing-term-prompt">$</span>
          <input
            ref={inputRef}
            className="landing-term-input"
            value={input}
            disabled={busy}
            placeholder="paste share url…"
            spellCheck={false}
            autoComplete="off"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            onPaste={onPaste}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
          />
          <button type="submit" className="landing-term-run" disabled={busy || !input.trim()}>
            Run
          </button>
        </form>
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

function JoinModal({
  providers,
  onGoogle,
  onApple,
  onClose,
}: {
  providers: Providers;
  onGoogle: () => void;
  onApple: () => void;
  onClose: () => void;
}) {
  return (
    <div className="landing-modal-root" role="dialog" aria-modal="true" aria-labelledby="join-us-title">
      <div className="landing-modal-backdrop" onClick={onClose} />
      <div className="landing-modal">
        <button type="button" className="landing-modal-close" onClick={onClose} aria-label="关闭">
          ×
        </button>
        <h2 id="join-us-title" className="landing-modal-title">
          JOIN US
        </h2>
        <p className="landing-modal-sub">一起搞大事</p>
        <div className="landing-modal-actions">
          {providers.google && (
            <button type="button" className="landing-btn landing-btn-google" onClick={onGoogle}>
              <GoogleLogo />
              <span>Continue with Google</span>
            </button>
          )}
          {providers.apple && (
            <button type="button" className="landing-btn landing-btn-apple" onClick={onApple}>
              <AppleLogo />
              <span>Continue with Apple</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function sleep(ms: number) {
  return new Promise<void>((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function BrandMark() {
  // public/neng.png → Vite base 下为 /studio/neng.png
  const src = `${import.meta.env.BASE_URL}neng.png`;
  return <img className="landing-logo-img" src={src} alt="" width={48} height={48} />;
}

function GoogleLogo() {
  return (
    <svg className="landing-btn-icon" viewBox="0 0 48 48" aria-hidden>
      <path
        fill="#4285F4"
        d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v8.5h12.4c-.5 2.8-2.1 5.2-4.5 6.8v5.6h7.2c4.2-3.9 7-9.6 7-16.4z"
      />
      <path
        fill="#34A853"
        d="M24 47c6 0 11-2 14.7-5.4l-7.2-5.6c-2 1.3-4.5 2.1-7.5 2.1-5.8 0-10.7-3.9-12.4-9.2H4.2v5.8C7.9 42 15.4 47 24 47z"
      />
      <path
        fill="#FBBC05"
        d="M11.6 28.9c-.4-1.3-.7-2.7-.7-4.2s.2-2.9.7-4.2v-5.8H4.2C2.8 17.6 2 21.2 2 24.7s.8 7.1 2.2 10l7.4-5.8z"
      />
      <path
        fill="#EA4335"
        d="M24 11.3c3.3 0 6.2 1.1 8.5 3.3l6.4-6.4C35 4.6 30 2.3 24 2.3 15.4 2.3 7.9 7.3 4.2 14.7l7.4 5.8c1.7-5.3 6.6-9.2 12.4-9.2z"
      />
    </svg>
  );
}

function AppleLogo() {
  return (
    <svg className="landing-btn-icon" viewBox="0 0 24 24" aria-hidden>
      <path
        fill="currentColor"
        d="M16.37 12.63c.03 3.23 2.83 4.31 2.86 4.32-.02.08-.45 1.53-1.47 3.03-.89 1.3-1.81 2.59-3.26 2.62-1.43.03-1.89-.85-3.53-.85-1.64 0-2.15.82-3.5.87-1.4.05-2.47-1.4-3.37-2.69C2.3 17.3.96 13.3 2.72 10.3c.88-1.49 2.45-2.43 4.15-2.46 1.3-.02 2.52.87 3.53.87 1 0 2.56-1.08 4.32-.92.74.03 2.81.3 4.14 2.25-.11.07-2.47 1.44-2.49 4.59zM13.9 5.67c.7-.85 1.17-2.03 1.04-3.21-1.01.04-2.23.67-2.95 1.52-.65.75-1.22 1.95-1.07 3.1 1.13.09 2.28-.57 2.98-1.41z"
      />
    </svg>
  );
}
