/*
 * v2 인증 API: 로그인(토큰 저장)·내 계정 조회·로그아웃(토큰 삭제).
 */
import { request } from "./http";
import { clearToken, setToken } from "./token";
import type { Account, TokenResponse } from "./types";

/** 로그인 성공 시 토큰을 저장한다. 실패(401)는 ApiError 로 던진다(만료 이벤트 없음). */
export async function login(loginId: string, password: string, signal?: AbortSignal): Promise<void> {
  const result = await request<TokenResponse>("/auth/login", {
    method: "POST",
    body: { loginId, password },
    signal,
    skipAuthExpiry: true,
  });
  setToken(result.accessToken);
}

export function me(signal?: AbortSignal): Promise<Account> {
  return request<Account>("/auth/me", { signal });
}

export function logout(): void {
  clearToken();
}
