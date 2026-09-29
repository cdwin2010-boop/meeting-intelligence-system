/*
 * 목(Mock) API — 백엔드 없이 화면을 개발/시연할 때 사용합니다.
 * 실제 서버 버전은 api-http.ts, 둘 중 무엇을 쓸지는 api.ts가 환경변수로 고릅니다.
 * 두 파일의 함수 이름/인자/반환 타입은 항상 같아야 합니다.
 */
import { ApiError, GpuGuardError, createAbortError } from "./api-errors";
import { MOCK_ACTION_ITEMS, MOCK_MEETING } from "./mock-data";
import { MOCK_JOBS } from "./mock-admin";
import type {
  ActionItem,
  ActionItemStatus,
  AdminJob,
  JobSortState,
  JobStatus,
  Meeting,
  SortState,
  UploadedJob,
  UploadInput,
  UploadReceipt,
} from "./types";

// 삭제가 반영되도록 모듈 안에 "가짜 DB"를 복사해 둡니다.
let store: ActionItem[] = MOCK_ACTION_ITEMS.map((item) => ({ ...item }));

// 네트워크 지연을 흉내 내되, signal이 abort되면 즉시 reject 합니다.
function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(createAbortError());
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(createAbortError());
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

// 상태 정렬 우선순위: 급한 것(overdue)부터
const STATUS_RANK: Record<ActionItemStatus, number> = {
  overdue: 0,
  in_progress: 1,
  open: 2,
  done: 3,
};

function sortItems(items: ActionItem[], sort: SortState): ActionItem[] {
  if (!sort) return [...items]; // 정렬 없음 = 원래 순서
  const factor = sort.direction === "asc" ? 1 : -1;

  return [...items].sort((a, b) => {
    switch (sort.key) {
      case "status":
        return (STATUS_RANK[a.status] - STATUS_RANK[b.status]) * factor;
      case "id":
      case "dueDate": // ISO 날짜/ID 문자열은 사전순 비교가 곧 시간/번호순
        return a[sort.key].localeCompare(b[sort.key]) * factor;
      default:
        return a[sort.key].localeCompare(b[sort.key], "ko") * factor;
    }
  });
}

export async function fetchMeeting(meetingId: string, signal?: AbortSignal): Promise<Meeting> {
  await delay(0, signal);
  const upload = findUploadByMeeting(meetingId);
  if (upload) return uploadedMeeting(upload);
  if (meetingId !== MOCK_MEETING.id) throw new ApiError("not_found", `Meeting ${meetingId} not found.`);
  return MOCK_MEETING;
}

export async function fetchActionItems(
  meetingId: string,
  sort: SortState,
  signal?: AbortSignal,
): Promise<ActionItem[]> {
  await delay(400, signal);
  const upload = findUploadByMeeting(meetingId);
  if (upload) return sortItems(uploadedActionItems(upload), sort);
  return sortItems(store, sort); // 그 외 회의 ID는 기존대로 시드 목록
}

export async function deleteActionItem(id: string, signal?: AbortSignal): Promise<void> {
  await delay(600, signal);
  store = store.filter((item) => item.id !== id);
  deletedUploadItemIds.add(id); // 업로드로 만든 액션아이템도 삭제가 반영되게
}

/* ------------------------------------------------------------------ *
 * 관리자 콘솔 목 API: 큐 조회 / 재시도 / 강제 종료
 * 실제 연동 시 각각 GET /admin/jobs, POST /admin/jobs/{id}/retry, POST /admin/jobs/{id}/kill 로 교체
 * ------------------------------------------------------------------ */

const cloneJob = (job: AdminJob): AdminJob => ({
  ...job,
  worker: job.worker ? { ...job.worker } : null,
  errorLog: [...job.errorLog],
});

let jobStore: AdminJob[] = MOCK_JOBS.map(cloneJob);

// 정렬 우선순위: 문제가 있는 것(failed)부터 위로
const JOB_STATUS_RANK: Record<JobStatus, number> = {
  failed: 0,
  processing: 1,
  queued: 2,
  completed: 3,
};

function sortJobs(jobs: AdminJob[], sort: JobSortState): AdminJob[] {
  if (!sort) return [...jobs]; // 정렬 없음 = 원래 순서
  const factor = sort.direction === "asc" ? 1 : -1;

  return [...jobs].sort((a, b) => {
    switch (sort.key) {
      case "status":
        return (JOB_STATUS_RANK[a.status] - JOB_STATUS_RANK[b.status]) * factor;
      case "audioSeconds":
        return (a.audioSeconds - b.audioSeconds) * factor;
      case "elapsedSeconds":
        // 시작 전(null)은 가장 작은 값(-1)으로 취급
        return ((a.elapsedSeconds ?? -1) - (b.elapsedSeconds ?? -1)) * factor;
      case "meetingTitle":
        return a.meetingTitle.localeCompare(b.meetingTitle, "ko") * factor;
      default:
        return a.id.localeCompare(b.id) * factor;
    }
  });
}

