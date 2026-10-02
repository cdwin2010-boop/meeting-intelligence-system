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

/** POST /api/meetings/upload 응답(202 접수, 처리는 서버 백그라운드) */
export interface UploadAccepted {
  meetingId: number;
  jobId: number;
}

// 큰 음성 파일 전송용 기한(일반 요청 30초와 따로). 기본 10분
const UPLOAD_TIMEOUT_MS = Number(process.env.NEXT_PUBLIC_V2_UPLOAD_TIMEOUT_MS) || 600_000;

/**
 * 음성 회의록 올리기. heldAt 은 시간대 오프셋이 붙은 ISO 8601(예: 2026-10-01T14:00:00+09:00)이어야 한다.
 * 허용 형식(415)·용량(413)은 서버 설정이 판정하고, 오류 문구도 서버가 준 것을 그대로 쓴다.
 */
export function uploadMeeting(
  input: { file: File; title: string; heldAt: string; participantIds?: number[] },
  signal?: AbortSignal,
): Promise<UploadAccepted> {
  const form = new FormData();
  form.append("file", input.file);
  form.append("title", input.title);
  form.append("heldAt", input.heldAt);
  // 참석자는 같은 이름(participantIds)으로 여러 번 보낸다(서버는 반복·쉼표 묶음 모두 받음). 없으면 보내지 않는다
  for (const id of input.participantIds ?? []) form.append("participantIds", String(id));
  return request<UploadAccepted>("/meetings/upload", { method: "POST", body: form, signal, timeoutMs: UPLOAD_TIMEOUT_MS });
}
