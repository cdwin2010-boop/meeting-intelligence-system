"use client";

/*
 * 처리 엔진 안내: 서버가 fake(시험용 가짜 처리)로 돌고 있으면 글자로 알린다(색만으로 구분하지 않음).
 * 조회는 화면을 연 뒤 한 번만 하고, 실패하거나 gemini 이면 아무것도 보이지 않는다.
 */
import { useEffect, useState } from "react";

import { StatusDot } from "@/components/mono";
import { isAbortError } from "@/lib/v2/errors";
import { FAKE_ENGINE_NOTICE, getEngine } from "@/lib/v2/system";

export function useEngineIsFake(enabled: boolean): boolean {
  const [isFake, setIsFake] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    getEngine(controller.signal)
      .then((info) => setIsFake(info.isFake))
      .catch((error) => {
        if (!isAbortError(error)) setIsFake(false);
      });
    return () => controller.abort();
  }, [enabled]);
  return isFake;
}

export function FakeEngineNotice({ show, label }: { show: boolean; label?: string }) {
  if (!show) return null;
  return (
    <p role="status" aria-label={label ?? "처리 엔진 안내"} className="rounded-mn-card border border-mn-border bg-mn-surface px-4 py-3 text-sm">
      <StatusDot tone="queued" label={FAKE_ENGINE_NOTICE} />
    </p>
  );
}
