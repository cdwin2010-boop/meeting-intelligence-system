/*
 * 내가 올린 회의록의 처리 현황: GET /api/me/processing (로그인 필요, 읽기 전용).
 * 응답은 회의록마다 가장 최근 작업 1건(최근 PROCESSING_TRACK_HOURS 시간 안, 최신순 최대 10건). 오류 코드는 분류 코드만.
 * elapsedSec 은 서버가 응답 시점 기준으로 계산한 값이다(브라우저 시계에 의존하지 않는다).
 */
import { request } from "./http";

export type ProcessingJobStatus = "queued" | "running" | "completed" | "failed" | "no_content";

export interface ProcessingItem {
  meetingId: number;
  jobId: number;
  title: string;
  status: ProcessingJobStatus;
  errorCode: string | null;
  elapsedSec: number;
  finishedAt: string | null;
  /** 재처리 허용(서버의 reprocess_meeting 판정 그대로) */
  canReprocess: boolean;
}

export const isActiveStatus = (status: string): boolean => status === "queued" || status === "running";

export function listMyProcessing(signal?: AbortSignal): Promise<ProcessingItem[]> {
  return request<ProcessingItem[]>("/me/processing", { signal });
}

/** 초 → mm:ss(60분이 넘으면 분이 그대로 커진다) */
export function formatElapsed(totalSec: number): string {
  const sec = Math.max(0, Math.floor(totalSec));
  return `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
}
