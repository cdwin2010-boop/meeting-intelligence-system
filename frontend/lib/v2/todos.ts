/*
 * v2 "할 일" API: 로그인 계정의 할 일 묶음과 확정 안내(읽기 전용).
 * 응답 모양은 backend/app/api/me.py 의 Todos·NoticeOut 스키마(camelCase)와 맞춘다.
 */
import { request } from "./http";
import type { MissingField } from "./errors";

export type ConfirmKind = "manager" | "registration" | "period_elapsed" | "due_reached";

/** 각 목록은 최대 50건 + 전체 건수(total) */
export interface TodoList<T> {
  total: number;
  items: T[];
}

export interface AwaitingMeeting {
  id: number;
  title: string;
  autoConfirmAt: string | null;
}

export interface NeedsCompletionItem {
  meetingId: number;
  itemId: number;
  title: string;
  missingFields: MissingField[];
}

export interface MyItem {
  meetingId: number;
  itemId: number;
  title: string;
  dueDate: string | null; // YYYY-MM-DD
  dueUndetermined: boolean;
  status: "pending" | "confirmed";
}

export interface UnreadAutoConfirmed {
  entityType: "meeting" | "action_item";
  meetingId: number;
  itemId: number | null;
  title: string;
  confirmKind: ConfirmKind;
  confirmedAt: string;
}

/** GET /api/me/todos */
export interface Todos {
  awaitingConfirmMeetings: TodoList<AwaitingMeeting>;
  needsCompletionItems: TodoList<NeedsCompletionItem>;
  myItems: TodoList<MyItem>;
  unreadAutoConfirmed: TodoList<UnreadAutoConfirmed>;
}

/** GET /api/me/notices 항목(미확인 안내, 최신순). 확정 안내는 kind="confirmed_notice" */
export interface Notice {
  id: number;
  kind: string;
  entityType: "meeting" | "action_item";
  entityId: number;
  meetingId: number;
  payload: { confirmKind?: ConfirmKind; title?: string };
  createdAt: string;
}

export function getTodos(signal?: AbortSignal): Promise<Todos> {
  return request<Todos>("/me/todos", { signal });
}

export function getNotices(signal?: AbortSignal): Promise<Notice[]> {
  return request<Notice[]>("/me/notices", { signal });
}
