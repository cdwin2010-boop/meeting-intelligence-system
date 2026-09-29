/*
 * API 공통 에러 도구 (목 API와 실제 HTTP API가 함께 사용합니다)
 */

/** fetch가 취소될 때 브라우저가 던지는 것과 같은 형태의 에러 */
export const createAbortError = () => new DOMException("The operation was aborted.", "AbortError");

export const isAbortError = (error: unknown): boolean =>
  error instanceof DOMException && error.name === "AbortError";

/** 서버가 "지금 상태에서는 그 동작을 할 수 없다"고 응답하는 경우(HTTP 404/409에 해당) */
export class ApiError extends Error {
  constructor(
    public readonly code: "not_found" | "invalid_state",
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
