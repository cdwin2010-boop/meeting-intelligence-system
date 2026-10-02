/*
 * v2 회의록 목록 API(읽기 전용). 응답 모양은 backend/app/api/meeting_schemas.py 의 MeetingListPage(camelCase)와 맞춘다.
 * 열람 권한은 서버가 판정한다(관리자 이상은 고객사 전체, 담당자는 참석·담당·등록한 회의록만).
 */
import { request } from "./http";
import type { ConfirmKind } from "./todos";

export type MeetingStatus = "processing" | "awaiting_confirmation" | "confirmed" | "failed" | "no_content";

export interface MeetingListItem {
  id: number;
  title: string;
  heldAt: string;
  registeredBy: { id: number; name: string };
  origin: string;
  status: MeetingStatus;
  confirmKind: ConfirmKind | null;
  itemCount: number;
  needsCompletionCount: number;
  autoConfirmAt: string | null;
}

/** GET /api/meetings (회의 일시 최신순, page 는 1부터) */
export interface MeetingListPage {
  items: MeetingListItem[];
  total: number;
  page: number;
  size: number;
}

export const MEETINGS_PAGE_SIZE = 20;

export function listMeetings(page: number, signal?: AbortSignal): Promise<MeetingListPage> {
  const query = new URLSearchParams({ page: String(page), size: String(MEETINGS_PAGE_SIZE) });
  return request<MeetingListPage>(`/meetings?${query.toString()}`, { signal });
}
