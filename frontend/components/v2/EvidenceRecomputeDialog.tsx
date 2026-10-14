"use client";

/*
 * 근거 위치 재계산 팝업(작업 B-1c-1). 허용 동작 edit_held_at 과 같은 조건(관리자·지시자, 잠기지 않은 회의록)에서만 연다.
 * - 열면 먼저 dryRun 으로 대상 건수(재계산 대상·확정 제외)를 보여 주고, 사유(필수)를 받아 실행한다. AI·외부 호출 없음, 전사문 원문 불변.
 * - 결과: 변경 N건 · 근사 N건 · 미변경 N건 · 확정 제외 N건. 서버 거부 문구는 팝업 안에 그대로 보인다.
 */
import { useEffect, useId, useRef, useState } from "react";

import { Modal, StatusDot } from "@/components/mono";
import { isAbortError } from "@/lib/v2/errors";
import { recomputeEvidence, REASON_MAX, type EvidenceRecomputeResult } from "@/lib/v2/meetings";

export function EvidenceRecomputeDialog({
  open,
  meetingId,
  onClose,
  onDone,
}: {
  open: boolean;
  meetingId: number;
  onClose: () => void;
  /** 실행 성공 뒤 화면 갱신 + 결과 메시지 */
  onDone: (result: EvidenceRecomputeResult) => Promise<void> | void;
}) {
  const reasonId = useId();
  const [preview, setPreview] = useState<EvidenceRecomputeResult | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setPreview(null);
    setReason("");
    setError(null);
    setSaving(false);
    setLoading(true);
    recomputeEvidence(meetingId, { dryRun: true }, controller.signal)
      .then((result) => setPreview(result))
      .catch((err) => {
        if (isAbortError(err)) return;
        setError(err instanceof Error && err.message ? err.message : "대상 건수를 불러오지 못했습니다.");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [open, meetingId]);

  async function confirm() {
    if (saving || loading || !preview) return;
    const trimmed = reason.trim();
    if (!trimmed) {
      setError("재계산 사유를 입력해 주세요.");
      return;
    }
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setSaving(true);
    setError(null);
    try {
      const result = await recomputeEvidence(meetingId, { reason: trimmed }, controller.signal);
      await onDone(result);
      onClose();
    } catch (err) {
      if (isAbortError(err)) return;
      setError(err instanceof Error && err.message ? err.message : "근거 위치를 다시 계산하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => (saving ? undefined : onClose())}
      title="근거 위치 재계산"
      description="저장된 전사문과 업무별 근거 인용문·시각으로 근거 위치를 다시 계산합니다. AI·외부 호출은 없고 전사문 원문은 바뀌지 않으며, 변경 내용은 이력에 남습니다."
      confirmLabel="근거 위치 재계산"
      cancelLabel="취소"
      confirmLoading={saving}
      onConfirm={() => void confirm()}
    >
      <div className="mt-3 flex flex-col gap-3">
        {loading ? (
          <p role="status" className="text-sm text-mn-muted">
            대상 건수를 확인하는 중…
          </p>
        ) : preview ? (
          <p role="status" data-testid="recompute-preview" className="text-sm">
            재계산 대상 {preview.targets}건 · 확정 제외 {preview.excluded}건
          </p>
        ) : null}
        <div className="flex flex-col gap-1">
          <label htmlFor={reasonId} className="text-sm font-medium">
            재계산 사유(필수)
          </label>
          <textarea
            id={reasonId}
            value={reason}
            maxLength={REASON_MAX}
            disabled={saving || loading || !preview}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            className="mn-focus w-full rounded-mn-control border border-mn-control bg-mn-bg px-3 py-2 text-sm text-mn-text outline-none disabled:opacity-50"
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
