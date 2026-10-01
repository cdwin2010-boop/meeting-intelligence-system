/*
 * 로그인 토큰 보관(sessionStorage): 브라우저 탭을 닫으면 사라진다.
 * 서버 렌더링(window 없음)에서는 아무것도 하지 않고, 저장소 접근이 막힌 환경(사생활 보호 모드 등)의 예외는 삼킨다.
 * 토큰 값은 로그·URL 에 남기지 않는다.
 */

export const TOKEN_STORAGE_KEY = "mi.v2.accessToken";

function storage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function getToken(): string | null {
  try {
    return storage()?.getItem(TOKEN_STORAGE_KEY) ?? null;
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    storage()?.setItem(TOKEN_STORAGE_KEY, token);
  } catch {
    // 저장 실패 시에도 화면은 계속 동작한다(새로고침하면 다시 로그인)
  }
}

export function clearToken(): void {
  try {
    storage()?.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // 무시
  }
}
