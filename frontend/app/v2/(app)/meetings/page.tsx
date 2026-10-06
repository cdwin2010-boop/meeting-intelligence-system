"use client";

/*
 * v2 회의록 목록 (시안 docs/ui-v2-mockups/03-meetings.html)
 * - GET /api/meetings 를 쪽 단위(20건)로 조회. 정렬은 서버의 회의 일시 최신순을 그대로 쓴다
 * - 쪽을 바꾸거나 다시 시도하면 이전 요청은 AbortController 로 취소한다
 * - 행(또는 회의명 링크)을 누르면 /v2/meetings/{id} 로 이동. 상태는 색 점과 글자 라벨을 함께 보여 준다
 * - 단계 탭(진행중 기본·종료·보류·삭제): 탭을 바꾸면 GET /api/meetings?phase=… 로 다시 조회하고 1쪽으로 돌아간다.
 *   보이는 탭은 목록 응답의 availablePhases(관리자 이상 4개, 담당자 진행중·종료)를 따르고, 응답에 없으면 진행중·종료만 보인다
 * - 각 행에 단계 글자 라벨("단계" 열, 색 없음)
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge, Button, StatusDot } from "@/components/mono";
import { detailPath, formatDateTime, meetingStatus, MEETING_PHASES, PHASE_LABEL, phaseText } from "@/components/v2/meeting-display";
import { isAbortError } from "@/lib/v2/errors";
import { listMeetings, type MeetingListItem, type MeetingListPage, type MeetingPhase } from "@/lib/v2/meetings";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: MeetingListPage };

// 처리 중·실패·내용 없음은 업무가 아직(또는 끝내) 없으므로 건수 대신 "—"
const HAS_ITEMS = new Set(["awaiting_confirmation", "confirmed"]);

// 목록 응답에 availablePhases 가 없으면(조회 전·옛 응답) 진행중·종료만 보인다(안전한 쪽)
const DEFAULT_PHASES: MeetingPhase[] = ["active", "ended"];

const EMPTY_TEXT: Record<MeetingPhase, string> = {
  active: "등록된 회의록이 없습니다.",
  ended: "종료된 회의록이 없습니다.",
  on_hold: "보류된 회의록이 없습니다.",
  deleted: "삭제된 회의록이 없습니다.",
};

/** 단계 탭. 현재 탭은 aria-selected 와 흰 밑줄·굵은 글자로 구분 */
function PhaseTabs({ phases, current, onChange }: { phases: MeetingPhase[]; current: MeetingPhase; onChange: (p: MeetingPhase) => void }) {
  return (
    <div role="tablist" aria-label="회의록 단계" className="flex gap-1 overflow-x-auto border-b border-mn-border max-md:flex-nowrap">
      {phases.map((phase) => {
        const selected = phase === current;
        return (
          <button
            key={phase}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(phase)}
            className={`mn-focus -mb-px h-10 shrink-0 whitespace-nowrap rounded-t-mn-control border-b-2 px-4 text-sm max-md:h-11 max-md:aria-selected:before:content-['●_'] ${
              selected ? "border-mn-text font-semibold text-mn-text" : "border-transparent text-mn-muted hover:text-mn-text"
            }`}
          >
            {PHASE_LABEL[phase]}
          </button>
        );
      })}
    </div>
  );
}

/** 카드 모드(768 미만)에서 셀 앞에 보이는 라벨: data-label 속성을 CSS 로 보여 준다(글자 내용·셀 순서는 그대로) */
const CELL_LABEL =
  "max-md:block max-md:px-0 max-md:before:mr-2 max-md:before:inline-block max-md:before:min-w-16 max-md:before:text-xs max-md:before:text-mn-muted max-md:before:content-[attr(data-label)]";

