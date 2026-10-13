/*
 * 처리 작업 상태 표시 한 곳(작업 88-2): 대기·진행·완료·실패 4단계를 StatusChip(87-1)으로 통일한다.
 * 회의록 상세의 처리 영역과 왼쪽 메뉴 "처리 현황"이 같이 쓴다. 색만으로 구분하지 않고 항상 글자 라벨이 함께 보인다.
 * 말소리가 없어 내용이 비어 완료된 작업(no_content)은 "내용 없음"으로 따로 보인다.
 */
import { StatusChip, type StatusChipTone } from "@/components/v2/StatusChip";
import { WORKER_DOWN_NOTICE } from "@/lib/v2/process-queue";
import type { QueueFields } from "@/lib/v2/process-queue";

type JobStatus = "queued" | "running" | "completed" | "failed" | "no_content";

const VIEW: Record<JobStatus, { tone: StatusChipTone; label: string }> = {
  queued: { tone: "pending", label: "대기 중" },
  running: { tone: "progress", label: "처리 중" },
  completed: { tone: "done", label: "처리 완료" },
  failed: { tone: "needs", label: "처리 실패" },
  no_content: { tone: "needs", label: "내용 없음" },
};

/** label 을 주면 기본 글자 대신 그 글자를 보인다(경과 시간·실패 이유를 붙이는 화면용) */
export function JobStatusChip({ status, label }: { status: string; label?: string }) {
  const view = VIEW[status as JobStatus] ?? VIEW.queued;
  return <StatusChip tone={view.tone} label={label ?? view.label} />;
}

/** 대기 중일 때만: 앞선 건수·우선 처리 표시·처리기 응답 없음 안내. 그 밖의 상태에서는 아무것도 그리지 않는다 */
export function QueueNotes({ status, queueAhead, prioritized, workerAlive }: { status: string } & QueueFields) {
  if (status !== "queued") return null;
  const showAhead = typeof queueAhead === "number";
  if (!showAhead && !prioritized && workerAlive !== false) return null;
  return (
    <span className="flex flex-wrap items-center gap-2 text-[13px] leading-5 text-mn-muted">
      {showAhead ? <span>앞에 <span className="font-mn-mono">{queueAhead}</span>건</span> : null}
      {prioritized ? <StatusChip tone="progress" label="우선 처리" /> : null}
      {workerAlive === false ? (
        <span role="status" className="w-full text-mn-text">
          {WORKER_DOWN_NOTICE}
        </span>
      ) : null}
    </span>
  );
}
