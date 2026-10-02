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
  /** 줄머리 화자 표기를 표시 이름으로 바꾼 원문(미등록은 "이름(미등록)"). fullText 는 원본 그대로 */
  displayText?: string;
  speakers?: SpeakerMapping[];
}

/** 화자 매핑 1건: 계정이면 accountId·accountName, 미등록이면 name(글자)만. displayName 은 화면 표시용 */
export interface SpeakerMapping {
  label: string;
  accountId: number | null;
  accountName: string | null;
  name: string | null;
  displayName: string;
  unregistered: boolean;
}

/** GET·PUT /api/meetings/{id}/speakers 응답 */
export interface SpeakersResponse {
  /** 전사문에 나오는 화자 표기(처음 나온 순서) */
  labels: string[];
  speakers: SpeakerMapping[];
  /** 이번 저장으로 담당자가 자동으로 채워진 업무 id(조회에서는 빈 목록) */
  autoAssignedItemIds: number[];
}

/** PUT 본문 항목: 화자마다 accountId 또는 name 중 하나만 */
export type SpeakerInput = { label: string; accountId: number } | { label: string; name: string };

export function getMeeting(id: number, signal?: AbortSignal): Promise<MeetingDetail> {
  return request<MeetingDetail>(`/meetings/${encodeURIComponent(String(id))}`, { signal });
}

export function getTranscript(id: number, signal?: AbortSignal): Promise<Transcript> {
  return request<Transcript>(`/meetings/${encodeURIComponent(String(id))}/transcript`, { signal });
}

/** 화자 매핑 조회(등록자·관리자 이상만, 아니면 403 — 판정은 서버) */
export function getSpeakers(id: number, signal?: AbortSignal): Promise<SpeakersResponse> {
  return request<SpeakersResponse>(`/meetings/${encodeURIComponent(String(id))}/speakers`, { signal });
}

/** 화자 매핑 저장(전체 교체). 400(잘못된 표기·계정)·403·409(상위 직급 매핑)는 서버 문구로 ApiError */
export function saveSpeakers(id: number, speakers: SpeakerInput[], signal?: AbortSignal): Promise<SpeakersResponse> {
  return request<SpeakersResponse>(`/meetings/${encodeURIComponent(String(id))}/speakers`, {
    method: "PUT",
    body: { speakers },
    signal,
  });
}

/** POST /api/meetings/{id}/confirm 응답(회의록만 확정. 업무는 따로 확정한다) */
export interface MeetingConfirmResult {
  id: number;
  status: MeetingStatus;
  confirmKind: ConfirmKind | null;
  confirmedBy: AccountRef | null;
  confirmedAt: string | null;
  confirmedItemIds: number[];
  skippedItemIds: number[];
}

/** 회의록 확정(총괄 관리자·지시자만 — 판정은 서버, 아니면 403. 확정할 수 없는 상태는 409). 이미 확정이면 그대로 200 */
export function confirmMeeting(id: number, signal?: AbortSignal): Promise<MeetingConfirmResult> {
  return request<MeetingConfirmResult>(`/meetings/${encodeURIComponent(String(id))}/confirm`, { method: "POST", signal });
}

/** 수정 요청 1건(사건 원장 기록. 상태·자동 확정 시계에 영향 없음). itemId 가 없으면 회의록 전체 대상 */
export interface ChangeRequest {
  requestId: number;
  requester: AccountRef | null;
  comment: string;
  itemId: number | null;
  createdAt: string;
  resolution: { decision: "accepted" | "rejected"; reason: string | null; resolvedBy: AccountRef | null; resolvedAt: string } | null;
}

/** 수정 요청 코멘트 최대 글자 수(서버 검사와 같음) */
export const CHANGE_REQUEST_MAX = 2000;

/** GET /api/meetings/{id}/change-requests (작성 순) */
export function listChangeRequests(id: number, signal?: AbortSignal): Promise<ChangeRequest[]> {
  return request<ChangeRequest[]>(`/meetings/${encodeURIComponent(String(id))}/change-requests`, { signal });
}

/** POST /api/meetings/{id}/change-requests (201 {requestId}). 회의록을 볼 수 있으면 누구나. 다른 회의록 업무는 400 */
export function createChangeRequest(
  id: number,
  input: { comment: string; itemId: number | null },
  signal?: AbortSignal,
): Promise<{ requestId: number }> {
  return request<{ requestId: number }>(`/meetings/${encodeURIComponent(String(id))}/change-requests`, {
    method: "POST",
    body: input,
    signal,
  });
}

/** 수정 요청 결정: 수락 또는 반려 */
export type ChangeRequestDecision = "accepted" | "rejected";

/** 해결 답변(reason) 최대 글자 수(서버 검사와 같음) */
export const CHANGE_REQUEST_REASON_MAX = 2000;

/**
 * POST /api/meetings/{id}/change-requests/{requestId}/resolve {decision, reason?} → 해결 정보가 담긴 수정 요청.
 * 관리자 이상만(아니면 403), 더 높은 직급이 이미 결정했으면 409, 없는 요청 404. 권한 판정은 서버
 */
export function resolveChangeRequest(
  id: number,
  requestId: number,
  input: { decision: ChangeRequestDecision; reason: string | null },
  signal?: AbortSignal,
): Promise<ChangeRequest> {
  return request<ChangeRequest>(
    `/meetings/${encodeURIComponent(String(id))}/change-requests/${encodeURIComponent(String(requestId))}/resolve`,
    { method: "POST", body: input, signal },
  );
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
