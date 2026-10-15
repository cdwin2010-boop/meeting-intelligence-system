/*
 * 원격(시험용) 엔진 연결 점검·L-3 벤치마크 측정 API(작업 L-3a, 시험 전용). 서버가 백그라운드로 실행하고 화면은 상태를 조회한다.
 * 시험 모드가 꺼져 있거나 권한이 없으면 서버가 403 으로 거부한다. 응답·결과 파일에는 주소·키·전사 원문이 없다.
 */
import { request } from "./http";

export type BenchMode = "quick" | "full";
export type BenchState = "idle" | "running" | "completed" | "interrupted" | "stopped" | "failed";

export interface BenchStatus {
  status: BenchState;
  mode: "" | BenchMode;
  runId: string;
  currentLabel: string;
  doneUnits: number;
  totalUnits: number;
  elapsedSeconds: number;
  message: string;
  canResume: boolean;
  canDownload: boolean;
  retries: number;
}

const BASE = "/admin/engine-test/bench";

export const BENCH_RESULT_FILE_NAME = "l3-bench-result.json";

export function getBenchStatus(signal?: AbortSignal): Promise<BenchStatus> {
  return request<BenchStatus>(`${BASE}/status`, { signal });
}

export function startBench(mode: BenchMode, signal?: AbortSignal): Promise<BenchStatus> {
  return request<BenchStatus>(`${BASE}/start`, { method: "POST", body: { mode }, signal });
}

/** 이어하기: 완료 항목은 건너뛰고 남은 항목만 실행한다. 주소·키·모델은 저장된 현재 값을 쓴다 */
export function resumeBench(signal?: AbortSignal): Promise<BenchStatus> {
  return request<BenchStatus>(`${BASE}/resume`, { method: "POST", signal });
}

export function stopBench(signal?: AbortSignal): Promise<BenchStatus> {
  return request<BenchStatus>(`${BASE}/stop`, { method: "POST", signal });
}

/** 결과 JSON(요약 Markdown 포함): 인증 헤더가 필요해 fetch 로 받는다 */
export function downloadBenchResult(signal?: AbortSignal): Promise<Blob> {
  return request<Blob>(`${BASE}/result`, { asBlob: true, signal });
}

export const BENCH_STATE_LABEL: Record<BenchState, string> = {
  idle: "대기 중",
  running: "측정 중",
  completed: "측정 완료",
  interrupted: "중단됨(연결 끊김)",
  stopped: "중단됨",
  failed: "측정 실패",
};

export function formatElapsed(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}분 ${String(s).padStart(2, "0")}초`;
}
