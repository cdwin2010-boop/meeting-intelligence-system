"use client";

/*
 * 수정 요청 패널(회의록 상세): 회의록 전체 또는 업무 하나를 골라 코멘트를 남기고, 남긴 요청 목록을 보여 준다.
 * - GET·POST /api/meetings/{id}/change-requests. 회의록을 볼 수 있으면 누구나 남길 수 있다(판정은 서버)
 * - 수정 요청은 기록일 뿐 회의록·업무 상태와 자동 확정 시계를 바꾸지 않는다
 * - 작성 실패(400·403·422·서버 오류)는 서버 문구를 보여 주고 입력값은 그대로 둔다. 해결(수락·반려) 동작은 아직 없음
 * - 업무 원장의 "수정 요청" 버튼이 target 을 넘기면 그 업무를 대상으로 골라 두고 입력칸으로 포커스를 옮긴다
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { Badge, Button, StatusDot } from "@/components/mono";
import { formatDateTime } from "@/components/v2/meeting-display";
import { isAbortError } from "@/lib/v2/errors";
import {
  CHANGE_REQUEST_MAX,
  createChangeRequest,
  listChangeRequests,
  type ActionItem,
  type ChangeRequest,
} from "@/lib/v2/meetings";

type ListState = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; requests: ChangeRequest[] };

type Notice = { tone: "ready" | "error"; text: string } | null;

const fieldClass =
  "mn-focus w-full rounded-mn-control border border-mn-border bg-mn-bg px-3 text-sm text-mn-text outline-none disabled:opacity-50";

const errorMessage = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

const DECISION_LABEL: Record<string, string> = { accepted: "수락됨", rejected: "반려됨" };

interface ChangeRequestPanelProps {
  meetingId: number;
  items: ActionItem[];
  /** 업무 원장에서 고른 대상(nonce 가 바뀔 때마다 다시 적용) */
  target: { itemId: number; nonce: number } | null;
}

export function ChangeRequestPanel({ meetingId, items, target }: ChangeRequestPanelProps) {
  const baseId = useId();
  const [list, setList] = useState<ListState>({ kind: "loading" });
  const [itemId, setItemId] = useState("");
  const [comment, setComment] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const listRef = useRef<AbortController | null>(null);
  const saveRef = useRef<AbortController | null>(null);
  const commentRef = useRef<HTMLTextAreaElement>(null);

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
    return () => {
      listRef.current?.abort();
      saveRef.current?.abort();
    };
  }, [load]);

  // 업무 원장에서 "수정 요청"을 누르면 그 업무를 대상으로 고르고 입력칸으로 이동
  useEffect(() => {
    if (!target) return;
    setItemId(String(target.itemId));
    setNotice(null);
    commentRef.current?.focus();
  }, [target]);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (saving) return;
    const text = comment.trim();
    if (!text) {
      setNotice({ tone: "error", text: "코멘트를 입력하세요." });
      return;
    }
    saveRef.current?.abort();
    const controller = new AbortController();
    saveRef.current = controller;
    setSaving(true);
    setNotice(null);
    try {
      await createChangeRequest(meetingId, { comment: text, itemId: itemId ? Number(itemId) : null }, controller.signal);
      setComment("");
      setItemId("");
      setNotice({ tone: "ready", text: "수정 요청을 남겼습니다" });
      void load();
    } catch (error) {
      if (isAbortError(error)) return;
      // 입력값은 그대로 둔다
      setNotice({ tone: "error", text: `수정 요청을 남기지 못했습니다 · ${errorMessage(error, "서버 오류")}` });
    } finally {
      setSaving(false);
    }
  }

  const titleOf = (id: number | null) => {
    if (id === null) return "회의록 전체";
    const found = items.find((it) => it.id === id);
    return found ? found.title || "(업무명 없음)" : `업무 ${id}`;
  };

  return (
    <section aria-label="수정 요청" className="flex flex-col gap-4 rounded-mn-card border border-mn-border bg-mn-surface p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold tracking-tight">수정 요청</h2>
        <span className="text-xs text-mn-muted">수정 요청은 기록만 남기며 확정 일정은 멈추지 않습니다.</span>
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

      <form onSubmit={(event) => void onSubmit(event)} className="flex flex-col gap-3 border-t border-mn-border pt-4">
        <div className="grid gap-3 sm:grid-cols-[240px_minmax(0,1fr)]">
          <div className="flex flex-col gap-2">
            <label htmlFor={`${baseId}-target`} className="text-sm font-medium">
              대상
            </label>
            <select
              id={`${baseId}-target`}
              value={itemId}
              disabled={saving}
              onChange={(event) => {
                setNotice(null);
                setItemId(event.target.value);
              }}
              className={`${fieldClass} h-10`}
            >
              <option value="">회의록 전체</option>
              {items.map((it) => (
                <option key={it.id} value={String(it.id)}>
                  {it.title || "(업무명 없음)"}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-2">
            <label htmlFor={`${baseId}-comment`} className="text-sm font-medium">
              코멘트
            </label>
            <textarea
              id={`${baseId}-comment`}
              ref={commentRef}
              value={comment}
              maxLength={CHANGE_REQUEST_MAX}
              rows={3}
              disabled={saving}
              placeholder="고쳐야 할 내용을 적어 주세요"
              onChange={(event) => {
                setNotice(null);
                setComment(event.target.value);
              }}
              className={`${fieldClass} py-2 placeholder:text-mn-muted`}
            />
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div aria-live="polite" className="text-sm">
            {notice ? (
              notice.tone === "error" ? (
                <span role="alert">
                  <StatusDot tone="error" label={notice.text} />
                </span>
              ) : (
                <StatusDot tone="ready" label={notice.text} />
              )
            ) : null}
          </div>
          <Button type="submit" disabled={saving}>
            {saving ? "보내는 중…" : "수정 요청 남기기"}
          </Button>
        </div>
      </form>
    </section>
  );
}
