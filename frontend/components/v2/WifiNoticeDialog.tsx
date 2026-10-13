"use client";

/*
 * 모바일 올리기 직전 Wi-Fi 안내 팝업(작업 88-2). 판정은 lib/v2/network.ts, 이 컴포넌트는 안내와 두 가지 선택만 담당한다.
 * - [업로드 계속]: 바로 올린다. [등록 보류]: 올리지 않고 기기 보류 목록에 저장한다(보류 처리는 호출부 onDefer).
 * - 처음 포커스는 [등록 보류](요금이 들지 않는 쪽). Esc·배경 클릭은 공용 Modal 규칙대로 팝업만 닫는다(올리지도 보류하지도 않고 입력 화면으로 돌아감).
 * - 이 기기에서 보류 저장소를 쓸 수 없으면 [등록 보류]를 막고 안내를 보인다. 저장 실패·한도 초과 사유는 팝업 안에 보인다.
 */
import { useEffect, useState } from "react";

import { Button, Modal, StatusDot } from "@/components/mono";
import { isPendingStorageAvailable, PENDING_UNAVAILABLE_MESSAGE } from "@/lib/v2/pending-uploads";

export const WIFI_NOTICE_TEXT = "Wi-Fi 환경에서 등록하셔야 통신 요금이 발생하지 않습니다. 지금 바로 업로드하시겠습니까?";

export function WifiNoticeDialog({
  open,
  onContinue,
  onDefer,
  onClose,
  error,
  deferring,
  alreadyPending = false,
}: {
  open: boolean;
  onContinue: () => void;
  onDefer: () => void;
  onClose: () => void;
  /** 보류 저장 실패·한도 초과 사유 */
  error: string | null;
  /** 보류 저장 진행 중 */
  deferring: boolean;
  /** 이미 보류 목록에 있는 항목을 다시 올리는 중(보류를 누르면 목록에 그대로 둔다) */
  alreadyPending?: boolean;
}) {
  const [storage, setStorage] = useState<"checking" | "ok" | "unavailable">("checking");

  useEffect(() => {
    if (!open) return;
    if (alreadyPending) {
      setStorage("ok");
      return;
    }
    let cancelled = false;
    setStorage("checking");
    void isPendingStorageAvailable().then((ok) => {
      if (!cancelled) setStorage(ok ? "ok" : "unavailable");
    });
    return () => {
      cancelled = true;
    };
  }, [open, alreadyPending]);

  return (
    <Modal open={open} onClose={onClose} title="Wi-Fi 연결 확인" description={WIFI_NOTICE_TEXT}>
      <div className="mt-2 flex flex-col gap-3">
        {storage === "unavailable" ? (
          <p role="status" className="text-sm text-mn-text">
            {PENDING_UNAVAILABLE_MESSAGE}
          </p>
        ) : null}
        {error ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={error} />
          </p>
        ) : null}
        <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
          {/* 첫 번째 버튼이 처음 포커스를 받는다 */}
          <Button onClick={onDefer} disabled={storage === "unavailable" || deferring} className="max-md:min-h-11">
            {deferring ? "보류 저장 중…" : "등록 보류"}
          </Button>
          <Button variant="primary" onClick={onContinue} disabled={deferring} className="max-md:min-h-11">
            업로드 계속
          </Button>
        </div>
      </div>
    </Modal>
  );
}
