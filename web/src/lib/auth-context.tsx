'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { apiFetch, apiAuth } from './api';

interface User {
  user_id: string;
  email: string;
  display_name: string | null;
  is_admin: boolean;
  created_at: string;
}

interface Tokens {
  access_token: string;
  refresh_token: string;
  expires_in: number;
}

interface AuthContextType {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (
    email: string,
    password: string,
    inviteToken: string,
    displayName?: string,
  ) => Promise<void>;
  logout: () => void;
  token: string | null;
}

const AuthContext = createContext<AuthContextType | null>(null);

const REFRESH_TOKEN_KEY = 'kura_rt';
const CLIENT_ID = 'kura-web';
const MOCK_AUTH = process.env.NEXT_PUBLIC_MOCK_AUTH === 'true';
const MOCK_AUTH_MODE_KEY = 'kura_mock_auth_mode';

const MOCK_USER: User = {
  user_id: 'mock-00000000-0000-0000-0000-000000000000',
  email: 'dev@kura.dev',
  display_name: 'Dev User',
  is_admin: true,
  created_at: '2026-01-01T00:00:00Z',
};

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearAuth = useCallback(() => {
    setUser(null);
    setAccessToken(null);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
    if (refreshTimerRef.current) {
      clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
  }, []);

  const fetchUser = useCallback(async (token: string): Promise<User | null> => {
    try {
      const res = await apiAuth('/v1/auth/me', token);
      if (!res.ok) return null;
      return res.json();
    } catch {
      return null;
    }
  }, []);

  const scheduleRefresh = useCallback(
    (refreshToken: string, expiresIn: number) => {
      if (refreshTimerRef.current) {
        clearTimeout(refreshTimerRef.current);
      }
      // Refresh 60 seconds before expiry
      const refreshMs = Math.max((expiresIn - 60) * 1000, 10_000);
      refreshTimerRef.current = setTimeout(async () => {
        try {
          const res = await apiFetch('/v1/auth/token', {
            method: 'POST',
            body: JSON.stringify({
              grant_type: 'refresh_token',
              refresh_token: refreshToken,
              client_id: CLIENT_ID,
            }),
          });
          if (!res.ok) {
            clearAuth();
            return;
          }
          const tokens: Tokens = await res.json();
          setAccessToken(tokens.access_token);
          localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
          scheduleRefresh(tokens.refresh_token, tokens.expires_in);
        } catch {
          clearAuth();
        }
      }, refreshMs);
    },
    [clearAuth],
  );

  const handleTokens = useCallback(
    async (tokens: Tokens) => {
      setAccessToken(tokens.access_token);
      localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
      scheduleRefresh(tokens.refresh_token, tokens.expires_in);

      const u = await fetchUser(tokens.access_token);
      if (u) setUser(u);
    },
    [fetchUser, scheduleRefresh],
  );

  // Bootstrap: try refresh token on mount (or mock in dev)
  useEffect(() => {
    if (MOCK_AUTH) {
      if (localStorage.getItem(MOCK_AUTH_MODE_KEY) === 'logged_out') {
        setUser(null);
        setAccessToken(null);
        setLoading(false);
        return;
      }
      setUser(MOCK_USER);
      setAccessToken('mock-token');
      setLoading(false);
      return;
    }
    const tryRestore = async () => {
      const rt = localStorage.getItem(REFRESH_TOKEN_KEY);
      if (!rt) {
        setLoading(false);
        return;
      }
      try {
        const res = await apiFetch('/v1/auth/token', {
          method: 'POST',
          body: JSON.stringify({
            grant_type: 'refresh_token',
            refresh_token: rt,
            client_id: CLIENT_ID,
          }),
        });
        if (!res.ok) {
          clearAuth();
          setLoading(false);
          return;
        }
        const tokens: Tokens = await res.json();
        await handleTokens(tokens);
      } catch {
        clearAuth();
      }
      setLoading(false);
    };
    tryRestore();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await apiFetch('/v1/auth/email/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error(err?.error || 'Login failed');
      }
      const tokens: Tokens = await res.json();
      await handleTokens(tokens);
    },
    [handleTokens],
  );

  const register = useCallback(
    async (
      email: string,
      password: string,
      inviteToken: string,
      displayName?: string,
    ) => {
      const regRes = await apiFetch('/v1/auth/register', {
        method: 'POST',
        body: JSON.stringify({
          email,
          password,
          invite_token: inviteToken,
          consent_anonymized_learning: true,
          display_name: displayName || undefined,
        }),
      });
      if (!regRes.ok) {
        const err = await regRes.json().catch(() => null);
        throw new Error(err?.error || 'Registration failed');
      }
      // Auto-login after registration
      await login(email, password);
    },
    [login],
  );

  const logout = useCallback(() => {
    clearAuth();
  }, [clearAuth]);

  const value = useMemo(
    () => ({ user, loading, login, register, logout, token: accessToken }),
    [user, loading, login, register, logout, accessToken],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
