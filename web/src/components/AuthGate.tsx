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
import { LandingPage } from './LandingPage';

type AuthContextValue = {
  authRequired: boolean;
  user: AuthUser | null;
  providers: { google: boolean; apple: boolean };
  logout: () => Promise<void>;
  startGoogleLogin: () => void;
  startAppleLogin: () => void;
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
  const [providers, setProviders] = useState({ google: true, apple: false });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const data = await api.getAuthStatus();
        if (cancelled) return;
        setAuthRequired(data.authRequired);
        setUser(data.authenticated ? data.user : null);
        if (data.providers) {
          setProviders({
            google: Boolean(data.providers.google),
            apple: Boolean(data.providers.apple),
          });
        }
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

  const startAppleLogin = useCallback(() => {
    window.location.href = '/api/auth/apple/login';
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
    () => ({ authRequired, user, providers, logout, startGoogleLogin, startAppleLogin }),
    [authRequired, user, providers, logout, startGoogleLogin, startAppleLogin],
  );

  if (loading) {
    return <div className="auth-loading-mask" aria-busy="true" />;
  }

  if (authRequired && !user) {
    return (
      <AuthContext.Provider value={value}>
        <LandingPage
          providers={providers}
          onGoogle={startGoogleLogin}
          onApple={startAppleLogin}
        />
      </AuthContext.Provider>
    );
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
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
