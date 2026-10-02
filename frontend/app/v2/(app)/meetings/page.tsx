"use client";

/*
 * v2 회의록 목록 (시안 docs/ui-v2-mockups/03-meetings.html)
 * - GET /api/meetings 를 쪽 단위(20건)로 조회. 정렬은 서버의 회의 일시 최신순을 그대로 쓴다
 * - 쪽을 바꾸거나 다시 시도하면 이전 요청은 AbortController 로 취소한다
 * - 행(또는 회의명 링크)을 누르면 /v2/meetings/{id} 로 이동. 상태는 색 점과 글자 라벨을 함께 보여 준다
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button, StatusDot, type StatusDotTone } from "@/components/mono";
import { isAbortError } from "@/lib/v2/errors";
import { listMeetings, type MeetingListItem, type MeetingListPage } from "@/lib/v2/meetings";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: MeetingListPage };

const AUTO_CONFIRM_KINDS = new Set(["period_elapsed", "due_reached"]);

/** 회의록 상태 → 점 색 + 글자 라벨 (확정은 자동/그 외를 구분) */
function meetingStatus(meeting: MeetingListItem): { tone: StatusDotTone; label: string } {
  switch (meeting.status) {
    case "processing":
      return { tone: "building", label: "전사·추출 중" };
    case "awaiting_confirmation":
      return { tone: "queued", label: "확정 대기" };
    case "confirmed":
      return { tone: "ready", label: meeting.confirmKind && AUTO_CONFIRM_KINDS.has(meeting.confirmKind) ? "자동 확정됨" : "확정 완료" };
    case "failed":
      return { tone: "error", label: "처리 실패" };
    case "no_content":
      return { tone: "queued", label: "내용 없음" };
    default:
      return { tone: "queued", label: String(meeting.status) };
  }
}

// 처리 중·실패·내용 없음은 업무가 아직(또는 끝내) 없으므로 건수 대신 "—"
const HAS_ITEMS = new Set(["awaiting_confirmation", "confirmed"]);

/** ISO 시각 → 브라우저 현지 시각 YYYY-MM-DD HH:mm (sv-SE 형식이 ISO 와 같다) */
function formatDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("sv-SE", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

const detailPath = (id: number) => `/v2/meetings/${id}`;

function MeetingsTable({ items }: { items: MeetingListItem[] }) {
  const router = useRouter();
  return (
    <div className="overflow-x-auto rounded-mn-card border border-mn-border bg-mn-surface">
      <table aria-label="회의록 목록" className="w-full min-w-[640px] table-fixed border-collapse text-sm">
        <colgroup>
          <col className="w-[180px]" />
          <col />
          <col className="w-[160px]" />
          <col className="w-[96px]" />
        </colgroup>
        <thead>
          <tr className="h-10 border-b border-mn-border text-left text-xs text-mn-muted">
            <th scope="col" className="px-4 font-medium">회의 일시</th>
            <th scope="col" className="px-4 font-medium">회의명</th>
            <th scope="col" className="px-4 font-medium">상태</th>
            <th scope="col" className="px-4 text-right font-medium">업무</th>
          </tr>
        </thead>
        <tbody>
          {items.map((meeting) => {
            const status = meetingStatus(meeting);
            return (
              // 행 전체 클릭으로 이동. 키보드·스크린리더는 회의명 링크로 같은 곳에 간다
              <tr
                key={meeting.id}
                onClick={() => router.push(detailPath(meeting.id))}
                className="h-12 cursor-pointer border-b border-mn-border last:border-b-0 hover:bg-mn-elevated"
              >
                <td className="px-4">
                  <span className="whitespace-nowrap font-mn-mono text-[13px]">{formatDateTime(meeting.heldAt)}</span>
                </td>
                <td className="truncate px-4">
                  <Link
                    href={detailPath(meeting.id)}
                    onClick={(event) => event.stopPropagation()}
                    className="mn-focus rounded-mn-control font-medium text-mn-text hover:underline"
                  >
                    {meeting.title || "(제목 없음)"}
                  </Link>
                </td>
                <td className="px-4">
                  <StatusDot tone={status.tone} label={status.label} />
                </td>
                <td className="px-4 text-right font-mn-mono text-[13px]">
                  {HAS_ITEMS.has(meeting.status) ? meeting.itemCount : <span className="text-mn-muted">—</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function V2MeetingsPage() {
  const [page, setPage] = useState(1);
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async (target: number) => {
    // 이전 요청이 남아 있으면 취소하고 새로 조회
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ kind: "loading" });
    try {
      const data = await listMeetings(target, controller.signal);
      setState({ kind: "ready", data });
    } catch (error) {
      if (isAbortError(error)) return;
      setState({ kind: "error", message: error instanceof Error && error.message ? error.message : "회의록을 불러오지 못했습니다." });
    }
  }, []);

  useEffect(() => {
    void load(page);
  }, [load, page]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const data = state.kind === "ready" ? state.data : null;
  const lastPage = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1;
  const firstIndex = data && data.items.length > 0 ? (data.page - 1) * data.size + 1 : 0;
  const lastIndex = data ? firstIndex + data.items.length - (data.items.length > 0 ? 1 : 0) : 0;

  return (
    <>
      <header>
        <h1 className="text-[28px] font-semibold leading-9 tracking-tight text-mn-text">회의록</h1>
        <p className="mt-1 text-sm text-mn-muted">등록된 회의록과 업무 현황입니다.</p>
      </header>

      {state.kind === "loading" ? (
        <p role="status" className="text-sm text-mn-muted">
          회의록을 불러오는 중…
        </p>
      ) : state.kind === "error" ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-4 rounded-mn-card border border-mn-border bg-mn-surface p-6">
          <span className="text-sm">
            <StatusDot tone="error" label={`회의록을 불러오지 못했습니다 · ${state.message}`} />
          </span>
          <Button size="sm" onClick={() => void load(page)}>
            다시 시도
          </Button>
        </div>
      ) : state.data.total === 0 ? (
        <div role="status" className="rounded-mn-card border border-mn-border bg-mn-surface p-6 text-sm text-mn-muted">
          등록된 회의록이 없습니다.
        </div>
      ) : (
        <>
          <MeetingsTable items={state.data.items} />
          <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-mn-muted">
            <span className="font-mn-mono text-[13px]">
              {firstIndex}–{lastIndex} / {state.data.total}
            </span>
            <div className="flex items-center gap-3">
              <span aria-live="polite" className="font-mn-mono text-[13px]">
                {state.data.page} / {lastPage} 쪽
              </span>
              <Button size="sm" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                이전
              </Button>
              <Button size="sm" disabled={page >= lastPage} onClick={() => setPage((p) => p + 1)}>
                다음
              </Button>
            </div>
          </div>
        </>
      )}
    </>
  );
}
