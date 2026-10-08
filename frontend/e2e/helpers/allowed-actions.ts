/*
 * E2E 가짜 서버용 허용 동작(allowedActions) 계산기. 실제 서버(backend app/auth/actions.py)가 회의록 상세 응답에 넣어 주는 값과 같은 표를 따른다.
 * 가로챈 상세 응답을 이 함수로 감싸면 역할(총괄·지시자·타 관리자·담당자)과 상태(확정 대기·확정·보류·종료·삭제)에 맞는 목록이 붙는다.
 * 상세 객체를 테스트가 바꾸면(업무 확정 등) 응답을 낼 때마다 다시 계산하므로 "동작 직후 서버 기준으로 갱신"도 흉내 낸다.
 */
type Account = { id: number; rank: string };
type Item = { id?: number; status?: string; assignee?: { id: number } | null; supersededBy?: unknown } & Record<string, unknown>;
type Detail = { status?: string; phase?: string; actionItems?: Item[] } & Record<string, unknown>;

export interface AllowOptions {
  /** 관리자일 때 이 회의록의 총괄인지(기본 true). false 면 "총괄 아닌 관리자" */
  lead?: boolean;
  /** 유사 업무 대체(71-1): "request"=검색·대체 요청만(참여자), "direct"=직권 대체까지(프로젝트 총괄·부서장·지시자). 없으면 대체 동작이 나오지 않는다 */
  supersede?: "request" | "direct";
}

const MEETING_ORDER = [
  "confirm_meeting", "hold_meeting", "resume_meeting", "end_meeting", "delete_meeting", "edit_minutes", "upload_update", "add_item",
  "edit_speakers", "reprocess_meeting", "resolve_change_request", "request_change", "download_excel", "view_history",
];
const ITEM_ORDER = ["confirm_item", "close_item", "delete_item", "set_assignee", "set_due", "request_change", "find_similar_items", "request_supersede", "supersede_item"];

const order = (names: string[], all: string[]) => all.filter((n) => names.includes(n));

export function meetingActions(detail: Detail, account: Account, options: AllowOptions = {}): string[] {
  const phase = detail.phase ?? "active";
  const status = detail.status ?? "confirmed";
  const lead = account.rank === "executive" || (account.rank === "manager" && options.lead !== false);
  const locked = phase !== "active";
  const holdable = status === "awaiting_confirmation" || status === "confirmed";
  const out = ["request_change", "download_excel", "view_history"];
  if (account.rank !== "staff") out.push("resolve_change_request");
  if (lead) {
    if (!locked) {
      out.push("edit_minutes", "upload_update", "add_item", "edit_speakers");
      if (status === "failed") out.push("reprocess_meeting"); // 처리 중인 작업이 없을 때(가짜 서버는 작업을 보지 않는다)
      if (status === "awaiting_confirmation") out.push("confirm_meeting");
      if (holdable) out.push("hold_meeting", "end_meeting");
    }
    if (holdable && phase === "on_hold") out.push("resume_meeting");
    if (phase !== "deleted" && status !== "processing") out.push("delete_meeting");
  }
  return order(out, MEETING_ORDER);
}

export function itemActions(item: Item, detail: Detail, account: Account, options: AllowOptions = {}): string[] {
  if (item.status === "deleted") return [];
  // 대체된 업무에는 수정 요청 외 동작이 나오지 않는다
  if (item.supersededBy) return ["request_change"];
  const lead = account.rank === "executive" || (account.rank === "manager" && options.lead !== false);
  const locked = (detail.phase ?? "active") !== "active";
  const out = ["request_change"];
  if (!locked) {
    const canWrite = account.rank === "executive" || (account.rank === "manager" && (lead || item.assignee?.id === account.id));
    if (canWrite) {
      if (item.status === "pending") out.push("confirm_item");
      if (item.status === "confirmed") out.push("close_item");
      if (item.status === "pending" || item.status === "confirmed") out.push("set_assignee", "set_due");
    }
    if (lead) out.push("delete_item");
    if (options.supersede && (item.status === "pending" || item.status === "confirmed")) {
      out.push("find_similar_items", "request_supersede");
      if (options.supersede === "direct") out.push("supersede_item");
    }
  }
  return order(out, ITEM_ORDER);
}

/** 상세 응답에 회의록·업무 단위 allowedActions 를 붙인 복사본(원본은 바꾸지 않는다) */
export function withAllowed<T extends Detail>(detail: T, account: Account, options: AllowOptions = {}): T {
  return {
    ...detail,
    allowedActions: meetingActions(detail, account, options),
    actionItems: (detail.actionItems ?? []).map((item) => ({ ...item, allowedActions: itemActions(item, detail, account, options) })),
  };
}

/**
 * 가짜 서버용: 가로챈 쓰기 요청이 성공 JSON 으로 응답하면 그 본문을 cb 로 알려 준다(상세 상태를 같이 바꾸는 데 쓴다).
 * 서버는 쓰기 뒤에 상세를 다시 받으면 바뀐 상태를 주므로, 가짜 서버도 같은 일을 하게 한다.
 */
export function onJson(route: { fulfill: (options?: never) => Promise<void> }, cb: (body: any) => void): void {
  const original = (route.fulfill as (options?: unknown) => Promise<void>).bind(route);
  (route as { fulfill: unknown }).fulfill = (options?: { status?: number; body?: unknown }) => {
    if ((options?.status ?? 200) < 300 && typeof options?.body === "string") {
      try {
        cb(JSON.parse(options.body));
      } catch {
        // JSON 이 아니면 무시
      }
    }
    return original(options);
  };
}
