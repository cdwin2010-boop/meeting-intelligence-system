/*
 * 보조창(오른쪽 메뉴·처리 현황 패널) 좌우 위치 설정(작업 85-1). 값은 left | right(기본 right)만 허용한다.
 * 저장은 쿠키(1년, 서버 렌더에서 읽어 깜빡임을 막는다). DB 에는 저장하지 않는다.
 * 쿠키 이름은 계정과 무관한 브라우저 설정이다.
 */
export type SidePosition = "left" | "right";

export const SIDE_COOKIE = "mi_side_position";
export const DEFAULT_SIDE: SidePosition = "right";
const ONE_YEAR_SECONDS = 60 * 60 * 24 * 365;

/** 알 수 없는 값(변조 포함)은 기본값 right */
export function parseSidePosition(value: unknown): SidePosition {
  return value === "left" ? "left" : DEFAULT_SIDE;
}

/** 브라우저에서 쿠키를 쓴다. 쿠키가 막혀 있어도 이번 방문 동안 화면은 바뀐다 */
export function writeSideCookie(side: SidePosition): void {
  try {
    document.cookie = `${SIDE_COOKIE}=${side}; Path=/; Max-Age=${ONE_YEAR_SECONDS}; SameSite=Lax`;
  } catch {
    // 저장 실패는 무시
  }
}
