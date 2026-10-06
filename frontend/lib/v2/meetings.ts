/*
 * v2 회의록 조회 API(읽기 전용: 목록·상세·전사문). 응답 모양은 backend/app/api/meeting_schemas.py(camelCase)와 맞춘다.
 * 열람 권한은 서버가 판정한다(관리자 이상은 고객사 전체, 담당자는 참석·담당·등록한 회의록만).
 */
import { request } from "./http";
import type { MissingField } from "./errors";
import type { ConfirmKind } from "./todos";

export type MeetingStatus = "processing" | "awaiting_confirmation" | "confirmed" | "failed" | "no_content";

/** 회의록 단계(서버 phase): 진행중 / 종료 / 보류 / 삭제. status(처리·확정 상태)와 별개 */
export type MeetingPhase = "active" | "ended" | "on_hold" | "deleted";

/** 종료 구분: 자동 종료(업무가 모두 종결) / 관리자 직권 종료 */
export type EndKind = "auto" | "manager";

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
  /** 회의록 단계(추가 필드). 없는 서버 응답이면 진행중으로 본다 */
  phase?: MeetingPhase;
}

/** GET /api/meetings (회의 일시 최신순, page 는 1부터) */
export interface MeetingListPage {
  items: MeetingListItem[];
  total: number;
  page: number;
  size: number;
}

export const MEETINGS_PAGE_SIZE = 20;

