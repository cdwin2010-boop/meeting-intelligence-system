"use client";

/*
 * 수정 요청 패널(회의록 상세): 남긴 수정 요청 목록을 보여 준다. 작성은 팝업(ChangeRequestDialog)에서 한다.
 * - GET /api/meetings/{id}/change-requests. 회의록을 볼 수 있으면 누구나 남길 수 있다(판정은 서버)
 * - 수정 요청은 기록일 뿐 회의록·업무 상태와 자동 확정 시계를 바꾸지 않는다. 해결(수락·반려) 동작은 아직 없음
 * - "회의록 전체 수정 요청" 버튼은 onRequestMeeting 으로 팝업을 연다
 * - refreshKey 가 바뀌면(팝업에서 남긴 뒤) 목록을 다시 받고 완료 문구를 보여 준다
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge, Button, StatusDot } from "@/components/mono";
import { formatDateTime } from "@/components/v2/meeting-display";
import { isAbortError } from "@/lib/v2/errors";
import { listChangeRequests, type ActionItem, type ChangeRequest } from "@/lib/v2/meetings";

type ListState = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; requests: ChangeRequest[] };

const errorMessage = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

const DECISION_LABEL: Record<string, string> = { accepted: "수락됨", rejected: "반려됨" };

interface ChangeRequestPanelProps {
  meetingId: number;
  items: ActionItem[];
  /** 팝업에서 수정 요청을 남길 때마다 1씩 늘어난다(0 이면 아직 없음) */
  refreshKey: number;
  onRequestMeeting: () => void;
}

export function ChangeRequestPanel({ meetingId, items, refreshKey, onRequestMeeting }: ChangeRequestPanelProps) {
  const [list, setList] = useState<ListState>({ kind: "loading" });
  const listRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    listRef.current?.abort();
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
    void load();
    return () => listRef.current?.abort();
  }, [load]);

  // 팝업에서 남긴 뒤 목록 새로 받기
  const firstRefresh = useRef(true);
  useEffect(() => {
    if (firstRefresh.current) {
      firstRefresh.current = false;
      return;
    }
    void load();
  }, [refreshKey, load]);

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
      <div aria-live="polite" className="text-sm empty:hidden">
        {refreshKey > 0 ? <StatusDot tone="ready" label="수정 요청을 남겼습니다" /> : null}
      </div>

      {list.kind === "loading" ? (
        <p role="status" className="text-sm text-mn-muted">
          수정 요청을 불러오는 중…
        </p>
      ) : list.kind === "error" ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-sm">
            <StatusDot tone="error" label={`수정 요청을 불러오지 못했습니다 · ${list.message}`} />
          </span>
          <Button size="sm" onClick={() => void load()}>
            다시 시도
          </Button>
        </div>
      ) : list.requests.length === 0 ? (
        <p className="text-sm text-mn-muted">남긴 수정 요청이 없습니다.</p>
      ) : (
        <ol aria-label="수정 요청 목록" className="flex flex-col divide-y divide-mn-border">
          {list.requests.map((req) => (
            <li key={req.requestId} className="flex flex-col gap-1 py-3 text-sm">
              <div className="flex flex-wrap items-center gap-2 text-xs text-mn-muted">
                <span className="text-mn-text">{req.requester?.name ?? "알 수 없음"}</span>
                <span className="font-mn-mono">{formatDateTime(req.createdAt)}</span>
                <Badge>{titleOf(req.itemId)}</Badge>
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
      )}
    </section>
  );
}
