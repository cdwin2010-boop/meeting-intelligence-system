"use client";

/*
 * 개인별 할 일 캘린더(작업 B-2): 월간·주간 보기, 기한 미확정 별도 목록, 칸이 차면 "+N건", 카드 클릭 시 상세 모달.
 * - 데이터: GET /api/me/calendar (보이는 기간만 조회, 기간·범위가 바뀔 때만 다시 조회). 열람 범위는 서버가 판정한다.
 * - 마감일은 날짜 문자열 그대로 칸에 넣는다(lib/v2/calendar.ts, 시간대 변환 없음).
 * - 키보드: 날짜 칸은 화살표(←→ 하루, ↑↓ 한 주)로 이동하고 Enter 로 그 날의 업무 목록을 연다.
 */
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button, Modal, StatusDot } from "@/components/mono";
import { StatusChip, type StatusChipTone } from "@/components/v2/StatusChip";
import {
  addDays,
  addMonths,
  CALENDAR_ICS_FILE_NAME,
  DAY_CELL_VISIBLE,
  dayLabel,
  downloadCalendarIcs,
  getCalendar,
  groupByDate,
  monthGrid,
  sameMonth,
  todayYmd,
  WEEKDAY_LABELS,
  weekGrid,
  type CalendarItem,
  type CalendarResponse,
  type CalendarScope,
} from "@/lib/v2/calendar";
import { isAbortError } from "@/lib/v2/errors";
import { getMeeting, type ActionItem, type MeetingDetail } from "@/lib/v2/meetings";

type ViewMode = "month" | "week";

