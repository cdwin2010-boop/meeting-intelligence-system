"use client";

/*
 * v2 할 일 화면 (시안 docs/ui-v2-mockups/02-home.html)
 * - GET /api/me/todos(확정 대기·보완 필요·내 업무·자동 확정 미열람) + GET /api/me/notices(확정 안내)를 함께 조회
 * - 화면을 떠나거나 다시 시도하면 이전 요청은 AbortController 로 취소한다
 * - 상태는 색 점과 글자 라벨을 함께 보여 준다. 읽기 전용(확인 처리·확정 버튼은 다음 단계)
 * - 수정 요청 대기(pendingChangeRequests): 서버가 내가 해결할 수 있는 미해결 요청만 준다. 건수가 0이면 묶음을 숨기고,
 *   항목을 누르면 그 회의록 상세(/v2/meetings/{id})로 이동한다
 * - 수정 요청 대기·보완 필요 업무 항목은 회의록 상세로 갈 때 returnTo=/v2 를 붙여, 상세에서 "할 일로 돌아가기"를 쓸 수 있다(작업 85-1).
 *   보완 필요 업무는 item=업무 번호도 붙여 상세가 그 업무 줄로 이동·강조한다
 */
import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { Badge, Button, StatusDot } from "@/components/mono";
import { useAuth } from "@/components/v2/AuthProvider";
import { Avatar } from "@/components/v2/Avatar";
import { formatDateTime, V2_MEETINGS_PATH } from "@/components/v2/meeting-display";
import { StatusChip, type StatusChipTone } from "@/components/v2/StatusChip";
import { SummaryCard } from "@/components/v2/SummaryCard";
import { isAbortError, type MissingField } from "@/lib/v2/errors";
import {
  getNotices,
  getTodos,
  type ConfirmKind,
  type MyItem,
  type Notice,
  type PendingChangeRequest,
  type TodoList,
  type Todos,
} from "@/lib/v2/todos";
import { withReturnTo } from "@/lib/v2/return-to";
import { isManager } from "@/lib/v2/types";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; todos: Todos; notices: Notice[] };

const CONFIRM_KIND_LABEL: Record<ConfirmKind, string> = {
  manager: "관리자 확정",
  registration: "등록 시 확정",
  period_elapsed: "기간 경과로 자동 확정",
  due_reached: "마감일 도달로 자동 확정",
};

const MISSING_LABEL: Record<MissingField, string> = {
  title: "업무명 필요",
  assignee: "담당자 필요",
  dueDate: "기한 필요",
};

const ITEM_STATUS: Record<MyItem["status"], { tone: StatusChipTone; label: string }> = {
  pending: { tone: "pending", label: "확정 대기" },
  confirmed: { tone: "done", label: "확정됨" },
};

const ENTITY_LABEL = { meeting: "회의록", action_item: "업무" } as const;

/** ISO 시각 → 브라우저 현지 날짜 YYYY-MM-DD (sv-SE 형식이 ISO 날짜와 같다) */
function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString("sv-SE");
}

const todayLabel = () =>
  new Date().toLocaleDateString("ko-KR", { year: "numeric", month: "long", day: "numeric", weekday: "long" });

function Mono({ children }: { children: ReactNode }) {
  return <span className="whitespace-nowrap font-mn-mono text-[13px]">{children}</span>;
}

/** 카드 한 묶음: 제목 + 전체 건수, 50건 넘으면 일부만 보인다는 안내 */
function Section({ title, list, children }: { title: string; list: TodoList<unknown>; children: ReactNode }) {
  return (
    <section aria-label={title} className="overflow-hidden rounded-mn-card border border-mn-border bg-mn-surface">
      <div className="flex items-center justify-between border-b border-mn-border px-5 py-4">
        <h2 className="text-[17px] font-bold leading-6">{title}</h2>
        <Badge mono>{list.total}</Badge>
      </div>
      <ul className="divide-y divide-mn-border">{children}</ul>
      {list.total > list.items.length ? (
        <p className="border-t border-mn-border px-5 py-3 text-xs text-mn-muted">
          앞의 <span className="font-mn-mono">{list.items.length}</span>건만 표시합니다 (전체{" "}
          <span className="font-mn-mono">{list.total}</span>건)
        </p>
      ) : null}
    </section>
  );
}

