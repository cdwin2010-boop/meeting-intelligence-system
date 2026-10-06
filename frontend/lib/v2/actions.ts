/*
 * 서버가 내려주는 허용 동작 이름(backend app/auth/actions.py 의 상수와 정확히 같다)과 판정 함수.
 * 화면은 직급·상태를 직접 비교하지 않고 이 이름이 allowedActions 에 있는지로 버튼을 보이거나 숨긴다.
 * 목록이 없거나 null 이면 모든 동작이 막힌 것으로 본다(안전한 쪽). 서버의 거부(403·409)가 최종 안전장치다.
 */

/** 회의록 단위 동작(회의록 상세 응답의 allowedActions) */
export const MEETING_ACTION = {
  confirmMeeting: "confirm_meeting",
  holdMeeting: "hold_meeting",
  resumeMeeting: "resume_meeting",
  endMeeting: "end_meeting",
  deleteMeeting: "delete_meeting",
  editMinutes: "edit_minutes",
  uploadUpdate: "upload_update",
  addItem: "add_item",
  editSpeakers: "edit_speakers",
  resolveChangeRequest: "resolve_change_request",
  requestChange: "request_change",
  downloadExcel: "download_excel",
  viewHistory: "view_history",
} as const;

/** 업무 단위 동작(상세 응답의 각 업무 allowedActions. 다른 응답에서는 null) */
export const ITEM_ACTION = {
  confirmItem: "confirm_item",
  closeItem: "close_item",
  deleteItem: "delete_item",
  setAssignee: "set_assignee",
  setDue: "set_due",
  requestChange: "request_change",
} as const;

export type ActionName = (typeof MEETING_ACTION)[keyof typeof MEETING_ACTION] | (typeof ITEM_ACTION)[keyof typeof ITEM_ACTION];

/** 이 동작이 허용됐는지. 목록이 없거나 null 이면 false */
export function can(actions: readonly string[] | null | undefined, name: ActionName): boolean {
  return Array.isArray(actions) && actions.includes(name);
}
