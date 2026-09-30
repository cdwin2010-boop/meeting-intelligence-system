import type { SpeakerNames } from "./speaker-names";

export type { SpeakerNames };

export type ActionItemStatus = "open" | "in_progress" | "done" | "overdue";

/** 발화 근거: LLM이 액션아이템을 추출한 원문 발언 */
export interface Quote {
  speaker: string;
  /** 회의 녹음 내 위치 (HH:MM:SS) */
  timestamp: string;
  text: string;
}

export interface ActionItem {
  id: string;
  task: string;
  assignee: string;
  /** ISO 날짜 (YYYY-MM-DD) */
  dueDate: string;
  status: ActionItemStatus;
  quote: Quote;
}

export interface Attendee {
  id: string;
  name: string;
  role: string;
}

export interface Meeting {
  id: string;
  title: string;
  /** ISO 8601 일시 */
  startedAt: string;
  attendees: Attendee[];
  /** 전사 원문. 전사 전(queued/processing)과 시드 회의는 null */
  transcriptText: string | null;
  /** v1.9.9: "화자N" → 실제 이름/직함. 매핑이 없으면 {}. 원본(assignee·전사 원문)은 바뀌지 않고 화면이 적용한다 */
  speakerNames: SpeakerNames;
}

/**
 * 회의 목록(GET /meetings)의 한 줄 — docs/API-CONTRACT.md "회의 목록"
 * 서버가 startedAt 내림차순(같으면 id 내림차순)으로 준다. 정렬 옵션·페이지 나눔은 없다.
 */
export interface MeetingSummary {
  id: string;
  title: string;
  /** ISO 8601 일시 */
  startedAt: string;
  /** 가장 최근 작업의 상태. 업로드 작업이 없는 시드 회의는 null */
  jobStatus: JobStatus | null;
}

export type SortKey = "id" | "task" | "assignee" | "dueDate" | "status";
export type SortDirection = "asc" | "desc";

/** null = 정렬 없음(원래 순서). 3-State: none → asc → desc → none */
export type SortState = { key: SortKey; direction: SortDirection } | null;

/* ------------------------------------------------------------------ *
 * 관리자 콘솔(Admin Console) 타입
 * ------------------------------------------------------------------ */

/** 작업 상태: queued(대기) → processing(처리 중) → completed(완료) / failed(실패) */
export type JobStatus = "queued" | "processing" | "completed" | "failed";

/** 작업을 실행한 Worker 정보 (실패 원인 추적용) */
export interface JobWorker {
  id: string;
  host: string;
  engine: "gemini-api" | "faster-whisper";
  stage: "STT" | "LLM";
  /** 클라우드 API 엔진이면 null */
  gpu: string | null;
}

export interface AdminJob {
  id: string;
  meetingTitle: string;
  /** 오디오 길이(초) */
  audioSeconds: number;
  /** 소요 시간(초). 아직 시작 전(queued)이면 null */
  elapsedSeconds: number | null;
  status: JobStatus;
  /** 시도 횟수 (재시도할 때마다 +1) */
  attempt: number;
  /** ISO 8601. 시작 전이면 null */
  startedAt: string | null;
  worker: JobWorker | null;
  /** 실패 작업의 에러 로그(한 줄 = 한 항목). 실패가 아니면 빈 배열 */
  errorLog: string[];
}

export type JobSortKey = "id" | "meetingTitle" | "audioSeconds" | "elapsedSeconds" | "status";
export type JobSortState = { key: JobSortKey; direction: SortDirection } | null;

/* ------------------------------------------------------------------ *
 * 음성 등록(업로드) 타입 — docs/API-CONTRACT.md "음성 등록 API"
 * ------------------------------------------------------------------ */

export type SttEngine = "gemini-api" | "faster-whisper";

/** POST /meetings 에 보낼 값 (FormData로 변환해 전송) */
export interface UploadInput {
  file: File;
  title: string;
  /** ISO 8601 회의 일시. 마감일 계산의 기준 */
  startedAt: string;
  engine: SttEngine;
  /** true면 GPU 가드 경고를 알고도 로컬로 진행 */
  forceLocal?: boolean;
}

/** 202 접수증: 전사를 기다리지 않고 바로 돌려받는다 */
export interface UploadReceipt {
  meetingId: string;
  jobId: string;
  status: "queued";
}

/** GET /jobs/{jobId} 응답 (계약서의 JobStatus. 상태 문자열 타입 JobStatus와 이름이 겹쳐 UploadedJob으로 부름) */
export interface UploadedJob {
  id: string;
  meetingId: string;
  status: JobStatus;
  stage: "STT" | "LLM" | null;
  /** 실패가 아니면 "" */
  errorMessage: string;
}