function MeetingsTable({ items }: { items: MeetingListItem[] }) {
  const router = useRouter();
  return (
    <div className="overflow-x-auto rounded-mn-card border border-mn-border bg-mn-surface">
      {/* 768 미만에서는 같은 table/tr/td 를 CSS 로 카드처럼 보이게 한다(DOM·셀 순서 그대로). 표 의미를 위해 role 을 명시한다 */}
      <table role="table" aria-label="회의록 목록" className="w-full min-w-[640px] table-fixed border-collapse text-sm max-md:block max-md:min-w-0">
        <colgroup className="max-md:hidden">
          <col className="w-[180px]" />
          <col />
          <col className="w-[160px]" />
          <col className="w-[96px]" />
          <col className="w-[160px]" />
        </colgroup>
        <thead role="rowgroup" className="max-md:sr-only">
          <tr role="row" className="h-10 border-b border-mn-border text-left text-xs text-mn-muted">
            <th role="columnheader" scope="col" className="px-4 font-medium">회의 일시</th>
            <th role="columnheader" scope="col" className="px-4 font-medium">회의명</th>
            <th role="columnheader" scope="col" className="px-4 font-medium">상태</th>
            <th role="columnheader" scope="col" className="px-4 text-right font-medium">업무</th>
            <th role="columnheader" scope="col" className="px-4 font-medium">단계</th>
          </tr>
        </thead>
        <tbody role="rowgroup" className="max-md:block">
          {items.map((meeting) => {
            const status = meetingStatus(meeting);
            return (
              // 행 전체 클릭으로 이동. 키보드·스크린리더는 회의명 링크로 같은 곳에 간다
              <tr
                key={meeting.id}
                role="row"
                onClick={() => router.push(detailPath(meeting.id))}
                className="h-12 cursor-pointer border-b border-mn-border last:border-b-0 hover:bg-mn-elevated max-md:flex max-md:h-auto max-md:flex-col max-md:gap-1 max-md:px-4 max-md:py-3"
              >
                <td role="cell" data-label="회의 일시" className={`px-4 ${CELL_LABEL}`}>
                  <span className="whitespace-nowrap font-mn-mono text-[13px]">{formatDateTime(meeting.heldAt)}</span>
                </td>
                <td role="cell" data-label="회의명" className={`truncate px-4 max-md:overflow-visible max-md:whitespace-normal max-md:break-words ${CELL_LABEL}`}>
                  <Link
                    href={detailPath(meeting.id)}
                    onClick={(event) => event.stopPropagation()}
                    className="mn-focus rounded-mn-control font-medium text-mn-text hover:underline"
                  >
                    {meeting.title || "(제목 없음)"}
                  </Link>
                </td>
                <td role="cell" data-label="상태" className={`px-4 ${CELL_LABEL}`}>
                  <StatusDot tone={status.tone} label={status.label} />
                </td>
                <td role="cell" data-label="업무" className={`px-4 text-right font-mn-mono text-[13px] max-md:text-left ${CELL_LABEL}`}>
                  {HAS_ITEMS.has(meeting.status) ? meeting.itemCount : <span className="text-mn-muted">—</span>}
                </td>
                <td role="cell" data-label="단계" className={`px-4 ${CELL_LABEL}`}>
                  <Badge>{phaseText(meeting)}</Badge>
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
  // 보이는 탭은 서버가 목록 응답에 준 조회 가능 단계(availablePhases)를 따른다(직급을 직접 보지 않는다)
  const [availablePhases, setAvailablePhases] = useState<string[] | undefined>(undefined);
  const phases = availablePhases ? MEETING_PHASES.filter((p) => availablePhases.includes(p)) : DEFAULT_PHASES;
  const [phase, setPhase] = useState<MeetingPhase>("active");
  const [page, setPage] = useState(1);
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async (target: number, targetPhase: MeetingPhase) => {
    // 이전 요청이 남아 있으면 취소하고 새로 조회
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ kind: "loading" });
    try {
      const data = await listMeetings(target, controller.signal, targetPhase);
      setAvailablePhases(data.availablePhases);
      setState({ kind: "ready", data });
    } catch (error) {
      if (isAbortError(error)) return;
      setState({ kind: "error", message: error instanceof Error && error.message ? error.message : "회의록을 불러오지 못했습니다." });
    }
  }, []);

  useEffect(() => {
    void load(page, phase);
  }, [load, page, phase]);

  function changePhase(next: MeetingPhase) {
    if (next === phase) return;
    setPhase(next);
    setPage(1);
  }

  useEffect(() => () => controllerRef.current?.abort(), []);

  const data = state.kind === "ready" ? state.data : null;
  const lastPage = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1;
  const firstIndex = data && data.items.length > 0 ? (data.page - 1) * data.size + 1 : 0;
  const lastIndex = data ? firstIndex + data.items.length - (data.items.length > 0 ? 1 : 0) : 0;

  return (
    <div className="flex flex-col gap-6 leading-[1.6]">
      <header>
        <h1 title="회의록" className="mn-page-title text-mn-text">회의록</h1>
        <p className="mt-1 text-sm text-mn-muted">등록된 회의록과 업무 현황입니다.</p>
      </header>

      <PhaseTabs phases={phases} current={phase} onChange={changePhase} />

      {state.kind === "loading" ? (
        <p role="status" className="text-sm text-mn-muted">
          회의록을 불러오는 중…
        </p>
      ) : state.kind === "error" ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-4 rounded-mn-card border border-mn-border bg-mn-surface p-6">
          <span className="text-sm">
            <StatusDot tone="error" label={`회의록을 불러오지 못했습니다 · ${state.message}`} />
          </span>
          <Button size="sm" onClick={() => void load(page, phase)}>
            다시 시도
          </Button>
        </div>
      ) : state.data.total === 0 ? (
        <>
          <div role="status" className="rounded-mn-card border border-mn-border bg-mn-surface p-6 text-sm text-mn-muted">
            {EMPTY_TEXT[phase]}
          </div>
          {phase === "active" ? (
            <div>
              <Link
                href="/v2/upload"
                className="mn-focus inline-flex h-10 items-center justify-center rounded-mn-control border border-mn-accent bg-mn-accent px-4 text-sm font-medium text-mn-on-accent hover:bg-mn-accent-hover max-md:h-11"
              >
                첫 회의 올리기
              </Link>
            </div>
          ) : null}
        </>
      ) : (
        <>
          <MeetingsTable items={state.data.items} />
          <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-mn-muted max-md:justify-center">
            <span className="font-mn-mono text-[13px] max-md:hidden">
              {firstIndex}–{lastIndex} / {state.data.total}
            </span>
            <div className="flex items-center gap-3">
              <span aria-live="polite" className="font-mn-mono text-[13px]">
                {state.data.page} / {lastPage} 쪽
              </span>
              <Button size="sm" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))} className="max-md:min-h-11 max-md:min-w-11">
                이전
              </Button>
              <Button size="sm" disabled={page >= lastPage} onClick={() => setPage((p) => p + 1)} className="max-md:min-h-11 max-md:min-w-11">
                다음
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
