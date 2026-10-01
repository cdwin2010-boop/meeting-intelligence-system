/*
 * v2 API 타입. backend/app/api 의 응답 스키마와 필드 이름을 맞춘다(camelCase, id 는 숫자).
 */

/** 직급: 담당자 < 중간관리자 < 지시자 */
export type Rank = "staff" | "manager" | "executive";

/** GET /api/auth/me */
export interface Account {
  id: number;
  name: string;
  rank: Rank;
  tenantId: number;
}

/** POST /api/auth/login 응답 */
export interface TokenResponse {
  accessToken: string;
  tokenType: string;
}

export const RANK_LABEL: Record<Rank, string> = {
  staff: "담당자",
  manager: "중간관리자",
  executive: "지시자",
};

/** 관리자 이상(중간관리자·지시자) */
export const isManager = (rank: Rank | null | undefined): boolean => rank === "manager" || rank === "executive";