export async function fetchAdminJobs(sort: JobSortState, signal?: AbortSignal): Promise<AdminJob[]> {
  await delay(400, signal);
  return sortJobs(jobStore, sort).map(cloneJob);
}

/** 실패한 작업을 대기열로 되돌립니다. (failed → queued, 시도 횟수 +1) */
export async function retryJob(id: string, signal?: AbortSignal): Promise<AdminJob> {
  await delay(500, signal);
  const job = jobStore.find((j) => j.id === id);
  if (!job) throw new ApiError("not_found", `Job ${id} not found.`);
  if (job.status !== "failed") throw new ApiError("invalid_state", `Job ${id} is not failed.`);

  const updated: AdminJob = {
    ...job,
    status: "queued",
    attempt: job.attempt + 1,
    elapsedSeconds: null,
    startedAt: null,
    worker: null,
    errorLog: [],
  };
  jobStore = jobStore.map((j) => (j.id === id ? updated : j));
  return cloneJob(updated);
}

/** 처리 중인 작업을 강제 종료합니다. (processing → failed, 부분 결과 폐기) */
export async function killJob(id: string, signal?: AbortSignal): Promise<AdminJob> {
  await delay(600, signal);
  const job = jobStore.find((j) => j.id === id);
  if (!job) throw new ApiError("not_found", `Job ${id} not found.`);
  // 서버에서 한 번 더 확인: 화면을 보는 사이 작업이 이미 끝났을 수 있습니다(경쟁 상태).
  if (job.status !== "processing") throw new ApiError("invalid_state", `Job ${id} is not processing.`);

  const now = new Date().toISOString();
  const updated: AdminJob = {
    ...job,
    status: "failed",
    errorLog: [
      `${now} [WARN]  kill requested by administrator`,
      `${now} [ERROR] worker ${job.worker?.id ?? "unknown"} received SIGTERM; partial output discarded`,
    ],
  };
  jobStore = jobStore.map((j) => (j.id === id ? updated : j));
  return cloneJob(updated);
}

/* ------------------------------------------------------------------ *
 * 음성 등록 목 API — 계약(docs/API-CONTRACT.md "음성 등록 API")과 같은 검사·흐름
 * 작업 상태는 타이머로 바꾸지 않고 "접수 후 경과 시간"으로 매번 계산한다.
 * 그래서 몇 번을 폴링해도, 탭을 숨겼다 돌아와도 항상 같은 규칙으로 답한다.
 * ------------------------------------------------------------------ */

const ALLOWED_EXTENSIONS = [".mp3", ".m4a", ".wav"];
const MAX_UPLOAD_BYTES = 500 * 1024 * 1024; // 계약 기본 MAX_UPLOAD_MB=500
const BYTES_PER_AUDIO_SECOND = 16_000; // 스텁과 같은 어림: 128kbps ≈ 16,000 바이트/초
const GPU_GUARD_MINUTES = 20;

interface MockUpload {
  meetingId: string;
  jobId: string;
  title: string;
  startedAt: string;
  /** 접수 시각 (Date.now()) */
  acceptedAt: number;
}

let uploads: MockUpload[] = [];
let uploadSeq = 0;
const deletedUploadItemIds = new Set<string>();

function findUploadByMeeting(meetingId: string): MockUpload | undefined {
  return uploads.find((u) => u.meetingId === meetingId);
}

/** 경과 시간 → 상태: 0~2초 queued, ~5초 processing/STT, ~8초 processing/LLM, 이후 completed */
function uploadedJob(upload: MockUpload): UploadedJob {
  const elapsed = Date.now() - upload.acceptedAt;
  const base = { id: upload.jobId, meetingId: upload.meetingId, errorMessage: "" };
  if (elapsed < 2_000) return { ...base, status: "queued", stage: null };
  if (elapsed < 5_000) return { ...base, status: "processing", stage: "STT" };
  if (elapsed < 8_000) return { ...base, status: "processing", stage: "LLM" };
  return { ...base, status: "completed", stage: null };
}

const isCompleted = (upload: MockUpload) => uploadedJob(upload).status === "completed";

// 테스트 가이드 A파일 대본 (스텁 uploads.py와 같은 내용)
const SCRIPT = [
  { timestamp: "00:00:03", speaker: "김도현", text: "화자 분리가 자꾸 틀려요. 이서연 님, 개선안을 다음 주 금요일까지 정리해 주세요." },
  { timestamp: "00:00:12", speaker: "이서연", text: "네, 제가 다음 주 금요일까지 정리해서 공유하겠습니다." },
  { timestamp: "00:00:20", speaker: "김도현", text: "디자인 시안은 정민수 님이 이번 주 목요일까지 확정해 주세요." },
  { timestamp: "00:00:28", speaker: "정민수", text: "알겠습니다. 목요일까지 확정하겠습니다." },
  { timestamp: "00:00:35", speaker: "김도현", text: "그리고 점심 메뉴는 다음에 정합시다." },
];

