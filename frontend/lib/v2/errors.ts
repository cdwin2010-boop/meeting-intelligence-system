/*
 * v2 API 오류 (v1 의 lib/api-errors.ts 와 분리)
 *
 * 백엔드(backend/) 오류 본문은 세 가지 모양이 섞여 온다. 모두 사람이 읽을 한 줄 message 로 정리한다.
 *  1) {"detail": "문장"}                                   — 일반 HTTPException
 *  2) {"detail": {"message": "문장", "missingFields": [...]}} — 보완 필요(확정 불가 등)
 *  3) {"detail": [{"loc": [...], "msg": "...", ...}]}       — FastAPI 입력 검증(422)
 * 혹시 {"message", "missingFields"} 가 최상위로 와도 같은 방식으로 읽는다.
 */

export type MissingField = "title" | "assignee" | "dueDate";

/** 서버가 오류 상태 코드로 응답함 */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly missingFields?: MissingField[],
    /** 서버 오류 본문 그대로(오류 목록·동명이인 목록처럼 message 밖의 값을 읽을 때) */
    public readonly body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** 서버에 닿지 못함(연결 실패·시간 초과) */
export class NetworkError extends Error {
  constructor(message = "서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요") {
    super(message);
    this.name = "NetworkError";
  }
}

/** 화면이 취소한 요청인지(이 경우 오류로 표시하지 않는다) */
export const isAbortError = (error: unknown): boolean =>
  error instanceof DOMException && error.name === "AbortError";

const FALLBACK_BY_STATUS: Record<number, string> = {
  400: "요청 내용이 올바르지 않습니다.",
  401: "로그인이 필요합니다.",
  403: "권한이 없습니다.",
  404: "찾을 수 없습니다.",
  409: "지금 상태에서는 처리할 수 없습니다.",
  413: "파일이 너무 큽니다.",
  415: "허용하지 않는 파일 형식입니다.",
  422: "입력값을 확인해 주세요.",
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function readMissingFields(value: unknown): MissingField[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.filter((v): v is MissingField => v === "title" || v === "assignee" || v === "dueDate");
}

/** 오류 본문(JSON 이 아닐 수도 있음) → {message, missingFields} */
export function parseErrorBody(status: number, body: unknown): { message: string; missingFields?: MissingField[] } {
  const fallback = FALLBACK_BY_STATUS[status] ?? `요청을 처리하지 못했습니다(${status}).`;
  if (!isRecord(body)) return { message: fallback };

  // 최상위 {message, missingFields}
  if (typeof body.message === "string" && body.message) {
    return { message: body.message, missingFields: readMissingFields(body.missingFields) };
  }

  const detail = body.detail;
  if (typeof detail === "string" && detail) return { message: detail };
  if (isRecord(detail) && typeof detail.message === "string") {
    return { message: detail.message || fallback, missingFields: readMissingFields(detail.missingFields) };
  }
  if (Array.isArray(detail)) {
    // 422 검증 오류: 첫 항목의 msg 만 짧게
    const first = detail.find(isRecord);
    const msg = first && typeof first.msg === "string" ? first.msg : "";
    return { message: msg ? `${fallback} (${msg})` : fallback };
  }
  return { message: fallback };
}
