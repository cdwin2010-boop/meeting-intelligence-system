"use client";

/*
 * v2 로그인 상태 공유.
 *  - 마운트 시 저장된 토큰이 있으면 me() 로 확인 → authenticated, 없거나 실패하면 anonymous
 *  - http.ts 가 401 을 받아 보내는 "인증 만료" 이벤트를 받으면 anonymous 로 바꾼다
 *  - RequireAuth: anonymous 면 /v2/login?next=<현재 경로> 로 보낸다(next 는 /v2 아래만 허용)
 */
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import * as authApi from "@/lib/v2/auth";
import { isAbortError } from "@/lib/v2/errors";
import { AUTH_EXPIRED_EVENT } from "@/lib/v2/http";
import { loginPathFor } from "@/lib/v2/next-path";
import { getToken } from "@/lib/v2/token";
import type { Account } from "@/lib/v2/types";

export type AuthStatus = "loading" | "authenticated" | "anonymous";

interface AuthContextValue {
  account: Account | null;
  status: AuthStatus;
  login: (loginId: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [account, setAccount] = useState<Account | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");

  // 처음 열 때: 토큰이 있으면 서버에 내 계정을 물어 유효한지 확인
  useEffect(() => {
    const controller = new AbortController();
    if (!getToken()) {
      setStatus("anonymous");
      return () => controller.abort();
    }
    authApi
      .me(controller.signal)
      .then((found) => {
        setAccount(found);
        setStatus("authenticated");
      })
      .catch((error) => {
        if (isAbortError(error)) return;
        // 401 이면 http.ts 가 토큰을 이미 지웠다. 연결 실패도 일단 로그인 화면으로 보낸다
        setAccount(null);
        setStatus("anonymous");
      });
    return () => controller.abort();
  }, []);

  // 다른 요청에서 401 이 나면(토큰 만료 등) 로그아웃 상태로
  useEffect(() => {
    const onExpired = () => {
      setAccount(null);
      setStatus("anonymous");
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, []);

  const login = useCallback(async (loginId: string, password: string) => {
    await authApi.login(loginId, password);
    const found = await authApi.me();
    setAccount(found);
    setStatus("authenticated");
  }, []);

  const logout = useCallback(() => {
    authApi.logout();
    setAccount(null);
    setStatus("anonymous");
  }, []);

  const value = useMemo(() => ({ account, status, login, logout }), [account, status, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth 는 AuthProvider 안에서만 쓸 수 있습니다");
  return value;
}

/** 로그인한 사람에게만 children 을 보여 준다. anonymous 면 로그인 화면으로 이동 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  useEffect(() => {
    if (status !== "anonymous") return;
    const query = searchParams.toString();
    router.replace(loginPathFor(query ? `${pathname}?${query}` : pathname));
  }, [status, pathname, searchParams, router]);

  if (status !== "authenticated") {
    return (
      <p role="status" className="px-4 py-12 text-center text-sm text-mn-muted">
        로그인 상태를 확인하는 중…
      </p>
    );
  }
  return <>{children}</>;
}
