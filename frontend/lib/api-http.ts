/*
 * 실제 HTTP API — FastAPI 백엔드와 통신합니다. (규격: docs/API-CONTRACT.md)
 * 함수 이름/인자는 api-mock.ts와 동일합니다. 그래야 화면 코드를 바꾸지 않고 교체됩니다.
 *
 * 규칙 3가지
 *  1) signal(AbortController)을 fetch에 그대로 전달한다.
 *  2) AbortError는 catch해서 바꾸지 않는다 → 화면의 isAbortError가 그대로 판별한다.
 *  3) 오류는 "HTTP 상태 코드"만 믿는다 (404 → not_found, 409 → invalid_state).
 */
import { ApiError } from "./api-errors";
import type { ActionItem, AdminJob, JobSortState, Meeting, SortState } from "./types";

// 끝의 "/"는 제거해서 `${BASE_URL}${path}`가 "//"로 겹치지 않게 합니다.
const BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api").replace(/\/+$/, "");

// 서버가 연결은 받아 놓고 응답을 안 주면 fetch는 영원히 기다립니다(화면이 "Loading…"에서 멈춤).
// 그래서 기한(기본 10초)을 두고, 넘기면 "일반 오류"로 바꿔 화면이 오류 안내를 보여 주게 합니다.
const TIMEOUT_MS = Number(process.env.NEXT_PUBLIC_API_TIMEOUT_MS ?? "10000") || 10_000;

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  // 우리 쪽 취소 리모컨: (1) 화면이 준 signal이 취소되면 함께 취소, (2) 기한이 지나면 스스로 취소
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, TIMEOUT_MS);
  const outer = init.signal;
  const onOuterAbort = () => controller.abort();
  if (outer?.aborted) controller.abort();
  else outer?.addEventListener("abort", onOuterAbort, { once: true });

  try {
    // cache: "no-store" → Next.js 서버 컴포넌트에서 호출해도 항상 최신 데이터를 가져온다.
    const response = await fetch(`${BASE_URL}${path}`, {
      cache: "no-store",
      ...init,
      signal: controller.signal,
    });

    if (!response.ok) {
      if (response.status === 404) throw new ApiError("not_found", "Not found.");
      if (response.status === 409) {
        throw new ApiError("invalid_state", "The item is no longer in a valid state.");
      }
      if (response.status === 401 || response.status === 403) {
        throw new Error("You do not have permission to do this.");
      }
      throw new Error(`Request failed (${response.status}).`);
    }
    if (response.status === 204) return undefined as T; // 본문 없는 성공(삭제)
    return (await response.json()) as T;
  } catch (error) {
    // 기한 초과로 우리가 끊은 것은 "오류"로 알린다. (화면이 취소한 AbortError는 규칙 2대로 그대로 통과)
    if (timedOut && !outer?.aborted) throw new Error("Request timed out.");
    throw error;
  } finally {
    clearTimeout(timer);
    outer?.removeEventListener("abort", onOuterAbort);
  }
}

/** 정렬 상태 → 쿼리스트링. 정렬이 없으면 빈 문자열(서버 기본 순서). */
function sortQuery(sort: SortState | JobSortState): string {
  if (!sort) return "";
  const params = new URLSearchParams({ sortKey: sort.key, direction: sort.direction });
  return `?${params.toString()}`;
}

const segment = encodeURIComponent; // 경로에 들어가는 id는 반드시 인코딩

export function fetchMeeting(meetingId: string, signal?: AbortSignal): Promise<Meeting> {
  return request<Meeting>(`/meetings/${segment(meetingId)}`, { signal });
}

export function fetchActionItems(
  meetingId: string,
  sort: SortState,
  signal?: AbortSignal,
): Promise<ActionItem[]> {
  return request<ActionItem[]>(`/meetings/${segment(meetingId)}/action-items${sortQuery(sort)}`, { signal });
}

export function deleteActionItem(id: string, signal?: AbortSignal): Promise<void> {
  return request<void>(`/action-items/${segment(id)}`, { method: "DELETE", signal });
}

export function fetchAdminJobs(sort: JobSortState, signal?: AbortSignal): Promise<AdminJob[]> {
  return request<AdminJob[]>(`/admin/jobs${sortQuery(sort)}`, { signal });
}

export function retryJob(id: string, signal?: AbortSignal): Promise<AdminJob> {
  return request<AdminJob>(`/admin/jobs/${segment(id)}/retry`, { method: "POST", signal });
}

export function killJob(id: string, signal?: AbortSignal): Promise<AdminJob> {
  return request<AdminJob>(`/admin/jobs/${segment(id)}/kill`, { method: "POST", signal });
}
