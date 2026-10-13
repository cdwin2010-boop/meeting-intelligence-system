/*
 * 변환 대기열 표시와 즉시 변환 요청(작업 88-2, 백엔드 작업 88-1). 응답 모양은 docs/contracts/process-now-v1.json 이 기준이다.
 * - 즉시 변환(POST /api/meetings/{id}/process-now): 관리자·지시자만(서버 판정). 화면은 허용 동작 process_now 가 있을 때만 버튼을 보인다.
 * - 대기·처리 중 표시값(queueAhead·prioritized·waitingForPrior·workerAlive)은 처리 작업 응답(상세 processing, /api/me/processing)에 덧붙어 온다.
 */
import { request } from "./http";

export interface QueueFields {
  /** 앞서 시작할 대기 작업 수(queue 모드의 대기·처리 중 작업이 아니면 없음) */
  queueAhead?: number | null;
  /** 즉시 변환으로 우선 처리에 올려짐 */
  prioritized?: boolean;
  /** 선행 작업 때문에 바로 시작하지 못함 */
  waitingForPrior?: boolean;
  /** 워커 생존 신호가 최근임. false 면 처리기가 멈춘 것 */
  workerAlive?: boolean | null;
}

export interface ProcessNowResult {
  meetingId: number;
  jobId: number | null;
  status: "queued";
  queueAhead: number;
  prioritized: boolean;
  alreadyPrioritized: boolean;
  waitingForPrior: boolean;
  message: string;
  workerAlive: boolean;
}

/** 대기 상태에서만 보이는 처리기 응답 없음 안내(문구 고정) */
export const WORKER_DOWN_NOTICE = "처리기 응답이 없어 대기 중입니다. 관리자에게 문의해 주세요.";
/** 선행 작업이 없어 바로 시작할 때 즉시 변환 뒤 보이는 안내(waitingForPrior=false) */
export const PROCESS_NOW_STARTING = "변환을 바로 시작합니다.";

export function processNowMeeting(meetingId: number, signal?: AbortSignal): Promise<ProcessNowResult> {
  return request<ProcessNowResult>(`/meetings/${encodeURIComponent(String(meetingId))}/process-now`, { method: "POST", signal });
}

/** 즉시 변환 응답 → 화면 문구. 선행 작업이 있으면 서버가 준 message 를 그대로 쓴다 */
export function processNowMessage(result: Pick<ProcessNowResult, "waitingForPrior" | "message">): string {
  return result.waitingForPrior ? result.message : PROCESS_NOW_STARTING;
}
