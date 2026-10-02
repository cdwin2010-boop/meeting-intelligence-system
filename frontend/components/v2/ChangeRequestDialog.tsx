"use client";

/*
 * 수정 요청 작성 팝업(회의록 상세). 업무 행의 "수정 요청" 또는 회의록 전체 수정 요청 버튼으로 연다.
 * - 대상은 연 버튼이 정한다(업무 하나 또는 회의록 전체). 팝업 안에서는 바꾸지 않는다
 * - POST /api/meetings/{id}/change-requests. 성공하면 입력을 비우고 onCreated(목록 다시 받기)를 기다린다. 팝업은 닫지 않는다
 * - 하단 "수정 요청 내역"은 화면이 이미 받은 목록(list)에서 이 대상의 요청만 골라 보여 준다(열 때 따로 요청하지 않음)
 * - 실패(400·403·422·서버 오류)는 팝업 안에 서버 문구를 보여 주고 입력값은 그대로 둔다
 * - 닫기에 먼저 포커스, Esc·배경 클릭으로 닫기, 닫히면 연 버튼으로 포커스 복귀(Modal 이 처리)
 */
import { useEffect, useId, useRef, useState } from "react";

import { Modal, StatusDot } from "@/components/mono";
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
  const saveRef = useRef<AbortController | null>(null);

  // 열 때마다 입력을 비우고, 닫히면 진행 중인 요청을 취소한다
  const open = target !== null;
  const targetItemId = target?.itemId;
  useEffect(() => {
    if (!open) return;
    setComment("");
    setNotice(null);
    setSaving(false);
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
  }

  // 이 대상에 남긴 요청만(목록이 아직 없거나 실패면 그 상태 그대로)
  const history: ChangeRequestListState =
    list.kind === "ready" && target
      ? { kind: "ready", requests: list.requests.filter((req) => req.itemId === target.itemId) }
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
        </section>
      </div>
    </Modal>
  );
}
