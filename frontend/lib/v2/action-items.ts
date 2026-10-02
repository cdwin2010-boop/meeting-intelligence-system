/*
 * v2 업무 쓰기 API. 응답 모양은 backend/app/api/meeting_schemas.py 의 ActionItemOut(camelCase)과 맞춘다.
 * 권한·확정된 업무 제한은 서버가 판정한다(403 권한 없음, 400 같은 고객사 활성 계정 아님·확정 업무 빈칸, 409 삭제된 업무).
 */
import { request } from "./http";
import type { ActionItem } from "./meetings";

/** 담당자 지정·변경: PATCH /api/action-items/{id} 에 assigneeId 만 보낸다(다른 항목은 그대로) */
export function updateAssignee(itemId: number, assigneeId: number, signal?: AbortSignal): Promise<ActionItem> {
  return request<ActionItem>(`/action-items/${encodeURIComponent(String(itemId))}`, {
    method: "PATCH",
    body: { assigneeId },
    signal,
  });
}

/** 업무 확정: POST /api/action-items/{id}/confirm. 이미 확정이면 그대로 200. 보완 필요는 409 {message, missingFields}, 권한 없음 403 */
export function confirmActionItem(itemId: number, signal?: AbortSignal): Promise<ActionItem> {
  return request<ActionItem>(`/action-items/${encodeURIComponent(String(itemId))}/confirm`, { method: "POST", signal });
}
