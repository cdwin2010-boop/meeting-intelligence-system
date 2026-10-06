"use client";

/*
 * 회의록 처리 상태 안내(회의록 상세 머리 아래).
 * - 처리 중(processing): "음성을 처리하는 중입니다. 완료되면 업무가 표시됩니다." (자동 새로고침은 상세 화면이 한다)
 * - 처리 실패(failed): 오류 코드를 한국어 이유로 풀어 보여 주고 코드 글자도 함께 표시한다. "다시 처리" 버튼은 허용 동작(reprocess_meeting)이 있을 때만
 */
import { Button, StatusDot } from "@/components/mono";
import { describeProcessingError } from "@/lib/v2/processing-errors";
import type { MeetingProcessing, MeetingStatus } from "@/lib/v2/meetings";

interface ProcessingBannerProps {
  status: MeetingStatus;
  processing: MeetingProcessing | null | undefined;
  canReprocess: boolean;
  onReprocess: () => void;
}

export function ProcessingBanner({ status, processing, canReprocess, onReprocess }: ProcessingBannerProps) {
  if (status === "processing") {
    return (
      <p role="status" aria-label="처리 상태" className="rounded-mn-card border border-mn-border bg-mn-surface px-4 py-3 text-sm">
        <StatusDot tone="building" label="음성을 처리하는 중입니다. 완료되면 업무가 표시됩니다." />
      </p>
    );
  }
  if (status !== "failed") return null;
  const code = processing?.errorCode ?? null;
  return (
    <div
      role="alert"
      aria-label="처리 상태"
      className="flex flex-wrap items-center justify-between gap-3 rounded-mn-card border border-mn-border bg-mn-surface px-4 py-3"
    >
      <p className="flex min-w-0 flex-col gap-1 text-sm">
        <StatusDot tone="error" label={`처리에 실패했습니다 · ${describeProcessingError(code)}`} />
        {code ? (
          <span className="text-xs text-mn-muted">
            오류 코드 <span className="font-mn-mono text-[13px]">{code}</span>
          </span>
        ) : null}
      </p>
      {canReprocess ? (
        <Button size="sm" onClick={onReprocess}>
          다시 처리
        </Button>
      ) : null}
    </div>
  );
}
