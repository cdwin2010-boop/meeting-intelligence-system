/*
 * v2 API 요청 래퍼 (backend/, 기본 http://127.0.0.1:8001/api)
 *
 * - 저장된 토큰이 있으면 Authorization: Bearer 를 자동으로 붙인다.
 * - 요청 기한(기본 30초)이 지나면 끊고 NetworkError 로 알린다. 화면이 준 signal 로 취소한 AbortError 는 그대로 던진다.
 * - 서버에 닿지 못하면 NetworkError, 오류 상태 코드는 ApiError(status, message, missingFields).
 * - 401 이면 토큰을 지우고 "인증 만료" 이벤트를 보낸다(AuthProvider 가 받아 로그인 화면으로 보냄).
 *   로그인 요청 자체의 401(아이디·비밀번호 틀림)은 이벤트 없이 호출자에게 ApiError 로 넘긴다.
 */
import { ApiError, NetworkError, parseErrorBody } from "./errors";
import { clearToken, getToken } from "./token";

export const API_V2_BASE_URL = (process.env.NEXT_PUBLIC_API_V2_BASE_URL ?? "http://127.0.0.1:8001/api").replace(/\/+$/, "");
export const DEFAULT_TIMEOUT_MS = 30_000;
export const AUTH_EXPIRED_EVENT = "mi:v2:auth-expired";

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT";
  /** JSON 으로 보낼 본문(FormData 는 그대로 보낸다) */
  body?: unknown;
  signal?: AbortSignal;
  timeoutMs?: number;
  /** true 면 401 이어도 토큰 삭제·만료 이벤트를 하지 않는다(로그인 요청용) */
  skipAuthExpiry?: boolean;
}

function notifyAuthExpired(): void {
  clearToken();
  if (typeof window !== "undefined") window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal: outer, timeoutMs = DEFAULT_TIMEOUT_MS, skipAuthExpiry = false } = options;

  // 우리 쪽 취소 장치: 화면의 signal 이 취소되면 함께 취소, 기한이 지나면 스스로 취소
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const onOuterAbort = () => controller.abort();
  if (outer?.aborted) controller.abort();
  else outer?.addEventListener("abort", onOuterAbort, { once: true });

  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body; // Content-Type 은 브라우저가 multipart 구분선과 함께 정한다
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(`${API_V2_BASE_URL}${path}`, {
      method,
      headers,
      body: payload,
      cache: "no-store",
      signal: controller.signal,
    });
  } catch (error) {
    if (outer?.aborted) throw error; // 화면이 취소함 → AbortError 그대로
    if (timedOut) throw new NetworkError("서버 응답이 늦습니다. 잠시 후 다시 시도하세요");
    throw new NetworkError();
  } finally {
    clearTimeout(timer);
    outer?.removeEventListener("abort", onOuterAbort);
  }

  if (!response.ok) {
    const { message, missingFields } = parseErrorBody(response.status, await readJson(response));
    if (response.status === 401 && !skipAuthExpiry) notifyAuthExpired();
    throw new ApiError(response.status, message, missingFields);
  }
  if (response.status === 204) return undefined as T;
  return (await readJson(response)) as T;
}
