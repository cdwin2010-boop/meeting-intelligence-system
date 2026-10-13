/*
 * 회의 일시 입력 도우미(작업 88-4). 순수 함수만 둔다(다른 파일을 import 하지 않아 Node test runner 로 검증: lib/meeting-at.test.mjs).
 * - 입력은 사용자 현지 날짜·시각, 서버에는 오프셋이 붙은 ISO 8601 로 보낸다(서버가 UTC 로 저장). 오프셋을 같이 보내 날짜가 밀리지 않는다.
 * - 현재보다 미래는 거부한다. 시계 오차 허용 범위는 서버 설정(MEETING_AT_FUTURE_TOLERANCE_MINUTES, 기본 10분)과 같은 값을 쓴다
 *   (NEXT_PUBLIC_MEETING_AT_FUTURE_TOLERANCE_MINUTES 로 맞출 수 있다). 최종 판정은 서버다.
 */

const pad = (n: number) => String(n).padStart(2, "0");

export const MEETING_AT_REQUIRED_MESSAGE = "회의 일시를 입력하세요.";
export const MEETING_AT_FUTURE_MESSAGE = "회의 일시는 현재 시각보다 미래일 수 없습니다. 날짜와 시각을 다시 확인해 주세요.";

/** 미래 허용 오차(ms). 환경변수가 없거나 잘못되면 10분 */
export function futureToleranceMs(raw?: string | undefined): number {
  const minutes = Number(raw ?? (typeof process !== "undefined" ? process.env?.NEXT_PUBLIC_MEETING_AT_FUTURE_TOLERANCE_MINUTES : undefined));
  return (Number.isFinite(minutes) && minutes >= 0 ? minutes : 10) * 60_000;
}

/** 시각(ms) → 현지 date·time 입력 기본 형식 */
export function partsFromTimestamp(ms: number): { date: string; time: string } {
  const d = new Date(ms);
  return { date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`, time: `${pad(d.getHours())}:${pad(d.getMinutes())}` };
}

/** 현지 날짜·시각 → 오프셋 붙은 ISO 8601 (예: 2026-10-01T13:20:00+09:00). 잘못된 값이면 null */
export function toOffsetIso(date: string, time: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^\d{2}:\d{2}$/.test(time)) return null;
  const local = new Date(`${date}T${time}:00`);
  if (Number.isNaN(local.getTime())) return null;
  const offsetMin = -local.getTimezoneOffset(); // 그 날짜 기준(서머타임 반영)
  const sign = offsetMin >= 0 ? "+" : "-";
  const abs = Math.abs(offsetMin);
  return `${date}T${time}:00${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

/** 이 일시가 현재보다 미래(허용 오차 초과)인가. 해석할 수 없으면 false(서버가 판정) */
export function isFutureMeetingAt(iso: string, nowMs: number = Date.now(), toleranceMs: number = futureToleranceMs()): boolean {
  const at = new Date(iso).getTime();
  return Number.isFinite(at) && at > nowMs + toleranceMs;
}

/**
 * 파일 시각(File.lastModified)을 제안값으로 쓸 수 있는가: 양수이고 현재(오차 포함)보다 미래가 아니어야 한다.
 * 쓸 수 없으면 null → 빈칸으로 두고 필수 입력을 안내한다.
 */
export function suggestionFromTimestamp(ms: number | null | undefined, nowMs: number = Date.now(), toleranceMs: number = futureToleranceMs()): { date: string; time: string } | null {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms <= 0 || ms > nowMs + toleranceMs) return null;
  return partsFromTimestamp(ms);
}
