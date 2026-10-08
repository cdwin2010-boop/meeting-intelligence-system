/*
 * 새 프로젝트 등록 → 올리기 화면 복귀(작업 69-2b)
 * - returnTo 는 허용 목록으로만 받는다(열린 리다이렉트 방지). 그 밖의 값은 null → 기존 동작(상세로 이동).
 * - 올리기 입력값 임시 저장: 세션 저장소 mi.v2.uploadDraft. 파일·토큰·개인 정보는 저장하지 않는다.
 *   저장소가 막히거나 값이 깨져도 오류 없이 "저장 없음"으로 동작한다.
 */
import { MEETING_TYPES, type MeetingType } from "@/lib/v2/meeting-types";

export const UPLOAD_PATH = "/v2/upload";
export const UPLOAD_DRAFT_KEY = "mi.v2.uploadDraft";
/** 임시 저장 보존 기한(1시간) */
export const UPLOAD_DRAFT_TTL_MS = 60 * 60 * 1000;

const ALLOWED_RETURN_TO: readonly string[] = [UPLOAD_PATH];

/** 허용 목록에 정확히 일치하는 값만 통과. "//", "://", 백슬래시, 다른 경로는 모두 null */
export function parseReturnTo(raw: string | null | undefined): string | null {
  if (!raw) return null;
  if (raw.includes("//") || raw.includes("://") || raw.includes("\\")) return null;
  return ALLOWED_RETURN_TO.includes(raw) ? raw : null;
}

export const newProjectHref = (returnTo: string) => `/v2/projects/new?returnTo=${returnTo}`;
export const returnAfterCreate = (returnTo: string, projectId: number) => `${returnTo}?resume=1&newProject=${projectId}`;
export const RETURN_WITHOUT_PROJECT = `${UPLOAD_PATH}?resume=1`;

export interface UploadDraft {
  title: string;
  date: string;
  time: string;
  participantIds: number[];
  meetingType: MeetingType | "";
  savedAt: number;
}

export function saveUploadDraft(draft: Omit<UploadDraft, "savedAt">): void {
  try {
    window.sessionStorage.setItem(UPLOAD_DRAFT_KEY, JSON.stringify({ ...draft, savedAt: Date.now() }));
  } catch {
    // 저장소 접근이 막혀도 이동은 막지 않는다(복원 없이 진행)
  }
}

export function clearUploadDraft(): void {
  try {
    window.sessionStorage.removeItem(UPLOAD_DRAFT_KEY);
  } catch {
    // 무시
  }
}

/** 유효하고 1시간 이내인 임시 저장값. 없거나 깨졌거나 오래됐으면 null */
export function loadUploadDraft(): UploadDraft | null {
  try {
    const raw = window.sessionStorage.getItem(UPLOAD_DRAFT_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as Partial<UploadDraft> | null;
    if (!v || typeof v !== "object") return null;
    if (typeof v.savedAt !== "number" || !Number.isFinite(v.savedAt)) return null;
    const age = Date.now() - v.savedAt;
    if (age < 0 || age > UPLOAD_DRAFT_TTL_MS) return null;
    if (typeof v.title !== "string" || typeof v.date !== "string" || typeof v.time !== "string") return null;
    if (!Array.isArray(v.participantIds) || !v.participantIds.every((id) => Number.isInteger(id))) return null;
    const meetingType = v.meetingType === "" || MEETING_TYPES.includes(v.meetingType as MeetingType) ? (v.meetingType as MeetingType | "") : null;
    if (meetingType === null) return null;
    return { title: v.title, date: v.date, time: v.time, participantIds: v.participantIds, meetingType, savedAt: v.savedAt };
  } catch {
    return null;
  }
}
