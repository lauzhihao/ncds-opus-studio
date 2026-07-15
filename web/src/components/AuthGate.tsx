// Google OAuth 门闸：对齐 vps-insight 的 authRequired / authenticated 语义。
// 未配置 OAuth 时 authRequired=false，直接放行子树；配置后未登录只显示登录页。

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { LogOut } from 'lucide-react';

import { api } from '../api/client';
import type { AuthUser } from '../api/types';

type AuthContextValue = {
  authRequired: boolean;
  user: AuthUser | null;
  logout: () => Promise<void>;
  startGoogleLogin: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within AuthGate');
  }
  return ctx;
}

/** 未开 auth 时也可用：user=null, authRequired=false */
export function useAuthOptional(): AuthContextValue | null {
  return useContext(AuthContext);
}

export function AuthGate({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [authRequired, setAuthRequired] = useState(false);
  const [user, setUser] = useState<AuthUser | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const data = await api.getAuthStatus();
        if (cancelled) return;
        setAuthRequired(data.authRequired);
        setUser(data.authenticated ? data.user : null);
      } catch {
        // /api/auth/me 失败时保守处理：若后端其实开了 auth，业务 API 会 401；本地未开则多数接口仍可用。
        if (!cancelled) {
          setAuthRequired(false);
          setUser(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const startGoogleLogin = useCallback(() => {
    window.location.href = '/api/auth/google/login';
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // session 已过期也清本地态
    }
    setUser(null);
    if (authRequired) {
      // 保持门闸，显示登录
    }
  }, [authRequired]);

  const value = useMemo(
    () => ({ authRequired, user, logout, startGoogleLogin }),
    [authRequired, user, logout, startGoogleLogin],
  );

  if (loading) {
    return <div className="auth-loading-mask" aria-busy="true" />;
  }

  if (authRequired && !user) {
    return (
      <AuthContext.Provider value={value}>
        <AuthLoginScreen onLogin={startGoogleLogin} />
      </AuthContext.Provider>
    );
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

function GoogleLogo({ className = '' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 48 48" aria-hidden="true">
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

function AuthLoginScreen({ onLogin }: { onLogin: () => void }) {
  return (
    <div className="auth-screen">
      <div className="auth-panel">
        <div className="auth-brand">NCDS Opus Studio</div>
        <h1>用 Google 登录</h1>
        <p className="auth-status">登录后使用内容生产工作台</p>
        <button className="auth-google-button" type="button" onClick={onLogin}>
          <GoogleLogo className="auth-google-button-logo" />
          <span>Continue with Google</span>
        </button>
      </div>
    </div>
  );
}

/** 顶栏用户菜单：仅 auth 开启且已登录时显示 */
export function AuthUserMenu() {
  const auth = useAuthOptional();
  const [open, setOpen] = useState(false);

  if (!auth?.authRequired || !auth.user) return null;

  const { user, logout } = auth;
  const label = user.name || user.email;
  const initial = (label.trim()[0] || '?').toUpperCase();

  return (
    <div className="auth-user-menu-wrap">
      <button
        type="button"
        className="auth-user-trigger"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {user.pictureUrl ? (
          <img src={user.pictureUrl} alt="" referrerPolicy="no-referrer" />
        ) : (
          <span className="auth-user-avatar">{initial}</span>
        )}
        <span className="auth-user-label">{label}</span>
      </button>
      {open && (
        <>
          <div className="auth-user-menu-backdrop" onClick={() => setOpen(false)} />
          <div className="auth-user-dropdown" role="menu">
            <div className="auth-user-email">{user.email}</div>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                void logout();
              }}
            >
              <LogOut size={14} strokeWidth={1.8} />
              退出登录
            </button>
          </div>
        </>
      )}
    </div>
  );
}
