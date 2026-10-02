"use client";

/*
 * 수정 요청 목록(회의록 상세). 작성은 팝업(ChangeRequestDialog)에서 한다.
 * - useChangeRequests: GET /api/meetings/{id}/change-requests 를 회의록마다 한 번 받아 화면 아래 패널과 팝업 내역이 함께 쓴다
 *   (팝업을 열 때는 새로 요청하지 않는다. 새 요청을 남긴 뒤에만 reload 로 다시 받는다)
 * - 수정 요청은 기록일 뿐 회의록·업무 상태와 자동 확정 시계를 바꾸지 않는다. 해결(수락·반려) 동작은 아직 없음
 * - "회의록 전체 수정 요청" 버튼은 onRequestMeeting 으로 팝업을 연다
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge, Button, StatusDot } from "@/components/mono";
import { formatDateTime } from "@/components/v2/meeting-display";
import { isAbortError } from "@/lib/v2/errors";
import { listChangeRequests, type ActionItem, type ChangeRequest } from "@/lib/v2/meetings";

export type ChangeRequestListState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; requests: ChangeRequest[] };

const errorMessage = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

const DECISION_LABEL: Record<string, string> = { accepted: "수락됨", rejected: "반려됨" };

/** 회의록의 수정 요청 목록. meetingId 가 바뀌면 이전 요청을 취소하고 새로 받는다 */
export function useChangeRequests(meetingId: number | null) {
  const [list, setList] = useState<ChangeRequestListState>({ kind: "loading" });
  const listRef = useRef<AbortController | null>(null);

  const reload = useCallback(async () => {
    listRef.current?.abort();
    if (meetingId === null) return;
    const controller = new AbortController();
    listRef.current = controller;
    setList((prev) => (prev.kind === "ready" ? prev : { kind: "loading" }));
    try {
      setList({ kind: "ready", requests: await listChangeRequests(meetingId, controller.signal) });
    } catch (error) {
      if (isAbortError(error)) return;
      setList({ kind: "error", message: errorMessage(error, "수정 요청을 불러오지 못했습니다.") });
    }
  }, [meetingId]);

  useEffect(() => {
    setList({ kind: "loading" });
    void reload();
    return () => listRef.current?.abort();
  }, [reload]);

  return { list, reload };
}

interface ChangeRequestEntriesProps {
  list: ChangeRequestListState;
  /** 목록 이름(접근성) */
  label: string;
  onRetry: () => void;
  /** 대상 배지 글자. 없으면 배지를 그리지 않는다(팝업처럼 대상이 하나로 정해진 경우) */
  targetLabel?: (itemId: number | null) => string;
}

/** 수정 요청 항목들: 작성자·작성 시각·(대상)·해결 상태·코멘트. 패널과 팝업 내역이 같은 표시 규칙을 쓴다 */
export function ChangeRequestEntries({ list, label, onRetry, targetLabel }: ChangeRequestEntriesProps) {
  if (list.kind === "loading") {
    return (
      <p role="status" className="text-sm text-mn-muted">
        수정 요청을 불러오는 중…
      </p>
    );
  }
  if (list.kind === "error") {
    return (
      <div role="alert" className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-sm">
          <StatusDot tone="error" label={`수정 요청을 불러오지 못했습니다 · ${list.message}`} />
        </span>
        <Button size="sm" onClick={onRetry}>
          다시 시도
        </Button>
      </div>
    );
  }
  if (list.requests.length === 0) return <p className="text-sm text-mn-muted">남긴 수정 요청이 없습니다.</p>;
  return (
    <ol aria-label={label} className="flex flex-col divide-y divide-mn-border">
      {list.requests.map((req) => (
        <li key={req.requestId} className="flex flex-col gap-1 py-3 text-sm">
          <div className="flex flex-wrap items-center gap-2 text-xs text-mn-muted">
            <span className="text-mn-text">{req.requester?.name ?? "알 수 없음"}</span>
            <span className="font-mn-mono">{formatDateTime(req.createdAt)}</span>
            {targetLabel ? <Badge>{targetLabel(req.itemId)}</Badge> : null}
            {req.resolution ? (
              <Badge>{DECISION_LABEL[req.resolution.decision] ?? req.resolution.decision}</Badge>
            ) : (
              <Badge>해결 대기</Badge>
            )}
          </div>
          <p className="whitespace-pre-wrap leading-6">{req.comment}</p>
        </li>
      ))}
    </ol>
  );
}

interface ChangeRequestPanelProps {
  list: ChangeRequestListState;
  items: ActionItem[];
  onRetry: () => void;
  onRequestMeeting: () => void;
}

export function ChangeRequestPanel({ list, items, onRetry, onRequestMeeting }: ChangeRequestPanelProps) {
  const titleOf = (id: number | null) => {
    if (id === null) return "회의록 전체";
    const found = items.find((it) => it.id === id);
    return found ? found.title || "(업무명 없음)" : `업무 ${id}`;
  };

  return (
    <section aria-label="수정 요청" className="flex flex-col gap-4 rounded-mn-card border border-mn-border bg-mn-surface p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold tracking-tight">수정 요청</h2>
        <span className="flex flex-wrap items-center gap-3">
          <span className="text-xs text-mn-muted">수정 요청은 기록만 남기며 확정 일정은 멈추지 않습니다.</span>
          <Button size="sm" onClick={onRequestMeeting}>
            회의록 전체 수정 요청
          </Button>
        </span>
      </div>
      <ChangeRequestEntries list={list} label="수정 요청 목록" onRetry={onRetry} targetLabel={titleOf} />
    </section>
  );
}