function uploadedMeeting(upload: MockUpload): Meeting {
  return {
    id: upload.meetingId,
    title: upload.title,
    startedAt: upload.startedAt,
    attendees: [],
    // 전사 원문은 완료 후에만 채워진다
    transcriptText: isCompleted(upload)
      ? SCRIPT.map((line) => `[${line.timestamp}] ${line.speaker}: ${line.text}`).join("\n")
      : null,
  };
}

/** 회의 일시의 날짜 부분(회의 현지 날짜)을 기준으로 이번 주 목요일·다음 주 금요일 계산 (스텁과 같은 식) */
function dueDates(startedAt: string): { thisThursday: string; nextFriday: string } {
  const [y, m, d] = startedAt.slice(0, 10).split("-").map(Number);
  const base = Date.UTC(y, m - 1, d);
  const weekday = (new Date(base).getUTCDay() + 6) % 7; // 월=0 … 일=6 (파이썬 weekday와 같게)
  const addDays = (n: number) => new Date(base + n * 86_400_000).toISOString().slice(0, 10);
  return { thisThursday: addDays((3 - weekday + 7) % 7), nextFriday: addDays(7 - weekday + 4) };
}

function uploadedActionItems(upload: MockUpload): ActionItem[] {
  if (!isCompleted(upload)) return []; // 완료 전에는 결과 없음
  const { thisThursday, nextFriday } = dueDates(upload.startedAt);
  const suffix = upload.jobId.replace(/^job-/, "");
  const items: ActionItem[] = [
    {
      id: `AI-${suffix}-1`,
      task: "STT 화자 분리 정확도 개선안 정리",
      assignee: "이서연",
      dueDate: nextFriday,
      status: "open",
      quote: { ...SCRIPT[0] },
    },
    {
      id: `AI-${suffix}-2`,
      task: "디자인 시안 확정",
      assignee: "정민수",
      dueDate: thisThursday,
      status: "open",
      quote: { ...SCRIPT[2] },
    },
  ];
  return items.filter((item) => !deletedUploadItemIds.has(item.id));
}

/** 음성 파일 접수: 계약과 같은 검사를 하고, 통과하면 접수증을 바로 돌려준다. (실패 시 아무것도 저장하지 않음) */
export async function uploadMeetingAudio(input: UploadInput, signal?: AbortSignal): Promise<UploadReceipt> {
  await delay(300, signal);

  const title = input.title.trim();
  const startedAtOk = /^\d{4}-\d{2}-\d{2}T/.test(input.startedAt) && !Number.isNaN(Date.parse(input.startedAt));
  const engineOk = input.engine === "gemini-api" || input.engine === "faster-whisper";
  if (!title || title.length > 200 || !startedAtOk || !engineOk || input.file.size === 0) {
    throw new ApiError("invalid_input", "Some fields are missing or invalid.");
  }
  const extension = /\.[A-Za-z0-9]+$/.exec(input.file.name)?.[0].toLowerCase() ?? "";
  if (!ALLOWED_EXTENSIONS.includes(extension)) throw new ApiError("unsupported_type", "Unsupported file type.");
  if (input.file.size > MAX_UPLOAD_BYTES) throw new ApiError("too_large", "The file is too large.");

  // GPU 가드: 로컬 엔진이고 예상 처리 시간(분)이 기준 이상이면 작업을 만들지 않고 경고
  if (input.engine === "faster-whisper" && !input.forceLocal) {
    const audioSeconds = Math.max(1, Math.floor(input.file.size / BYTES_PER_AUDIO_SECOND));
    const expectedMinutes = Math.ceil(audioSeconds / 60);
    if (expectedMinutes >= GPU_GUARD_MINUTES) throw new GpuGuardError(expectedMinutes);
  }

  uploadSeq += 1;
  const no = String(uploadSeq).padStart(3, "0");
  const upload: MockUpload = {
    meetingId: `mtg-mock-${no}`,
    jobId: `job-mock-${no}`,
    title,
    startedAt: input.startedAt,
    acceptedAt: Date.now(),
  };
  uploads = [...uploads, upload];
  return { meetingId: upload.meetingId, jobId: upload.jobId, status: "queued" };
}

/** 내 작업 하나의 진행 상태 조회 (폴링용). 없으면 not_found */
export async function fetchJob(jobId: string, signal?: AbortSignal): Promise<UploadedJob> {
  await delay(200, signal);
  const upload = uploads.find((u) => u.jobId === jobId);
  if (!upload) throw new ApiError("not_found", `Job ${jobId} not found.`);
  return uploadedJob(upload);
}
