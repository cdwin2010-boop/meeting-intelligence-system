/*
 * API 진입점(파사드) — 화면 코드는 항상 "@/lib/api"만 import 합니다.
 *
 * 어떤 구현을 쓸지는 환경변수 하나로 정합니다.
 *   NEXT_PUBLIC_USE_MOCK=true  (기본값) → api-mock.ts  (백엔드 없이 동작)
 *   NEXT_PUBLIC_USE_MOCK=false          → api-http.ts  (FastAPI 서버와 통신)
 *
 * 값을 바꾼 뒤에는 `npm run dev`를 껐다 다시 켜야 반영됩니다.
 */
import * as http from "./api-http";
import * as mock from "./api-mock";

export { ApiError, GpuGuardError, isAbortError } from "./api-errors";

// "false"라고 명시했을 때만 실제 서버를 씁니다. (오타가 나도 안전한 쪽 = 목으로 동작)
const useMock = process.env.NEXT_PUBLIC_USE_MOCK !== "false";
const impl = useMock ? mock : http;

export const isMockApi = useMock;
export const fetchMeetings = impl.fetchMeetings;
export const fetchMeeting = impl.fetchMeeting;
export const fetchActionItems = impl.fetchActionItems;
export const deleteActionItem = impl.deleteActionItem;
export const fetchAdminJobs = impl.fetchAdminJobs;
export const retryJob = impl.retryJob;
export const killJob = impl.killJob;
export const uploadMeetingAudio = impl.uploadMeetingAudio;
export const fetchJob = impl.fetchJob;
