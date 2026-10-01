/*
 * 로그인 뒤 돌아갈 경로(next) 검사: 외부 사이트로 보내는 것을 막는다(오픈 리다이렉트 방지).
 * 같은 사이트의 /v2 아래 경로만 허용하고, 그 밖의 값은 모두 /v2 로 바꾼다.
 */
export const V2_HOME = "/v2";
export const V2_LOGIN = "/v2/login";

export function safeNextPath(next: string | null | undefined): string {
  if (!next) return V2_HOME;
  // "//host", "/\host", 제어 문자 등은 브라우저가 외부 주소로 해석할 수 있으므로 거절
  if (!next.startsWith("/") || next.startsWith("//") || next.includes("\\") || /[\u0000-\u001f]/.test(next)) {
    return V2_HOME;
  }
  // /v2, /v2/..., /v2?..., /v2#... 만 허용(/v2xyz 같은 다른 경로는 제외)
  if (!/^\/v2(?:[/?#]|$)/.test(next)) return V2_HOME;
  // 로그인 화면으로 다시 보내는 것은 의미가 없으므로 홈으로
  if (next === V2_LOGIN || next.startsWith(`${V2_LOGIN}?`) || next.startsWith(`${V2_LOGIN}/`)) return V2_HOME;
  return next;
}

export function loginPathFor(currentPath: string): string {
  return `${V2_LOGIN}?next=${encodeURIComponent(safeNextPath(currentPath))}`;
}
