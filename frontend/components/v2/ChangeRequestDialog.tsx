"use client";

/*
 * 수정 요청 작성 팝업(회의록 상세). 업무 행의 "수정 요청" 또는 회의록 전체 수정 요청 버튼으로 연다.
 * - 대상은 연 버튼이 정한다(업무 하나 또는 회의록 전체). 팝업 안에서는 바꾸지 않는다
 * - POST /api/meetings/{id}/change-requests. 성공하면 입력을 비우고 onCreated(목록 다시 받기)를 기다린다. 팝업은 닫지 않는다
 * - 하단 "수정 요청 내역"은 화면이 이미 받은 목록(list)에서 이 대상의 요청만 골라 보여 준다(열 때 따로 요청하지 않음)
 *   GET 이 쪽 인자·전체 건수를 지원하지 않아 화면에서 10건씩 나눈다. 열 때는 첫 쪽, 새 요청을 남기면 마지막 쪽(작성 순이라 새 요청이 끝)
 * - 실패(400·403·422·서버 오류)는 팝업 안에 서버 문구를 보여 주고 입력값은 그대로 둔다
 * - 닫기에 먼저 포커스, Esc·배경 클릭으로 닫기, 닫히면 연 버튼으로 포커스 복귀(Modal 이 처리)
 */
import { useEffect, useId, useRef, useState } from "react";

import { Button, Modal, StatusDot } from "@/components/mono";
import { ChangeRequestEntries, type ChangeRequestListState } from "@/components/v2/ChangeRequestPanel";
import { isAbortError } from "@/lib/v2/errors";
import { CHANGE_REQUEST_MAX, createChangeRequest } from "@/lib/v2/meetings";

const errorMessage = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

export interface ChangeRequestTarget {
  /** null 이면 회의록 전체 */
  itemId: number | null;
  label: string;
}

type Notice = { tone: "ready" | "error"; text: string } | null;

/** 팝업 내역 한 쪽 건수 */
const HISTORY_PAGE_SIZE = 10;

interface ChangeRequestDialogProps {
  meetingId: number;
  /** 대상. null 이면 닫힘 */
  target: ChangeRequestTarget | null;
  /** 화면이 받아 둔 회의록 전체 수정 요청 목록 */
  list: ChangeRequestListState;
  onRetryList: () => void;
  onClose: () => void;
  /** 새 요청을 남긴 뒤 부른다(목록 다시 받기) */
  onCreated: () => Promise<void>;
}

