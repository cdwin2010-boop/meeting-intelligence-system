/*
 * v2 유사 업무 검색·대체 API(작업 71-1 파트 B, 백엔드 66-4).
 * - GET /api/action-items/{id}/similar: 같은 프로젝트 과거 회의록의 열린 업무를 점수순으로(읽기 전용, 자동 병합 없음)
 * - POST /api/action-items/{newItemId}/supersede {supersedesItemId, reason}: 직권 대체. 사유 필수(2000자 이내). 403·409·422 는 서버 문구. 같은 쌍을 다시 보내면 200
 * - 대체 요청은 수정 요청 생성(lib/v2/meetings.ts createChangeRequest)에 kind="supersede" 와 supersedesItemId 를 실어 보낸다
 */
import type { AccountRef } from "./meetings";
import { request } from "./http";

export interface SimilarItem {
  itemId: number;
  title: string;
  assignee: AccountRef | null;
  /** YYYY-MM-DD */
  dueDate: string | null;
  status: string;
  /** 0~100 */
  score: number;
  /** 예: "업무명 유사", "담당자 동일", "기한 근접" */
  reasons: string[];
  meeting: { id: number; title: string; heldAt: string };
}

export function getSimilarItems(itemId: number, signal?: AbortSignal): Promise<SimilarItem[]> {
  return request<SimilarItem[]>(`/action-items/${encodeURIComponent(String(itemId))}/similar`, { signal });
}

export interface SupersedeResult {
  oldItemId: number;
  newItemId: number;
  projectId: number;
  /** 이미 같은 쌍이면 false */
  changed: boolean;
}

export function supersedeItem(newItemId: number, input: { supersedesItemId: number; reason: string }, signal?: AbortSignal): Promise<SupersedeResult> {
  return request<SupersedeResult>(`/action-items/${encodeURIComponent(String(newItemId))}/supersede`, { method: "POST", body: input, signal });
}

/** 대체 연결의 상대 업무. 상대 회의록을 열람할 수 없으면 meetingTitle 이 null */
export interface SupersessionRef {
  itemId: number;
  meetingId: number;
  meetingTitle: string | null;
}

/** 이 화면이 아는 업무 상태 라벨(유사 업무 카드용) */
export const SIMILAR_STATUS_LABEL: Record<string, string> = { pending: "확정 대기", confirmed: "확정됨" };

/** 열람 권한이 없는 상대 회의록의 표시 글자 */
export const NO_ACCESS_MEETING_LABEL = "열람 권한이 없는 회의록";
