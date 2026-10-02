"use client";

/*
 * 수정 요청 내역(회의록 상세 수정 요청 팝업에서 사용). 작성·해결은 팝업(ChangeRequestDialog)에서 한다.
 * - useChangeRequests: GET /api/meetings/{id}/change-requests 를 회의록마다 한 번 받아 둔다
 *   (팝업을 열 때는 새로 요청하지 않는다. 새 요청을 남긴 뒤에만 reload 로 다시 받고, 해결은 응답으로 그 요청만 바꾼다)
 * - ChangeRequestEntries: 작성자·작성 시각·해결 상태·코멘트, 해결된 요청은 처리자·처리 시각·답변까지 표시
 * - 수정 요청은 기록일 뿐 회의록·업무 상태와 자동 확정 시계를 바꾸지 않는다
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { Badge, Button, StatusDot } from "@/components/mono";
import { formatDateTime } from "@/components/v2/meeting-display";
import { isAbortError } from "@/lib/v2/errors";
import { listChangeRequests, type ChangeRequest } from "@/lib/v2/meetings";

export type ChangeRequestListState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; requests: ChangeRequest[] };

const errorMessage = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

export const DECISION_LABEL: Record<string, string> = { accepted: "수락됨", rejected: "반려됨" };

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

  /** 해결 응답으로 그 요청만 바꾼다(목록을 다시 받지 않음) */
  const replace = useCallback((saved: ChangeRequest) => {
    setList((prev) =>
      prev.kind === "ready"
        ? { kind: "ready", requests: prev.requests.map((req) => (req.requestId === saved.requestId ? saved : req)) }
        : prev,
    );
  }, []);

  return { list, reload, replace };
}

interface ChangeRequestEntriesProps {
  list: ChangeRequestListState;
  /** 목록 이름(접근성) */
  label: string;
  onRetry: () => void;
  /** 대상 배지 글자. 없으면 배지를 그리지 않는다(팝업처럼 대상이 하나로 정해진 경우) */
  targetLabel?: (itemId: number | null) => string;
  /** 요청마다 아래에 붙일 동작(해결 등) */
  renderActions?: (req: ChangeRequest) => ReactNode;
}

/** 수정 요청 항목들: 작성자·작성 시각·(대상)·해결 상태·코멘트, 해결됐으면 처리자·처리 시각·답변 */
export function ChangeRequestEntries({ list, label, onRetry, targetLabel, renderActions }: ChangeRequestEntriesProps) {
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
          {req.resolution ? (
            <div aria-label="처리 정보" className="mt-1 flex flex-col gap-1 border-l border-mn-border pl-3">
              <div className="flex flex-wrap items-center gap-2 text-xs text-mn-muted">
                <span>처리</span>
                <span className="text-mn-text">{req.resolution.resolvedBy?.name ?? "알 수 없음"}</span>
                <span className="font-mn-mono">{formatDateTime(req.resolution.resolvedAt)}</span>
              </div>
              {req.resolution.reason ? <p className="whitespace-pre-wrap leading-6">{req.resolution.reason}</p> : null}
            </div>
          ) : null}
          {renderActions?.(req)}
        </li>
      ))}
    </ol>
  );
}
