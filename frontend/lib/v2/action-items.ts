/*
 * v2 업무 쓰기 API. 응답 모양은 backend/app/api/meeting_schemas.py 의 ActionItemOut(camelCase)과 맞춘다.
 * 권한·확정된 업무 제한은 서버가 판정한다(403 권한 없음, 400 같은 고객사 활성 계정 아님·확정 업무 빈칸, 409 삭제된 업무).
 * 종결·삭제는 사유(reason) 필수: 없거나 공백·2000자 초과는 422, 보류·종료·삭제된 회의록의 업무는 409.
 */
import type { ClosureKind } from "./closure-kinds";
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

/** 업무 종결: POST /api/action-items/{id}/close {reason, closureKind}. 확정된 업무만(확정 전 409), 권한(지시자·총괄·본인 담당 관리자)은 서버 판정 */
export function closeActionItem(itemId: number, reason: string, closureKind: ClosureKind, signal?: AbortSignal): Promise<ActionItem> {
  return request<ActionItem>(`/action-items/${encodeURIComponent(String(itemId))}/close`, {
    method: "POST",
    body: { reason, closureKind },
    signal,
  });
}

/** 업무 삭제(표시만): POST /api/action-items/{id}/delete {reason}. 지시자·총괄만(서버 판정), 삭제된 업무는 상세 목록에서 빠진다 */
export function deleteActionItem(itemId: number, reason: string, signal?: AbortSignal): Promise<ActionItem> {
  return request<ActionItem>(`/action-items/${encodeURIComponent(String(itemId))}/delete`, {
    method: "POST",
    body: { reason },
    signal,
  });
}

/** 기한 설정: PATCH /api/action-items/{id} 에 dueDate(YYYY-MM-DD) 또는 dueUndetermined:true 중 하나만 보낸다. 권한·종결·삭제 거부는 서버 문구 */
export function updateDue(
  itemId: number,
  due: { dueDate: string } | { dueUndetermined: true },
  signal?: AbortSignal,
): Promise<ActionItem> {
  return request<ActionItem>(`/action-items/${encodeURIComponent(String(itemId))}`, { method: "PATCH", body: due, signal });
}