function Row({ title, meta, aside, href, linkLabel }: { title: string; meta?: ReactNode; aside?: ReactNode; href?: string; linkLabel?: string }) {
  const body = (
    <>
      <div className="min-w-0">
        <p className="truncate font-medium">{title}</p>
        {meta ? <p className="mt-1 text-[13px] leading-5 text-mn-muted">{meta}</p> : null}
      </div>
      {aside ? <div className="flex shrink-0 flex-wrap items-center gap-3">{aside}</div> : null}
    </>
  );
  // href 가 있으면 줄 전체가 링크다(누르면 해당 화면으로 이동)
  if (href) {
    return (
      <li>
        <Link href={href} aria-label={linkLabel} className="mn-focus flex items-center justify-between gap-4 px-5 py-4 hover:bg-mn-elevated">
          {body}
        </Link>
      </li>
    );
  }
  return <li className="flex items-center justify-between gap-4 px-5 py-4">{body}</li>;
}

const EMPTY_LIST: TodoList<PendingChangeRequest> = { total: 0, items: [] };

/** 수정 요청 대기 한 줄: 누르면 회의록 상세로 이동 */
function ChangeRequestRow({ request }: { request: PendingChangeRequest }) {
  const target = request.itemId === null ? "회의록 전체" : request.itemTitle || "(업무명 없음)";
  return (
    <li>
      <Link
        href={withReturnTo(`${V2_MEETINGS_PATH}/${request.meetingId}`, "/v2", request.itemId === null ? {} : { item: request.itemId })}
        className="mn-focus flex items-center justify-between gap-4 px-5 py-4 hover:bg-mn-elevated"
      >
        <div className="min-w-0">
          <p className="truncate font-medium">{request.meetingTitle || "(제목 없음)"}</p>
          <p className="mt-1 truncate text-sm">
            {request.kind === "supersede" ? <><Badge>대체 요청</Badge> </> : null}
            {request.commentPreview}
          </p>
          <p className="mt-1 text-[13px] leading-5 text-mn-muted">
            {target}
            {request.kind === "supersede" ? <> · 대체될 업무 <Mono>#{request.supersedesItemId ?? "?"}</Mono></> : null} · {request.requester?.name ?? "알 수 없음"} · <Mono>{formatDateTime(request.createdAt)}</Mono>
          </p>
        </div>
        <span className="shrink-0">
          <StatusChip tone="revision" label="해결 대기" />
        </span>
      </Link>
    </li>
  );
}

/** 기한 값이 없거나 미정이면 "미확정"(보조색)으로 보인다 */
function DueCell({ item }: { item: MyItem }) {
  if (item.dueUndetermined || !item.dueDate) return <span className="text-mn-muted">미확정</span>;
  return <Mono>{item.dueDate}</Mono>;
}

function MyItemsTable({ list, assigneeName }: { list: TodoList<MyItem>; assigneeName: string }) {
  return (
    <section aria-label="내 업무" className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-[17px] font-bold leading-6">내 업무</h2>
        <Badge mono>{list.total}</Badge>
      </div>
      <div className="overflow-x-auto rounded-mn-card border border-mn-border bg-mn-surface">
        <table aria-label="내 업무" className="w-full min-w-[640px] table-fixed border-collapse text-[15px] leading-[23px]">
          <colgroup>
            <col />
            <col className="w-[160px]" />
            <col className="w-[140px]" />
            <col className="w-[140px]" />
          </colgroup>
          <thead>
            <tr className="h-12 border-b border-mn-border text-left text-[13px] leading-5 text-mn-muted">
              <th scope="col" className="px-4 font-medium">업무명</th>
              <th scope="col" className="px-4 font-medium">담당자</th>
              <th scope="col" className="px-4 font-medium">완료 기한</th>
              <th scope="col" className="px-4 font-medium">상태</th>
            </tr>
          </thead>
          <tbody>
            {list.items.length === 0 ? (
              <tr className="h-16">
                <td colSpan={4} className="px-4 text-center text-mn-muted">
                  담당 업무가 없습니다.
                </td>
              </tr>
            ) : (
              list.items.map((item) => {
                const status = ITEM_STATUS[item.status] ?? { tone: "queued" as const, label: item.status };
                return (
                  <tr key={item.itemId} className="h-16 border-b border-mn-border last:border-b-0">
                    <td className="truncate px-4">{item.title}</td>
                    <td className="px-4">
                      {/* 내 업무 표의 담당자는 로그인한 본인이다. 이름이 없으면 "미확정" */}
                      {assigneeName ? (
                        <span className="flex items-center gap-2">
                          <Avatar name={assigneeName} />
                          <span className="truncate">{assigneeName}</span>
                        </span>
                      ) : (
                        <span className="text-mn-muted">미확정</span>
                      )}
                    </td>
                    <td className="px-4">
                      <DueCell item={item} />
                    </td>
                    <td className="px-4">
                      <StatusChip tone={status.tone} label={status.label} />
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      {list.total > list.items.length ? (
        <p className="text-[13px] leading-5 text-mn-muted">
          기한이 가까운 <span className="font-mn-mono">{list.items.length}</span>건만 표시합니다 (전체{" "}
          <span className="font-mn-mono">{list.total}</span>건)
        </p>
      ) : null}
    </section>
  );
}

function TodoContent({ todos, notices, manager, assigneeName }: { todos: Todos; notices: Notice[]; manager: boolean; assigneeName: string }) {
  const { awaitingConfirmMeetings: awaiting, needsCompletionItems: needs, myItems, unreadAutoConfirmed: unread } = todos;
  const changeRequests = todos.pendingChangeRequests ?? EMPTY_LIST;
  const confirmNotices = notices.filter((n) => n.kind === "confirmed_notice");
  const empty =
    awaiting.total + needs.total + myItems.total + unread.total + changeRequests.total === 0 && confirmNotices.length === 0;

  if (empty) {
    return (
      <div role="status" className="rounded-mn-card border border-mn-border bg-mn-surface p-6 text-sm text-mn-muted">
        지금 처리할 일이 없습니다.
      </div>
    );
  }

  return (
    <>
      {confirmNotices.length > 0 ? (
        <section aria-label="확정 안내" className="rounded-mn-card border border-mn-border bg-mn-surface">
          <h2 className="sr-only">확정 안내</h2>
          <ul className="divide-y divide-mn-border">
            {confirmNotices.map((notice) => (
              <li key={notice.id} className="flex flex-wrap items-center gap-3 px-5 py-4">
                <StatusChip tone="done" label="확정 안내" />
                <span className="font-medium">{notice.payload.title || "(제목 없음)"}</span>
                <span className="text-mn-muted">
                  {ENTITY_LABEL[notice.entityType] ?? notice.entityType} ·{" "}
                  {notice.payload.confirmKind ? CONFIRM_KIND_LABEL[notice.payload.confirmKind] ?? "확정" : "확정"} ·{" "}
                  <Mono>{formatDate(notice.createdAt)}</Mono>
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {/* 요약 카드: 칸 최소 200px 로 자동 맞춤(좁은 화면은 한 칸씩) */}
      <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-4">
        {manager ? (
          <>
            <SummaryCard label="확정 대기 회의록" value={awaiting.total} hint="관리자 확정을 기다립니다" />
            <SummaryCard label="보완 필요 업무" value={needs.total} hint="빠진 항목을 채워야 합니다" />
          </>
        ) : null}
        <SummaryCard label="내 업무" value={myItems.total} hint="내가 맡은 업무입니다" />
        <SummaryCard label="자동 확정됨 (미열람)" value={unread.total} hint="확정 내용을 확인해 주세요" />
      </div>

      {manager && (awaiting.total > 0 || needs.total > 0) ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {awaiting.total > 0 ? (
            <Section title="확정 대기 회의록" list={awaiting}>
              {awaiting.items.map((m) => (
                <Row
                  key={m.id}
                  title={m.title}
                  meta={
                    <>
                      자동 확정 예정 <Mono>{formatDate(m.autoConfirmAt)}</Mono>
                    </>
                  }
                  aside={<StatusChip tone="pending" label="확정 대기" />}
                />
              ))}
            </Section>
          ) : null}
          {needs.total > 0 ? (
            <Section title="보완 필요 업무" list={needs}>
              {needs.items.map((item) => (
                <Row
                  key={item.itemId}
                  title={item.title || "(업무명 없음)"}
                  href={withReturnTo(`${V2_MEETINGS_PATH}/${item.meetingId}`, "/v2", { item: item.itemId })}
                  linkLabel={`보완 필요 업무로 이동: ${item.title || "업무명 없음"}`}
                  aside={item.missingFields.map((field) => (
                    <StatusChip key={field} tone="needs" label={MISSING_LABEL[field] ?? field} />
                  ))}
                />
              ))}
            </Section>
          ) : null}
        </div>
      ) : null}

      {changeRequests.total > 0 ? (
        <Section title="수정 요청 대기" list={changeRequests}>
          {changeRequests.items.map((request) => (
            <ChangeRequestRow key={request.requestId} request={request} />
          ))}
        </Section>
      ) : null}

      {unread.total > 0 ? (
        <Section title="자동 확정됨 (미열람)" list={unread}>
          {unread.items.map((entry) => (
            <Row
              key={`${entry.entityType}-${entry.itemId ?? entry.meetingId}`}
              title={entry.title}
              meta={
                <>
                  {ENTITY_LABEL[entry.entityType] ?? entry.entityType} · {CONFIRM_KIND_LABEL[entry.confirmKind] ?? entry.confirmKind} ·{" "}
                  <Mono>{formatDate(entry.confirmedAt)}</Mono>
                </>
              }
              aside={<StatusChip tone="done" label="자동 확정됨 (미열람)" />}
            />
          ))}
        </Section>
      ) : null}

      <MyItemsTable list={myItems} assigneeName={assigneeName} />
    </>
  );
}

export default function V2TodoPage() {
  const { account } = useAuth();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    // 이전 요청이 남아 있으면 취소하고 새로 조회
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ kind: "loading" });
    try {
      const [todos, notices] = await Promise.all([getTodos(controller.signal), getNotices(controller.signal)]);
      setState({ kind: "ready", todos, notices });
    } catch (error) {
      if (isAbortError(error)) return;
      setState({ kind: "error", message: error instanceof Error && error.message ? error.message : "할 일을 불러오지 못했습니다." });
    }
  }, []);

  useEffect(() => {
    void load();
    return () => controllerRef.current?.abort();
  }, [load]);

  return (
    <>
      <header>
        <h1 className="mn-page-title text-mn-text">할 일</h1>
        <p className="mt-1 text-[13px] leading-5 text-mn-muted">{todayLabel()} · 오늘 처리할 일을 먼저 보여 드립니다.</p>
      </header>

      {state.kind === "loading" ? (
        <p role="status" className="text-sm text-mn-muted">
          할 일을 불러오는 중…
        </p>
      ) : state.kind === "error" ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-4 rounded-mn-card border border-mn-border bg-mn-surface p-6">
          <span className="text-sm">
            <StatusDot tone="error" label={`할 일을 불러오지 못했습니다 · ${state.message}`} />
          </span>
          <Button size="sm" onClick={() => void load()}>
            다시 시도
          </Button>
        </div>
      ) : (
        <TodoContent todos={state.todos} notices={state.notices} manager={isManager(account?.rank)} assigneeName={account?.name ?? ""} />
      )}
    </>
  );
}