export function ChangeRequestDialog({ meetingId, target, list, onRetryList, onClose, onCreated }: ChangeRequestDialogProps) {
  const commentId = useId();
  const [comment, setComment] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [page, setPage] = useState(1);
  const saveRef = useRef<AbortController | null>(null);

  // 열 때마다 입력을 비우고, 닫히면 진행 중인 요청을 취소한다
  const open = target !== null;
  const targetItemId = target?.itemId;
  useEffect(() => {
    if (!open) return;
    setComment("");
    setNotice(null);
    setSaving(false);
    setPage(1);
    return () => saveRef.current?.abort();
  }, [open, targetItemId]);

  async function onConfirm() {
    if (!target || saving) return;
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
      await createChangeRequest(meetingId, { comment: text, itemId: target.itemId }, controller.signal);
    } catch (err) {
      if (isAbortError(err)) return;
      // 입력값은 그대로 두고 서버 문구를 보여 준다
      setNotice({ tone: "error", text: `수정 요청을 남기지 못했습니다 · ${errorMessage(err, "서버 오류")}` });
      setSaving(false);
      return;
    }
    setComment("");
    setNotice({ tone: "ready", text: "수정 요청을 남겼습니다" });
    setSaving(false);
    await onCreated();
    // 새 요청이 있는 마지막 쪽으로(실제 쪽 번호는 아래에서 마지막 쪽으로 맞춘다)
    setPage(Number.MAX_SAFE_INTEGER);
  }

  // 이 대상에 남긴 요청만(목록이 아직 없거나 실패면 그 상태 그대로), 정렬은 받은 순서 그대로
  const targetRequests = list.kind === "ready" && target ? list.requests.filter((req) => req.itemId === target.itemId) : [];
  const total = targetRequests.length;
  const lastPage = Math.max(1, Math.ceil(total / HISTORY_PAGE_SIZE));
  const current = Math.min(Math.max(1, page), lastPage);
  const firstIndex = (current - 1) * HISTORY_PAGE_SIZE;
  const history: ChangeRequestListState =
    list.kind === "ready" && target
      ? { kind: "ready", requests: targetRequests.slice(firstIndex, firstIndex + HISTORY_PAGE_SIZE) }
      : list;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="수정 요청"
      description={
        <>
          <span>대상 </span>
          <span className="text-mn-text">{target?.label}</span>
          <span> · 기록만 남기며 확정 일정은 멈추지 않습니다.</span>
        </>
      }
      confirmLabel="수정 요청 남기기"
      cancelLabel="닫기"
      initialFocus="cancel"
      onConfirm={() => void onConfirm()}
      confirmLoading={saving}
      panelClassName="w-full max-w-lg max-h-[calc(100dvh-2rem)] overflow-y-auto"
    >
      <div className="mt-3 flex flex-col gap-3">
        <div className="flex flex-col gap-2">
          <label htmlFor={commentId} className="text-sm font-medium">
            코멘트
          </label>
          <textarea
            id={commentId}
            value={comment}
            maxLength={CHANGE_REQUEST_MAX}
            rows={4}
            disabled={saving}
            placeholder="고쳐야 할 내용을 적어 주세요"
            onChange={(event) => {
              setNotice(null);
              setComment(event.target.value);
            }}
            className="mn-focus w-full rounded-mn-control border border-mn-border bg-mn-bg px-3 py-2 text-sm text-mn-text outline-none placeholder:text-mn-muted disabled:opacity-50"
          />
        </div>
        <div aria-live="polite" className="text-sm empty:hidden">
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
        <section aria-label="수정 요청 내역" className="flex flex-col gap-2 border-t border-mn-border pt-3">
          <h3 className="text-sm font-medium">수정 요청 내역</h3>
          {/* 내역이 길면 이 영역만 스크롤(팝업은 화면 높이를 넘지 않음) */}
          <div className="max-h-[min(40dvh,320px)] overflow-y-auto">
            <ChangeRequestEntries list={history} label="이 대상의 수정 요청 내역" onRetry={onRetryList} />
          </div>
          {total > HISTORY_PAGE_SIZE ? (
            <nav aria-label="수정 요청 내역 쪽" className="flex flex-wrap items-center justify-between gap-3 text-sm text-mn-muted">
              <span className="font-mn-mono text-[13px]">
                {firstIndex + 1}–{Math.min(firstIndex + HISTORY_PAGE_SIZE, total)} / {total}
              </span>
              <span className="flex flex-wrap items-center gap-2">
                <Button size="sm" disabled={current <= 1} onClick={() => setPage(current - 1)}>
                  이전
                </Button>
                {/* 현재 쪽은 누를 수 없는 표시(흰 테두리·굵게), 나머지는 쪽 이동 버튼. 확인 버튼이 유일한 primary 라 채움은 쓰지 않는다 */}
                {Array.from({ length: lastPage }, (_, index) => index + 1).map((n) =>
                  n === current ? (
                    <span
                      key={n}
                      aria-current="page"
                      aria-label={`${n}쪽`}
                      className="inline-flex h-8 min-w-8 items-center justify-center rounded-mn-control border border-mn-text px-3 font-mn-mono text-[13px] font-semibold text-mn-text"
                    >
                      {n}
                    </span>
                  ) : (
                    <Button key={n} size="sm" aria-label={`${n}쪽`} onClick={() => setPage(n)} className="font-mn-mono">
                      {n}
                    </Button>
                  ),
                )}
                <Button size="sm" disabled={current >= lastPage} onClick={() => setPage(current + 1)}>
                  다음
                </Button>
              </span>
            </nav>
          ) : null}
        </section>
      </div>
    </Modal>
  );
}
