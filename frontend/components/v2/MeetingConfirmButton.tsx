"use client";

/*
 * 회의록 확정 버튼 + 확인 대화상자(회의록 상세).
 * - POST /api/meetings/{id}/confirm 은 회의록만 확정한다(업무 확정은 업무 원장에서 따로)
 * - 권한은 서버가 판정한다(상세 응답에 권한 값이 없어 버튼은 모두에게 보이고, 403·409 는 서버 문구를 대화상자에 표시)
 * - 확인 대화상자는 취소 버튼에 먼저 포커스가 가고(본문에 포커스 요소 없음), 확인 버튼은 구체적 동작 이름("회의록 확정")
 */
import { useEffect, useRef, useState } from "react";

import { Button, Modal, StatusDot } from "@/components/mono";
import { isAbortError } from "@/lib/v2/errors";
import { confirmMeeting, type MeetingConfirmResult } from "@/lib/v2/meetings";

interface MeetingConfirmButtonProps {
  meetingId: number;
  title: string;
  onConfirmed: (result: MeetingConfirmResult) => void;
}

export function MeetingConfirmButton({ meetingId, title, onConfirmed }: MeetingConfirmButtonProps) {
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []);

  function close() {
    controllerRef.current?.abort();
    setSaving(false);
    setOpen(false);
  }

  async function onConfirm() {
    if (saving) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setSaving(true);
    setError(null);
    try {
      const result = await confirmMeeting(meetingId, controller.signal);
      setSaving(false);
      setOpen(false);
      onConfirmed(result);
    } catch (err) {
      if (isAbortError(err)) return;
      setError(err instanceof Error && err.message ? err.message : "확정하지 못했습니다.");
      setSaving(false);
    }
  }

  return (
    <>
      <Button
        variant="primary"
        onClick={() => {
          setError(null);
          setOpen(true);
        }}
      >
        회의록 확정
      </Button>
      <Modal
        open={open}
        onClose={close}
        title="회의록을 확정할까요?"
        description={
          <>
            <span className="text-mn-text">{title || "(제목 없음)"}</span>
            <span> · 회의록만 확정합니다. 업무는 업무 원장에서 하나씩 확정하세요. 확정 후에는 되돌릴 수 없습니다.</span>
          </>
        }
        confirmLabel="회의록 확정"
        cancelLabel="취소"
        onConfirm={() => void onConfirm()}
        confirmLoading={saving}
      >
        {error ? (
          <p role="alert" className="mt-3 text-sm">
            <StatusDot tone="error" label={`확정하지 못했습니다 · ${error}`} />
          </p>
        ) : null}
      </Modal>
    </>
  );
}
