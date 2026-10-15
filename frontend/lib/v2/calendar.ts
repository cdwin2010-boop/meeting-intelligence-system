/*
 * v2 개인별 할 일 캘린더(작업 B-2): GET /api/me/calendar 와 날짜 계산 도우미.
 * 마감일은 "YYYY-MM-DD" 문자열 그대로 다룬다. Date 로 바꿀 때는 UTC 기준으로만 계산해 시간대 때문에 하루가 밀리지 않게 한다.
 */
import { request } from "./http";

export interface CalendarItem {
  meetingId: number;
  meetingTitle: string;
  itemId: number;
  title: string;
  assignee: { id: number; name: string } | null;
  dueDate: string | null; // YYYY-MM-DD
  dueUndetermined: boolean;
  status: "pending" | "confirmed" | "closed";
}

export interface CalendarList {
  total: number;
  items: CalendarItem[];
}

export type CalendarScope = "self" | "all";

export interface CalendarResponse {
  from: string;
  to: string;
  scope: CalendarScope;
  dated: CalendarList;
  undetermined: CalendarList;
}

/** from~to(양 끝 포함)의 마감일 업무와 기한 미확정 업무. scope=all 은 지시자·관리자만(그 밖은 403) */
export function getCalendar(from: string, to: string, scope: CalendarScope, signal?: AbortSignal): Promise<CalendarResponse> {
  const query = `from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}&scope=${scope}`;
  return request<CalendarResponse>(`/me/calendar?${query}`, { signal });
}

/** 캘린더 파일(.ics) 내려받기(작업 E-1): 기한이 확정된 진행 중 업무만. 인증 헤더가 필요해 fetch 로 받는다 */
export function downloadCalendarIcs(scope: CalendarScope, signal?: AbortSignal): Promise<Blob> {
  return request<Blob>(`/me/calendar.ics?scope=${scope}`, { asBlob: true, signal });
}

export const CALENDAR_ICS_FILE_NAME = "aidots-tasks.ics";

/* ---------------- 날짜 문자열 계산(UTC 기준, 시간대 변환 없음) ---------------- */

const pad = (n: number) => String(n).padStart(2, "0");

export function ymd(year: number, month1: number, day: number): string {
  return `${year}-${pad(month1)}-${pad(day)}`;
}

function toUtc(value: string): Date {
  const [y, m, d] = value.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

function fromUtc(date: Date): string {
  return ymd(date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate());
}

export function addDays(value: string, days: number): string {
  const date = toUtc(value);
  date.setUTCDate(date.getUTCDate() + days);
  return fromUtc(date);
}

/** 0=일요일 … 6=토요일 */
export function weekdayOf(value: string): number {
  return toUtc(value).getUTCDay();
}

/** 그 날이 속한 주의 일요일 */
export function weekStart(value: string): string {
  return addDays(value, -weekdayOf(value));
}

export function addMonths(value: string, months: number): string {
  const [y, m, d] = value.split("-").map(Number);
  const target = new Date(Date.UTC(y, m - 1 + months, 1));
  const last = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
  return ymd(target.getUTCFullYear(), target.getUTCMonth() + 1, Math.min(d, last));
}

export function sameMonth(a: string, b: string): boolean {
  return a.slice(0, 7) === b.slice(0, 7);
}

/** 월간 보기: 그 달 1일이 속한 주의 일요일부터 6주(42일) */
export function monthGrid(anchor: string): string[] {
  const first = `${anchor.slice(0, 7)}-01`;
  const start = weekStart(first);
  return Array.from({ length: 42 }, (_, i) => addDays(start, i));
}

/** 주간 보기: 그 주의 일요일부터 7일 */
export function weekGrid(anchor: string): string[] {
  const start = weekStart(anchor);
  return Array.from({ length: 7 }, (_, i) => addDays(start, i));
}

/** 오늘(사용자 PC 의 달력 날짜). 서버 시각이나 UTC 가 아니라 화면을 보는 사람의 날짜 */
export function todayYmd(): string {
  const now = new Date();
  return ymd(now.getFullYear(), now.getMonth() + 1, now.getDate());
}

export const WEEKDAY_LABELS = ["일", "월", "화", "수", "목", "금", "토"] as const;

export function dayLabel(value: string): string {
  const [, m, d] = value.split("-").map(Number);
  return `${m}월 ${d}일 ${WEEKDAY_LABELS[weekdayOf(value)]}요일`;
}

/** 칸 하나에 펼쳐 보이는 업무 수. 넘으면 "+N건"으로 접는다 */
export const DAY_CELL_VISIBLE = 3;

export function groupByDate(items: CalendarItem[]): Map<string, CalendarItem[]> {
  const map = new Map<string, CalendarItem[]>();
  for (const item of items) {
    if (!item.dueDate) continue;
    const list = map.get(item.dueDate);
    if (list) list.push(item);
    else map.set(item.dueDate, [item]);
  }
  return map;
}
