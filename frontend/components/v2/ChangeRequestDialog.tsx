"use client";

/*
 * 수정 요청 작성 팝업(회의록 상세). 업무 행의 "수정 요청" 또는 회의록 전체 수정 요청 버튼으로 연다.
 * - 대상은 연 버튼이 정한다(업무 하나 또는 회의록 전체). 팝업 안에서는 바꾸지 않는다
 * - POST /api/meetings/{id}/change-requests. 성공하면 onCreated 를 부르고 닫힌다(목록 갱신은 화면 아래 패널)
 * - 실패(400·403·422·서버 오류)는 팝업 안에 서버 문구를 보여 주고 입력값은 그대로 둔다
 * - 취소에 먼저 포커스, Esc·배경 클릭으로 닫기, 닫히면 연 버튼으로 포커스 복귀(Modal 이 처리)
 */
import { useEffect, useId, useRef, useState } from "react";

import { Modal, StatusDot } from "@/components/mono";
import { isAbortError } from "@/lib/v2/errors";
import { CHANGE_REQUEST_MAX, createChangeRequest } from "@/lib/v2/meetings";

const errorMessage = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

export interface ChangeRequestTarget {
  /** null 이면 회의록 전체 */
  itemId: number | null;
  label: string;
}

interface ChangeRequestDialogProps {
  meetingId: number;
  /** 대상. null 이면 닫힘 */
  target: ChangeRequestTarget | null;
  onClose: () => void;
  onCreated: () => void;
}

export function ChangeRequestDialog({ meetingId, target, onClose, onCreated }: ChangeRequestDialogProps) {
  const commentId = useId();
  const [comment, setComment] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const saveRef = useRef<AbortController | null>(null);

  // 열 때마다 입력을 비우고, 닫히면 진행 중인 요청을 취소한다
  const open = target !== null;
  useEffect(() => {
    if (!open) return;
    setComment("");
    setError(null);
    setSaving(false);
    return () => saveRef.current?.abort();
  }, [open, target?.itemId]);

  async function onConfirm() {
    if (!target || saving) return;
    const text = comment.trim();
    if (!text) {
      setError("코멘트를 입력하세요.");
      return;
    }
    saveRef.current?.abort();
    const controller = new AbortController();
    saveRef.current = controller;
    setSaving(true);
    setError(null);
    try {
      await createChangeRequest(meetingId, { comment: text, itemId: target.itemId }, controller.signal);
      setSaving(false);
      onCreated();
    } catch (err) {
      if (isAbortError(err)) return;
      // 입력값은 그대로 두고 서버 문구를 보여 준다
      setError(`수정 요청을 남기지 못했습니다 · ${errorMessage(err, "서버 오류")}`);
      setSaving(false);
    }
  }

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
      cancelLabel="취소"
      initialFocus="cancel"
      onConfirm={() => void onConfirm()}
      confirmLoading={saving}
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
              setError(null);
              setComment(event.target.value);
            }}
            className="mn-focus w-full rounded-mn-control border border-mn-border bg-mn-bg px-3 py-2 text-sm text-mn-text outline-none placeholder:text-mn-muted disabled:opacity-50"
          />
        </div>
        {error ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={error} />
          </p>
        ) : null}
      </div>
    </Modal>
  );
}
