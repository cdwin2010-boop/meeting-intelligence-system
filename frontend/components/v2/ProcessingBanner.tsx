"use client";

/*
 * 회의록 처리 상태 안내(회의록 상세 머리 아래).
 * - 처리 중(processing): "음성을 처리하는 중입니다. 완료되면 업무가 표시됩니다." (자동 새로고침은 상세 화면이 한다)
 * - 처리 실패(failed): 오류 코드를 한국어 이유로 풀어 보여 주고 코드 글자도 함께 표시한다. "다시 처리" 버튼은 허용 동작(reprocess_meeting)이 있을 때만
 */
import { Button, StatusDot } from "@/components/mono";
import { describeProcessingError } from "@/lib/v2/processing-errors";
import { buildStages, stoppedAtText, type StageState, type StageView } from "@/lib/v2/processing-stages";
import { sourceKindOf } from "@/lib/v2/source-kind";
import type { MeetingProcessing, MeetingStatus } from "@/lib/v2/meetings";

interface ProcessingBannerProps {
  status: MeetingStatus;
  processing: MeetingProcessing | null | undefined;
  canReprocess: boolean;
  onReprocess: () => void;
  /** 자료 종류(audio | transcript_txt | transcript_xlsx). 자료 파일은 1단계가 "음성 변환 건너뜀" */
  sourceKind?: string | null;
}

const STAGE_TONE: Record<StageState, string> = {
  done: "mn-chip-success",
  current: "mn-chip-info",
  pending: "mn-chip-muted",
  failed: "mn-chip-danger",
  skipped: "mn-chip-neutral",
};

/** 처리 3단계(준비·변환 → 업무 추출 → 완료 정리). 색과 함께 글자로 상태를 쓴다 */
function Stages({ stages }: { stages: StageView[] }) {
  return (
    <ol aria-label="처리 단계" className="flex flex-wrap gap-2">
      {stages.map((stage) => (
        <li
          key={stage.key}
          aria-current={stage.state === "current" || stage.state === "failed" ? "step" : undefined}
          className={`mn-chip ${STAGE_TONE[stage.state]} max-w-full whitespace-normal break-words`}
        >
          <span className="font-mn-mono">{stage.number}</span> {stage.label} · {stage.stateLabel}
        </li>
      ))}
    </ol>
  );
}

export function ProcessingBanner({ status, processing, canReprocess, onReprocess, sourceKind }: ProcessingBannerProps) {
  const kind = sourceKindOf(sourceKind);
  if (status === "processing") {
    const stages = buildStages({ status: "processing", stage: processing?.stage, sourceKind: kind });
    return (
      <div role="status" aria-label="처리 상태" className="flex flex-col gap-2 rounded-mn-card border border-mn-border bg-mn-surface px-4 py-3 text-sm">
        <p>
          <StatusDot tone="building" label="음성을 처리하는 중입니다. 완료되면 업무가 표시됩니다." />
        </p>
        {stages ? <Stages stages={stages} /> : null}
      </div>
    );
  }
  if (status !== "failed") return null;
  const code = processing?.errorCode ?? null;
  const stages = buildStages({ status: "failed", stage: processing?.stage, sourceKind: kind });
  const stopped = stoppedAtText(stages);
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
        {stages ? <Stages stages={stages} /> : null}
        {stopped ? <span className="text-xs text-mn-muted">{stopped}</span> : null}
      </p>
      {canReprocess ? (
        <Button size="sm" onClick={onReprocess}>
          다시 처리
        </Button>
      ) : null}
    </div>
  );
}

/** 처리 경고(완료됐지만 일부 구간 처리가 실패한 사항). 없으면(필드 없음 포함) 아무것도 그리지 않는다 */
export function ProcessingWarnings({ warnings }: { warnings: string[] | null | undefined }) {
  const list = Array.isArray(warnings) ? warnings.filter((w) => typeof w === "string" && w.trim() !== "") : [];
  if (list.length === 0) return null;
  return (
    <section aria-label="처리 경고" className="flex flex-col gap-2 rounded-mn-card border border-mn-border bg-mn-surface px-4 py-3">
      <p role="status" className="text-sm">
        <StatusDot tone="error" label="일부 구간 처리 실패 · 아래 구간의 업무와 회의록 항목은 빠졌을 수 있습니다. 필요하면 다시 처리해 주세요." />
      </p>
      <ul className="flex flex-col gap-1 pl-5 text-xs text-mn-muted">
        {list.map((warning, index) => (
          <li key={`${index}-${warning}`} className="list-disc break-words">
            {warning}
          </li>
        ))}
      </ul>
    </section>
  );
}

