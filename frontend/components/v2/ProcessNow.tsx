"use client";

/*
 * [즉시 변환] 버튼(작업 88-2). 서버가 허용 동작 process_now 를 내려준 경우에만 보인다(직급을 화면에서 다시 판단하지 않는다).
 * - 누르면 POST /api/meetings/{id}/process-now. 요청 중에는 버튼이 비활성(중복 클릭 방지).
 * - 응답 waitingForPrior=true 면 서버 message 를 그대로, false 면 "변환을 바로 시작합니다." 를 보이고 onDone 으로 상태를 다시 받는다.
 * - 403·404·409 는 서버가 준 한국어 사유를 그대로 보인다.
 * - 왼쪽 메뉴 처리 현황은 /api/me/processing 항목의 canProcessNow 만 본다(회의록 상세를 따로 조회하지 않는다, 작업 88-3).
 */
import { useEffect, useRef, useState } from "react";

import { Button, StatusDot } from "@/components/mono";
import { isAbortError } from "@/lib/v2/errors";
import { processNowMeeting, processNowMessage } from "@/lib/v2/process-queue";

export function ProcessNow({
  meetingId,
  allowed,
  onDone,
  className = "",
}: {
  meetingId: number;
  /** 서버 허용 동작에 process_now 가 있는가 */
  allowed: boolean;
  /** 요청이 성공한 뒤 상태 다시 받기 */
  onDone?: () => void;
  className?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const busyRef = useRef(false);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []);

  async function run() {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      const result = await processNowMeeting(meetingId, controller.signal);
      setMessage(processNowMessage(result));
      onDone?.();
    } catch (err) {
      if (isAbortError(err)) return;
      setError(err instanceof Error && err.message ? err.message : "즉시 변환을 요청하지 못했습니다.");
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  if (!allowed && message === null && error === null) return null;
  return (
    <span className={`flex flex-col items-start gap-2 ${className}`}>
      {allowed && message === null ? (
        <Button size="sm" disabled={busy} onClick={() => void run()} className="max-md:min-h-11">
          {busy ? "요청 중…" : "즉시 변환"}
        </Button>
      ) : null}
      {message !== null ? (
        <span role="status" className="text-[13px] leading-5 text-mn-text">
          {message}
        </span>
      ) : null}
      {error !== null ? (
        <span role="alert" className="text-[13px] leading-5">
          <StatusDot tone="error" label={error} />
        </span>
      ) : null}
    </span>
  );
}
