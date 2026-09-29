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
