/*
 * API 공통 에러 도구 (목 API와 실제 HTTP API가 함께 사용합니다)
 */

/** fetch가 취소될 때 브라우저가 던지는 것과 같은 형태의 에러 */
export const createAbortError = () => new DOMException("The operation was aborted.", "AbortError");

export const isAbortError = (error: unknown): boolean =>
  error instanceof DOMException && error.name === "AbortError";

/**
 * 상태 코드로 구분되는 API 오류
 *  404 not_found / 409 invalid_state
 *  (업로드 전용) 400 invalid_input / 413 too_large / 415 unsupported_type
 */
export type ApiErrorCode = "not_found" | "invalid_state" | "invalid_input" | "too_large" | "unsupported_type";

export class ApiError extends Error {
  constructor(
    public readonly code: ApiErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * 업로드 GPU 가드 경고 (HTTP 409 + 본문 detail.code === "gpu_guard")
 * 로컬 Faster-Whisper 예상 처리 시간이 기준 이상이라 작업을 만들지 않은 경우.
 * 화면은 "Gemini로 전환 / 그래도 로컬로(forceLocal) / 취소"를 고르게 한다.
 */
export class GpuGuardError extends Error {
  constructor(public readonly expectedMinutes: number) {
    super(`Local transcription is expected to take about ${expectedMinutes} minutes.`);
    this.name = "GpuGuardError";
  }
}
