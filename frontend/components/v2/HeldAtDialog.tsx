"use client";

/*
 * 회의 일시 변경 팝업(작업 88-4). 서버가 허용 동작 edit_held_at 을 내려준 사용자(관리자·지시자)만 연다.
 * - 새 회의 날짜·시각(필수, 현재보다 미래 불가)과 변경 사유(필수)를 받아 PATCH /api/meetings/{id}/held-at 로 보낸다. 변경 이력은 서버가 남긴다.
 * - 이미 계산된 업무 기한과 등록 일시는 바뀌지 않는다(안내 문구).
 * - 서버 거부(403·404·409·422)는 서버 문구를 팝업 안에 그대로 보인다. 취소·Esc 는 변경 없이 닫는다.
 */
import { useEffect, useId, useRef, useState } from "react";

import { Modal, StatusDot } from "@/components/mono";
import { formatDateTime } from "@/components/v2/meeting-display";
import { isAbortError } from "@/lib/v2/errors";
import {
  isFutureMeetingAt,
  MEETING_AT_FUTURE_MESSAGE,
  MEETING_AT_REQUIRED_MESSAGE,
  partsFromTimestamp,
  toOffsetIso,
} from "@/lib/v2/meeting-at";
import { changeMeetingHeldAt, REASON_MAX } from "@/lib/v2/meetings";

const inputClass =
  "mn-focus h-10 w-full rounded-mn-control border border-mn-control bg-mn-bg px-3 text-sm text-mn-text outline-none disabled:opacity-50";

export function HeldAtDialog({
  open,
  meetingId,
  currentHeldAt,
  onClose,
  onChanged,
}: {
  open: boolean;
  meetingId: number;
  /** 지금 저장된 회의 일시(ISO) */
  currentHeldAt: string;
  onClose: () => void;
  /** 변경 성공 뒤 화면 갱신 */
  onChanged: () => Promise<void> | void;
}) {
  const dateId = useId();
  const timeId = useId();
  const reasonId = useId();
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  // 열 때마다 현재 값으로 채우고 입력을 비운다. 닫히면 진행 중인 요청을 취소한다
  useEffect(() => {
    if (!open) return;
    const ms = new Date(currentHeldAt).getTime();
    const parts = Number.isFinite(ms) ? partsFromTimestamp(ms) : { date: "", time: "" };
    setDate(parts.date);
    setTime(parts.time);
    setReason("");
    setError(null);
    setSaving(false);
    return () => controllerRef.current?.abort();
  }, [open, currentHeldAt]);

  async function confirm() {
    if (saving) return;
    const iso = toOffsetIso(date, time);
    if (!iso) {
      setError(MEETING_AT_REQUIRED_MESSAGE);
      return;
    }
    if (isFutureMeetingAt(iso)) {
      setError(MEETING_AT_FUTURE_MESSAGE);
      return;
    }
    const trimmed = reason.trim();
    if (!trimmed) {
      setError("변경 사유를 입력해 주세요.");
      return;
    }
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setSaving(true);
    setError(null);
    try {
      await changeMeetingHeldAt(meetingId, iso, trimmed, controller.signal);
      await onChanged();
      onClose();
    } catch (err) {
      if (isAbortError(err)) return;
      setError(err instanceof Error && err.message ? err.message : "회의 일시를 변경하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => (saving ? undefined : onClose())}
      title="회의 일시 변경"
      description={`현재 회의 일시: ${formatDateTime(currentHeldAt)}. 이미 계산된 업무 기한과 등록 일시는 바뀌지 않으며, 변경 내용은 이력에 남습니다.`}
      confirmLabel="회의 일시 변경"
      cancelLabel="취소"
      confirmLoading={saving}
      onConfirm={() => void confirm()}
    >
      <div className="mt-3 flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={dateId} className="sr-only">
            새 회의 날짜
          </label>
          <input id={dateId} type="date" value={date} required disabled={saving} onChange={(e) => setDate(e.target.value)} className={`${inputClass} w-[160px] font-mn-mono`} />
          <label htmlFor={timeId} className="sr-only">
            새 회의 시각
          </label>
          <input id={timeId} type="time" value={time} required disabled={saving} onChange={(e) => setTime(e.target.value)} className={`${inputClass} w-[120px] font-mn-mono`} />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={reasonId} className="text-sm font-medium">
            변경 사유 (필수)
          </label>
          <textarea
            id={reasonId}
            value={reason}
            maxLength={REASON_MAX}
            rows={3}
            disabled={saving}
            onChange={(e) => setReason(e.target.value)}
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