/** GET /api/meetings?phase=…(기본 active). 담당자가 on_hold·deleted 를 요청하면 서버가 403 */
export function listMeetings(page: number, signal?: AbortSignal, phase: MeetingPhase = "active"): Promise<MeetingListPage> {
  const query = new URLSearchParams({ page: String(page), size: String(MEETINGS_PAGE_SIZE), phase });
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
  /** 업무 출처(추가 필드): ai(AI 추출) | manual(수기·업로드 직권 등록). 없으면 ai */
  origin?: "ai" | "manual";
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
  /** 계정이 없는 참석자 이름(추가 필드, 화면에는 "이름(미등록)"). 없는 서버 응답이면 빈 목록 */
  guestParticipants?: string[];
  /** 회의록 5개 항목(추가 필드). 없는 서버 응답이면 아직 생성되지 않은 것으로 본다 */
  minutes?: Minutes;
  actionItems: ActionItem[];
  /** reason: 처리 사유가 있는 사건(업무 종결·삭제, 회의록 보류·직권 종료·삭제)만, 그 밖에는 null */
  recentEvents: { eventType: string; actor: AccountRef | null; createdAt: string; reason?: string | null }[];
  // 아래는 보류·종료·삭제(추가 필드). 이 필드가 없는 서버 응답이면 진행중·기록 없음으로 본다
  phase?: MeetingPhase;
  onHold?: boolean;
  onHoldBy?: AccountRef | null;
  onHoldAt?: string | null;
  onHoldReason?: string | null;
  resumedBy?: AccountRef | null;
  resumedAt?: string | null;
  endKind?: EndKind | null;
  endedBy?: AccountRef | null;
  endedAt?: string | null;
  endReason?: string | null;
  deletedBy?: AccountRef | null;
  deletedAt?: string | null;
  deleteReason?: string | null;
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

/** 처리 사유 최대 글자 수(서버 검사와 같음) */
export const REASON_MAX = 2000;

const meetingPath = (id: number, action: string) => `/meetings/${encodeURIComponent(String(id))}/${action}`;

/**
 * 회의록 보류·재개·직권 종료·삭제. 권한은 서버 판정(지시자·총괄, 아니면 403). 응답은 쓰지 않고 화면이 상세를 다시 받는다.
 * - 보류 POST /hold {reason}: 확정 대기·확정만, 종료·삭제됨 409
 * - 재개 POST /resume (사유 없음): 업무 기한을 모두 비운다
 * - 직권 종료 POST /end {reason}: 확정 전 업무도 함께 종결, 보류·삭제됨 409
 * - 삭제 POST /delete {reason}: 처리 중 409
 * 사유 없음·공백·2000자 초과는 422
 */
export async function holdMeeting(id: number, reason: string, signal?: AbortSignal): Promise<void> {
  await request<unknown>(meetingPath(id, "hold"), { method: "POST", body: { reason }, signal });
}

export async function resumeMeeting(id: number, signal?: AbortSignal): Promise<void> {
  await request<unknown>(meetingPath(id, "resume"), { method: "POST", signal });
}

export async function endMeeting(id: number, reason: string, signal?: AbortSignal): Promise<void> {
  await request<unknown>(meetingPath(id, "end"), { method: "POST", body: { reason }, signal });
}

export async function deleteMeeting(id: number, reason: string, signal?: AbortSignal): Promise<void> {
  await request<unknown>(meetingPath(id, "delete"), { method: "POST", body: { reason }, signal });
}

/** GET /api/meetings/{id}/audio-url: 약 10분 유효한 재생 주소. url 은 API 기본 주소 뒤에 붙이는 경로(서명 토큰 포함). 음성 없음·열람 불가는 404 */
export interface AudioUrl {
  url: string;
  expiresInSec: number;
}

export function getAudioUrl(meetingId: number, signal?: AbortSignal): Promise<AudioUrl> {
  return request<AudioUrl>(`/meetings/${encodeURIComponent(String(meetingId))}/audio-url`, { signal });
}

/** 회의록 5개 항목(MinutesOut). 정보가 없는 항목은 서버가 "내용없음"을 준다 */
export const MINUTES_EMPTY = "내용없음";
export const MINUTES_FIELDS = ["purpose", "discussion", "decisions", "risks", "nextAgenda"] as const;
export type MinutesField = (typeof MINUTES_FIELDS)[number];
export const MINUTES_LABEL: Record<MinutesField, string> = {
  purpose: "목적",
  discussion: "주요 논의사항",
  decisions: "결정사항",
  risks: "리스크",
  nextAgenda: "다음 안건",
};

export interface Minutes {
  purpose: string;
  discussion: string;
  decisions: string;
  risks: string;
  nextAgenda: string;
  /** 만든 처리 엔진. null 이면 5개 항목이 아직 생성되지 않은 회의록 */
  engine: string | null;
  updatedBy: AccountRef | null;
  updatedAt: string | null;
}

/** PATCH /api/meetings/{id}/minutes: 보낸 항목만 바꾸고(비우면 "내용없음"), 바뀐 항목만 이력에 남는다. 지시자·총괄만(서버 판정), 보류·종료·삭제는 409 */
export function updateMinutes(id: number, fields: Partial<Record<MinutesField, string>>, signal?: AbortSignal): Promise<Minutes> {
  return request<Minutes>(`/meetings/${encodeURIComponent(String(id))}/minutes`, { method: "PATCH", body: fields, signal });
}

/** GET /api/meetings/{id}/history 의 한 줄(쪽 인자 없음, 최신순 최대 200건) */
export interface HistoryEntry {
  id: number;
  targetType: string;
  targetId: number;
  /** 화면 구분: 직권 수정 | 업무 갱신 | 직권 등록 */
  kind: string;
  kindCode?: string;
  /** 같은 업로드 묶음 식별자(업로드로 생긴 이력만) */
  batchId?: string | null;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  changedBy: AccountRef | null;
  changedAt: string;
}

export function getHistory(id: number, signal?: AbortSignal): Promise<HistoryEntry[]> {
  return request<HistoryEntry[]>(`/meetings/${encodeURIComponent(String(id))}/history`, { signal });
}

/** GET /api/meetings/{id}/export: 엑셀(.xlsx) 파일. 인증 헤더가 필요해 fetch 로 받는다(파일명은 화면에서 만든다) */
export function downloadMeetingExcel(id: number, signal?: AbortSignal): Promise<Blob> {
  return request<Blob>(`/meetings/${encodeURIComponent(String(id))}/export`, { asBlob: true, signal });
}

/** 업로드 미리보기·적용의 항목 변경 칸(업무 PATCH 사건과 같은 키) */
export interface ItemChange {
  title?: string;
  assigneeId?: number | null;
  dueDate?: string | null;
  dueUndetermined?: boolean;
}

export interface UploadIssue {
  sheet: string;
  row: number | null;
  message: string;
}

export interface UploadAmbiguity {
  /** choices 의 키(예: "row:3", "participant:박동명") */
  key: string;
  name: string;
  sheet: string;
  row: number | null;
  candidates: { id: number; name: string; loginId: string }[];
}

export interface UploadPreview {
  canApply: boolean;
  errors: UploadIssue[];
  warnings: UploadIssue[];
  ambiguities: UploadAmbiguity[];
  items: {
    updates: { itemId: number; row: number; title: string; before: ItemChange; after: ItemChange }[];
    unchanged: number[];
    skipped: { itemId: number; row: number; title: string; status: string; reason: string }[];
    added: { row: number; title: string; assigneeId: number | null; dueDate: string | null; dueUndetermined: boolean; evidence: string }[];
  };
  participants: {
    before: string[];
    after: string[];
    added: string[];
    removed: { accountId: number; name: string; loginId: string; viewImpact: string }[];
  } | null;
  /** 5개 항목 변경. 키는 서버 칸 이름(purpose·discussion·decisions·risks·next_agenda) */
  minutes: Record<string, { before: string; after: string }>;
}

export interface UploadApplyResult {
  batchId: string;
  updatedItemIds: number[];
  addedItemIds: number[];
  skipped: { itemId: number; reason: string }[];
  unchangedItemIds: number[];
  participantsChanged: boolean;
  minutesChanged: string[];
  warnings: UploadIssue[];
}

function uploadForm(file: File, choices: Record<string, number>): FormData {
  const form = new FormData();
  form.append("file", file);
  form.append("choices", JSON.stringify(choices));
  return form;
}

/** POST /api/meetings/{id}/update-upload/preview: 아무것도 저장하지 않고 변경 예정만 돌려준다(오류가 있어도 200, canApply=false) */
export function previewUpload(id: number, file: File, choices: Record<string, number>, signal?: AbortSignal): Promise<UploadPreview> {
  return request<UploadPreview>(`/meetings/${encodeURIComponent(String(id))}/update-upload/preview`, {
    method: "POST",
    body: uploadForm(file, choices),
    signal,
  });
}

/** POST /api/meetings/{id}/update-upload/apply: 같은 파일·선택값으로 한 번에 적용(오류·동명이인 선택 누락은 400 으로 아무것도 적용하지 않음) */
export function applyUpload(id: number, file: File, choices: Record<string, number>, signal?: AbortSignal): Promise<UploadApplyResult> {
  return request<UploadApplyResult>(`/meetings/${encodeURIComponent(String(id))}/update-upload/apply`, {
    method: "POST",
    body: uploadForm(file, choices),
    signal,
  });
}

export interface ManualItemInput {
  title: string;
  assigneeId?: number;
  dueDate?: string;
  dueUndetermined?: boolean;
}

/** POST /api/meetings/{id}/action-items: 수기 업무 등록(근거는 서버가 "등록자 직권 지정"으로 둔다). 지시자·총괄만(서버 판정) */
export function createManualItem(id: number, input: ManualItemInput, signal?: AbortSignal): Promise<ActionItem> {
  return request<ActionItem>(`/meetings/${encodeURIComponent(String(id))}/action-items`, { method: "POST", body: input, signal });
}