/** 초 → HH:MM:SS(근거 시각 표시) */
function formatSeconds(sec: number): string {
  const total = Math.max(0, Math.floor(sec));
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}`;
}

const STATUS_CHIP: Record<CalendarItem["status"], { tone: StatusChipTone; label: string }> = {
  pending: { tone: "pending", label: "확정 대기" },
  confirmed: { tone: "progress", label: "진행 중" },
  closed: { tone: "done", label: "종결" },
};

function ItemCard({ item, showAssignee, onOpen }: { item: CalendarItem; showAssignee: boolean; onOpen: (item: CalendarItem) => void }) {
  const chip = STATUS_CHIP[item.status];
  return (
    <button
      type="button"
      onClick={() => onOpen(item)}
      aria-label={`${item.title} · ${chip.label}${showAssignee && item.assignee ? ` · 담당 ${item.assignee.name}` : ""}`}
      className="mn-focus flex w-full flex-col gap-0.5 rounded-mn-control border border-mn-border bg-mn-surface px-2 py-1 text-left text-xs text-mn-text hover:bg-mn-elevated"
    >
      <span className="line-clamp-2 break-words">{item.title}</span>
      <span className="text-[11px] text-mn-muted">
        {chip.label}
        {showAssignee && item.assignee ? ` · ${item.assignee.name}` : ""}
      </span>
    </button>
  );
}

export function TodoCalendar({ manager }: { manager: boolean }) {
  const [view, setView] = useState<ViewMode>("month");
  const [scope, setScope] = useState<CalendarScope>("self");
  const [anchor, setAnchor] = useState<string>(() => todayYmd());
  const [focusDay, setFocusDay] = useState<string>(() => todayYmd());
  const [state, setState] = useState<{ kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; data: CalendarResponse }>({ kind: "loading" });
  const [dayList, setDayList] = useState<string | null>(null);
  const [selected, setSelected] = useState<CalendarItem | null>(null);
  const [icsError, setIcsError] = useState<string | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const today = useMemo(() => todayYmd(), []);

  const days = useMemo(() => (view === "month" ? monthGrid(anchor) : weekGrid(anchor)), [view, anchor]);
  const from = days[0];
  const to = days[days.length - 1];

  useEffect(() => {
    const controller = new AbortController();
    setState({ kind: "loading" });
    getCalendar(from, to, scope, controller.signal)
      .then((data) => setState({ kind: "ready", data }))
      .catch((error) => {
        if (isAbortError(error)) return;
        setState({ kind: "error", message: error instanceof Error && error.message ? error.message : "캘린더를 불러오지 못했습니다." });
      });
    return () => controller.abort();
  }, [from, to, scope]);

  const byDate = useMemo(() => (state.kind === "ready" ? groupByDate(state.data.dated.items) : new Map<string, CalendarItem[]>()), [state]);

  // 캘린더 파일(.ics): 지금 보는 범위(내 업무/전체 업무)의 진행 중·기한 확정 업무를 내려받는다
  async function exportIcs() {
    setIcsError(null);
    try {
      const blob = await downloadCalendarIcs(scope);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = CALENDAR_ICS_FILE_NAME;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      if (!isAbortError(error)) setIcsError(error instanceof Error && error.message ? error.message : "캘린더 파일을 내려받지 못했습니다.");
    }
  }

  function move(step: -1 | 1) {
    const next = view === "month" ? addMonths(anchor, step) : addDays(anchor, step * 7);
    setAnchor(next);
    setFocusDay(next);
  }

  function goToday() {
    setAnchor(today);
    setFocusDay(today);
  }

  function focusCell(day: string) {
    setFocusDay(day);
    // 보이는 범위를 벗어나면 기준일을 옮긴다(다음 렌더에서 칸이 생긴다)
    if (!days.includes(day)) setAnchor(day);
    requestAnimationFrame(() => gridRef.current?.querySelector<HTMLElement>(`[data-day="${day}"]`)?.focus());
  }

  function onCellKey(event: React.KeyboardEvent<HTMLButtonElement>, day: string) {
    const step = event.key === "ArrowLeft" ? -1 : event.key === "ArrowRight" ? 1 : event.key === "ArrowUp" ? -7 : event.key === "ArrowDown" ? 7 : 0;
    if (!step) return;
    event.preventDefault();
    focusCell(addDays(day, step));
  }

  const title = view === "month" ? `${anchor.slice(0, 4)}년 ${Number(anchor.slice(5, 7))}월` : `${dayLabel(days[0])} ~ ${dayLabel(days[6])}`;
  const showAssignee = scope === "all";
  const dayItems = dayList ? byDate.get(dayList) ?? [] : [];

  return (
    <section aria-label="할 일 캘린더" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1" role="group" aria-label="기간 이동">
          <Button type="button" size="sm" variant="secondary" onClick={() => move(-1)} aria-label={view === "month" ? "이전 달" : "이전 주"}>
            ←
          </Button>
          <Button type="button" size="sm" variant="secondary" onClick={goToday}>
            오늘
          </Button>
          <Button type="button" size="sm" variant="secondary" onClick={() => move(1)} aria-label={view === "month" ? "다음 달" : "다음 주"}>
            →
          </Button>
        </div>
        <h2 aria-live="polite" className="font-mn-mono text-base font-semibold text-mn-text">
          {title}
        </h2>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1" role="group" aria-label="보기 방식">
            {(["month", "week"] as const).map((mode) => (
              <Button key={mode} type="button" size="sm" variant={view === mode ? "primary" : "secondary"} aria-pressed={view === mode} onClick={() => setView(mode)}>
                {mode === "month" ? "월간" : "주간"}
              </Button>
            ))}
          </div>
          {manager ? (
            <div className="flex items-center gap-1" role="group" aria-label="업무 범위">
              {(["self", "all"] as const).map((value) => (
                <Button key={value} type="button" size="sm" variant={scope === value ? "primary" : "secondary"} aria-pressed={scope === value} onClick={() => setScope(value)}>
                  {value === "self" ? "내 업무" : "전체 업무"}
                </Button>
              ))}
            </div>
          ) : null}
          <Button type="button" size="sm" variant="secondary" onClick={() => void exportIcs()}>
            캘린더 파일(.ics) 내려받기
          </Button>
        </div>
      </div>
      <p className="text-xs text-mn-muted">기한이 있는 진행 중 업무만 포함됩니다.</p>
      {icsError ? (
        <p role="alert" className="text-sm">
          <StatusDot tone="error" label={icsError} />
        </p>
      ) : null}

      {state.kind === "loading" ? (
        <p role="status" className="text-sm text-mn-muted">
          캘린더를 불러오는 중…
        </p>
      ) : null}
      {state.kind === "error" ? (
        <p role="alert" className="text-sm">
          <StatusDot tone="error" label={`캘린더를 불러오지 못했습니다 · ${state.message}`} />
        </p>
      ) : null}

      <div className="overflow-x-auto rounded-mn-card border border-mn-border" tabIndex={-1}>
      <div ref={gridRef} role="grid" aria-label={`${title} 마감일 캘린더`} className="min-w-[560px]">
        <div role="row" className="grid grid-cols-7 border-b border-mn-border bg-mn-surface">
          {WEEKDAY_LABELS.map((label) => (
            <div key={label} role="columnheader" className="px-2 py-1 text-center text-[13px] text-mn-muted">
              {label}
            </div>
          ))}
        </div>
        {Array.from({ length: days.length / 7 }, (_, row) => (
          <div key={row} role="row" className="grid grid-cols-7">
            {days.slice(row * 7, row * 7 + 7).map((day) => {
              const items = byDate.get(day) ?? [];
              const shown = items.slice(0, DAY_CELL_VISIBLE);
              const rest = items.length - shown.length;
              const outside = view === "month" && !sameMonth(day, anchor);
              return (
                <div
                  key={day}
                  role="gridcell"
                  aria-label={`${dayLabel(day)} 업무 ${items.length}건`}
                  className={`flex min-h-[96px] flex-col gap-1 border-b border-r border-mn-border p-1 ${outside ? "bg-mn-bg opacity-60" : "bg-mn-surface"} ${view === "week" ? "min-h-[200px]" : ""}`}
                >
                  <button
                    type="button"
                    data-day={day}
                    tabIndex={focusDay === day ? 0 : -1}
                    onFocus={() => setFocusDay(day)}
                    onKeyDown={(event) => onCellKey(event, day)}
                    onClick={() => items.length && setDayList(day)}
                    aria-label={`${dayLabel(day)}, 업무 ${items.length}건${items.length ? " 목록 열기" : ""}`}
                    className={`mn-focus self-start rounded-mn-control px-1 font-mn-mono text-xs ${day === today ? "bg-mn-accent text-mn-bg" : "text-mn-text"}`}
                  >
                    {Number(day.slice(8, 10))}
                  </button>
                  {shown.map((item) => (
                    <ItemCard key={item.itemId} item={item} showAssignee={showAssignee} onOpen={setSelected} />
                  ))}
                  {rest > 0 ? (
                    <button
                      type="button"
                      onClick={() => setDayList(day)}
                      aria-label={`${dayLabel(day)} 업무 ${rest}건 더 보기`}
                      className="mn-focus rounded-mn-control px-1 text-left text-xs text-mn-muted hover:text-mn-text"
                    >
                      +{rest}건
                    </button>
                  ) : null}
                </div>
              );
            })}
          </div>
        ))}
      </div>
      </div>

      {state.kind === "ready" && state.data.dated.total > state.data.dated.items.length ? (
        <p role="status" className="text-xs text-mn-muted">
          이 기간의 업무가 많아 일부만 표시했습니다({state.data.dated.items.length}/{state.data.dated.total}건).
        </p>
      ) : null}

      <section aria-label="기한 미확정 업무" className="flex flex-col gap-2 rounded-mn-card border border-mn-border bg-mn-surface p-4">
        <h3 className="text-sm font-semibold text-mn-text">
          기한 미확정{state.kind === "ready" ? ` · ${state.data.undetermined.total}건` : ""}
        </h3>
        {state.kind === "ready" && state.data.undetermined.items.length === 0 ? (
          <p className="text-xs text-mn-muted">기한이 정해지지 않은 업무가 없습니다.</p>
        ) : null}
        <ul className="flex flex-col gap-1">
          {state.kind === "ready"
            ? state.data.undetermined.items.map((item) => (
                <li key={item.itemId}>
                  <ItemCard item={item} showAssignee={showAssignee} onOpen={setSelected} />
                </li>
              ))
            : null}
        </ul>
      </section>

      <Modal
        open={dayList !== null}
        onClose={() => setDayList(null)}
        title={dayList ? `${dayLabel(dayList)} 업무 ${dayItems.length}건` : "업무 목록"}
        cancelLabel="닫기"
        confirmLabel="닫기"
        onConfirm={() => setDayList(null)}
      >
        <ul className="flex flex-col gap-2">
          {dayItems.map((item) => (
            <li key={item.itemId}>
              <ItemCard
                item={item}
                showAssignee={showAssignee}
                onOpen={(picked) => {
                  setDayList(null);
                  setSelected(picked);
                }}
              />
            </li>
          ))}
        </ul>
      </Modal>

      {selected ? <ItemDetailModal item={selected} onClose={() => setSelected(null)} /> : null}
    </section>
  );
}

/** 카드 상세: 회의록 정보와 업무 상세(담당자·기한·상태·근거 시각과 인용문). 회의록 상세 응답을 그대로 쓴다 */
function ItemDetailModal({ item, onClose }: { item: CalendarItem; onClose: () => void }) {
  const router = useRouter();
  const [state, setState] = useState<{ kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; meeting: MeetingDetail }>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    getMeeting(item.meetingId, controller.signal)
      .then((meeting) => setState({ kind: "ready", meeting }))
      .catch((error) => {
        if (isAbortError(error)) return;
        setState({ kind: "error", message: error instanceof Error && error.message ? error.message : "상세를 불러오지 못했습니다." });
      });
    return () => controller.abort();
  }, [item.meetingId]);

  const detail: ActionItem | undefined = state.kind === "ready" ? state.meeting.actionItems.find((candidate) => candidate.id === item.itemId) : undefined;
  const chip = STATUS_CHIP[item.status];
  const quote = detail?.evidenceQuote?.trim() ?? "";
  const hasPosition = detail?.evidenceStartSec !== null && detail?.evidenceStartSec !== undefined;
  const unverified = Boolean(detail) && quote !== "" && !hasPosition; // 인용문은 있는데 전사문에서 위치를 확인하지 못함

  return (
    <Modal
      open
      onClose={onClose}
      title={item.title}
      description={`회의록: ${item.meetingTitle}`}
      confirmLabel="회의록 열기"
      cancelLabel="닫기"
      onConfirm={() => router.push(`/v2/meetings/${item.meetingId}?returnTo=${encodeURIComponent("/v2")}`)}
      panelClassName="w-full max-w-lg"
    >
      <dl className="grid grid-cols-[88px_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm">
        <dt className="text-mn-muted">상태</dt>
        <dd>
          <StatusChip tone={chip.tone} label={chip.label} />
        </dd>
        <dt className="text-mn-muted">담당자</dt>
        <dd className="text-mn-text">{item.assignee?.name ?? "미지정"}</dd>
        <dt className="text-mn-muted">기한</dt>
        <dd className="font-mn-mono text-mn-text">{item.dueDate ?? "기한 미확정"}</dd>
        {state.kind === "ready" ? (
          <>
            <dt className="text-mn-muted">회의 일시</dt>
            <dd className="font-mn-mono text-mn-text">{state.meeting.heldAt}</dd>
          </>
        ) : null}
        <dt className="text-mn-muted">근거</dt>
        <dd className="flex flex-col gap-1 text-mn-text">
          {state.kind === "loading" ? <span className="text-mn-muted">불러오는 중…</span> : null}
          {state.kind === "error" ? <StatusDot tone="error" label={state.message} /> : null}
          {state.kind === "ready" && !detail ? <span className="text-mn-muted">상세를 찾지 못했습니다.</span> : null}
          {detail ? (
            <>
              {hasPosition ? (
                <span className="font-mn-mono text-xs">
                  {formatSeconds(detail.evidenceStartSec as number)}
                  {detail.evidenceApprox ? " · 근사 위치" : ""}
                </span>
              ) : (
                <span className="text-xs text-mn-muted">근거 위치 없음</span>
              )}
              {quote ? <blockquote className="border-l-2 border-mn-border pl-2 text-sm">{quote}</blockquote> : <span className="text-xs text-mn-muted">근거 인용문 없음</span>}
              {unverified ? <StatusChip tone="needs" label="근거 미검증" /> : null}
            </>
          ) : null}
        </dd>
      </dl>
    </Modal>
  );
}
