/*
 * 돌아갈 화면(returnTo) 표시용 경로 검사(작업 85-1). 할 일 화면 → 회의록 상세로 갔다가 되돌아올 때 쓴다.
 * 같은 사이트의 /v2 아래 경로만 허용한다(외부 주소·//host·\ 포함·제어 문자는 거절 → 돌아가기 링크를 그리지 않는다).
 */
import { safeNextPath, V2_HOME } from "./next-path";

export const RETURN_TO_PARAM = "returnTo";

/** 값이 유효한 내부 경로면 그대로, 아니면 null(safeNextPath 가 값을 바꾸면 거절로 본다) */
export function validReturnTo(raw: string | null | undefined): string | null {
  if (!raw) return null;
  return safeNextPath(raw) === raw ? raw : null;
}

/** path 뒤에 returnTo 를 붙인다. from 이 유효하지 않으면 path 그대로 */
export function withReturnTo(path: string, from: string | null | undefined, extra: Record<string, string | number> = {}): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(extra)) params.set(key, String(value));
  const valid = validReturnTo(from);
  if (valid) params.set(RETURN_TO_PARAM, valid);
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

/** 돌아가기 링크 글자: 할 일 화면이면 "할 일로", 그 밖의 내부 화면은 "이전 화면으로" */
export function returnLabel(path: string): string {
  return path === V2_HOME ? "할 일로 돌아가기" : "이전 화면으로 돌아가기";
}
