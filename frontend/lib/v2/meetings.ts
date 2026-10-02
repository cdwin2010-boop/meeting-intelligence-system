/*
 * v2 회의록 조회 API(읽기 전용: 목록·상세·전사문). 응답 모양은 backend/app/api/meeting_schemas.py(camelCase)와 맞춘다.
 * 열람 권한은 서버가 판정한다(관리자 이상은 고객사 전체, 담당자는 참석·담당·등록한 회의록만).
 */
import { request } from "./http";
import type { MissingField } from "./errors";
import type { ConfirmKind } from "./todos";

export type MeetingStatus = "processing" | "awaiting_confirmation" | "confirmed" | "failed" | "no_content";

export interface MeetingListItem {
  id: number;
  title: string;
  heldAt: string;
  registeredBy: { id: number; name: string };
  origin: string;
  status: MeetingStatus;
  confirmKind: ConfirmKind | null;
  itemCount: number;
  needsCompletionCount: number;
  autoConfirmAt: string | null;
}

/** GET /api/meetings (회의 일시 최신순, page 는 1부터) */
export interface MeetingListPage {
  items: MeetingListItem[];
  total: number;
  page: number;
  size: number;
}

export const MEETINGS_PAGE_SIZE = 20;

export function listMeetings(page: number, signal?: AbortSignal): Promise<MeetingListPage> {
  const query = new URLSearchParams({ page: String(page), size: String(MEETINGS_PAGE_SIZE) });
  return request<MeetingListPage>(`/meetings?${query.toString()}`, { signal });
}

export interface AccountRef {
  id: number;
  name: string;
}

export type ActionItemStatus = "pending" | "confirmed" | "closed";

/** 상세의 업무 한 건(삭제된 업무는 서버가 빼고 준다) */
export interface ActionItem {
  id: number;
  title: string;
  assignee: AccountRef | null;
  dueDate: string | null; // YYYY-MM-DD
  dueUndetermined: boolean;
  status: ActionItemStatus;
  confirmKind: ConfirmKind | null;
  evidenceStartSec: number | null;
  evidenceQuote: string | null;
  needsCompletion: boolean;
  missingFields: MissingField[];
}

/** GET /api/meetings/{id} (권한 없음·다른 고객사·없음은 모두 404) */
export interface MeetingDetail {
  id: number;
  title: string;
  heldAt: string;
  summary: string;
  decisions: unknown[];
  status: MeetingStatus;
  confirmKind: ConfirmKind | null;
  confirmedBy: AccountRef | null;
  confirmedAt: string | null;
  firstCreatedAt: string;
  autoConfirmAt: string | null;
  registeredBy: AccountRef;
  origin: string;
  participants: AccountRef[];
  actionItems: ActionItem[];
  recentEvents: { eventType: string; actor: AccountRef | null; createdAt: string }[];
}

/** 전사 구간: 서버가 엔진 결과를 그대로 저장하므로 키는 snake_case(start_sec 등)다 */
export interface TranscriptSegment {
  speaker?: string | null;
  start_sec?: number | null;
  end_sec?: number | null;
  text?: string | null;
}

/** GET /api/meetings/{id}/transcript (전사문이 없으면 404) */
export interface Transcript {
  fullText: string;
  segments: TranscriptSegment[] | null;
  sttProvider: string;
}

export function getMeeting(id: number, signal?: AbortSignal): Promise<MeetingDetail> {
  return request<MeetingDetail>(`/meetings/${encodeURIComponent(String(id))}`, { signal });
}

export function getTranscript(id: number, signal?: AbortSignal): Promise<Transcript> {
  return request<Transcript>(`/meetings/${encodeURIComponent(String(id))}/transcript`, { signal });
}
