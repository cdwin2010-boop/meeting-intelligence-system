"use client";

/*
 * 헤더 오른쪽 처리 현황 요약 뱃지(모바일 768 미만에서만 그린다). ProcessingProvider 의 기존 상태를 쓰고 새 폴링을 만들지 않는다.
 * - 처리 중(queued·running)이 있으면 "처리 중 N건", 완료나 실패가 있으면 "완료 N · 실패 N"(실패 = 처리 실패 + 내용 없음). 모두 0이면 그리지 않는다.
 * - 누르면 드로어가 열리고 처리 현황 영역이 보인다(onOpen). 색이 아니라 글자로 표시한다.
 */
import { useProcessing } from "@/components/v2/ProcessingProvider";
import { isActiveStatus } from "@/lib/v2/processing-status";

export function ProcessingBadge({ onOpen }: { onOpen: () => void }) {
  const { items } = useProcessing();
  const running = items.filter((item) => isActiveStatus(item.status)).length;
  const done = items.filter((item) => item.status === "completed").length;
  const failed = items.filter((item) => item.status === "failed" || item.status === "no_content").length;
  const parts = [running > 0 ? `처리 중 ${running}건` : null, done + failed > 0 ? `완료 ${done} · 실패 ${failed}` : null].filter(Boolean);
  if (parts.length === 0) return null;
  const text = parts.join(" · ");
  return (
    <button
      type="button"
      aria-label={`처리 현황 요약: ${text}`}
      onClick={onOpen}
      className="mn-focus inline-flex min-h-11 min-w-0 items-center truncate rounded-mn-control border border-mn-border px-3 font-mn-mono text-xs text-mn-text hover:bg-mn-elevated"
    >
      <span className="truncate">{text}</span>
    </button>
  );
}
