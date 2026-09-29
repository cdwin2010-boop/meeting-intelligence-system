"use client";

import { Button, StatusDot, type StatusDotTone } from "@/components/mono";
import { formatDateTime, formatDuration } from "@/lib/format";
import { getAriaSort } from "@/lib/table-sort";
import type { AdminJob, JobSortKey, JobSortState, JobStatus } from "@/lib/types";

/* 작업 상태 → StatusDot 매핑 (Processing: Blue / Completed: Teal / Failed: Red / Queued: 회색) */
const STATUS_VIEW: Record<JobStatus, { tone: StatusDotTone; label: string }> = {
  queued: { tone: "queued", label: "Queued" },
  processing: { tone: "building", label: "Processing" },
  completed: { tone: "ready", label: "Completed" },
  failed: { tone: "error", label: "Failed" },
};

interface ColumnDef {
  key: JobSortKey;
  header: string;
  /** 숫자 열은 오른쪽 정렬 (Mono Dark 규칙) */
  numeric?: boolean;
  className?: string;
}

const COLUMNS: ColumnDef[] = [
  { key: "id", header: "Job ID", className: "w-52" },
  { key: "meetingTitle", header: "Meeting" },
  { key: "audioSeconds", header: "Audio length", numeric: true, className: "w-32" },
  { key: "elapsedSeconds", header: "Elapsed", numeric: true, className: "w-28" },
  { key: "status", header: "Status", className: "w-36" },
];

const COLUMN_COUNT = COLUMNS.length + 1; // + 액션 열

interface JobQueueTableProps {
  jobs: AdminJob[];
  sort: JobSortState;
  onSort: (key: JobSortKey) => void;
  loading: boolean;
  error: string | null;
  expandedIds: ReadonlySet<string>;
  onToggleExpand: (id: string) => void;
  retryingIds: ReadonlySet<string>;
  onRetry: (job: AdminJob) => void;
  onKill: (job: AdminJob) => void;
}

export function JobQueueTable({
  jobs,
  sort,
  onSort,
  loading,
  error,
  expandedIds,
  onToggleExpand,
  retryingIds,
  onRetry,
  onKill,
}: JobQueueTableProps) {
  return (
    <div className="overflow-x-auto rounded-mn-card border border-mn-border bg-mn-surface">
      <table className="w-full border-collapse text-left" aria-busy={loading}>
        <caption className="mn-sr-only">
          STT and LLM job queue. Select a column header to sort. Select a failed job row to show its
          error log and worker details.
        </caption>

        <thead>
          <tr className="h-12 border-b border-mn-border">
            {COLUMNS.map((column) => {
              const active = sort?.key === column.key;
              return (
                <th
                  key={column.key}
                  scope="col"
                  aria-sort={getAriaSort(sort, column.key)}
                  className={`px-4 text-xs font-medium ${column.numeric ? "text-right" : ""} ${column.className ?? ""}`}
                >
                  <button
                    type="button"
                    onClick={() => onSort(column.key)}
                    className={[
                      "mn-focus -mx-2 inline-flex h-8 items-center gap-1 rounded-mn-control px-2",
                      "hover:bg-mn-elevated",
                      active ? "text-mn-text" : "text-mn-muted",
                    ].join(" ")}
                  >
                    {column.header}
                    {/* 색이 아닌 기호로도 방향 표시 (↕ 없음 / ↑ 오름차순 / ↓ 내림차순) */}
                    <span aria-hidden="true" className="font-mn-mono">
                      {active ? (sort.direction === "asc" ? "↑" : "↓") : "↕"}
                    </span>
                  </button>
                </th>
              );
            })}
            <th scope="col" className="w-40 px-4">
              <span className="mn-sr-only">Actions</span>
            </th>
          </tr>
        </thead>

        <tbody className={loading && jobs.length > 0 ? "opacity-60" : undefined}>
          {error ? (
            <tr className="h-12">
              <td colSpan={COLUMN_COUNT} className="px-4">
                {/* 에러는 Red 점 + 문구로 함께 표시 (색만으로 전달하지 않음) */}
                <span role="alert">
                  <StatusDot tone="error" label={error} />
                </span>
              </td>
            </tr>
          ) : null}

          {!error && loading && jobs.length === 0 ? (
            <tr className="h-12">
              <td colSpan={COLUMN_COUNT} className="px-4 text-sm text-mn-muted">
                Loading jobs…
              </td>
            </tr>
          ) : null}

          {!error && !loading && jobs.length === 0 ? (
            <tr className="h-12">
              <td colSpan={COLUMN_COUNT} className="px-4 text-sm text-mn-muted">
                No jobs in the queue.
              </td>
            </tr>
          ) : null}

          {jobs.map((job) => (
            <JobRows
              key={job.id}
              job={job}
              // 실패한 작업만 펼칠 수 있음 (재시도로 상태가 바뀌면 자동으로 접힘)
              expanded={job.status === "failed" && expandedIds.has(job.id)}
              retrying={retryingIds.has(job.id)}
              onToggleExpand={onToggleExpand}
              onRetry={onRetry}
              onKill={onKill}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * 한 작업 = <tr> 최대 두 개 (본 행 + 실패 시 펼침 행)
 * ------------------------------------------------------------------ */
interface JobRowsProps {
  job: AdminJob;
  expanded: boolean;
  retrying: boolean;
  onToggleExpand: (id: string) => void;
  onRetry: (job: AdminJob) => void;
  onKill: (job: AdminJob) => void;
}

function JobRows({ job, expanded, retrying, onToggleExpand, onRetry, onKill }: JobRowsProps) {
  const status = STATUS_VIEW[job.status];
  const expandable = job.status === "failed";
  const detailId = `job-detail-${job.id}`;

  return (
    <>
      <tr
        // 펼칠 수 있는 행만 클릭 가능. button의 click이 tr까지 버블링되므로 핸들러는 tr에 한 번만 둡니다.
        onClick={expandable ? () => onToggleExpand(job.id) : undefined}
        className={[
          "h-12 border-b border-mn-border",
          expandable ? "cursor-pointer hover:bg-mn-elevated" : "",
        ].join(" ")}
      >
        <td className="px-4">
          {expandable ? (
            <button
              type="button"
              aria-expanded={expanded}
              aria-controls={expanded ? detailId : undefined}
              aria-label={`${job.id}, show error log`}
              className="mn-focus -mx-2 flex items-center gap-2 rounded-mn-control px-2 py-1 text-left"
            >
              <span
                aria-hidden="true"
                className={`shrink-0 text-mn-muted transition-transform ${expanded ? "rotate-90" : ""}`}
              >
                ▸
              </span>
              <span className="font-mn-mono text-xs text-mn-text">{job.id}</span>
            </button>
          ) : (
            // 화살표 자리(약 16px)만큼 들여써서 Job ID 열이 한 줄로 정렬되도록 함
            <span className="pl-4 font-mn-mono text-xs text-mn-text">{job.id}</span>
          )}
        </td>

        <td className="max-w-0 px-4">
          {/* 행 높이 48px 유지를 위해 한 줄로 자르고 전체 제목은 title로 제공 */}
          <span className="block truncate text-sm text-mn-text" title={job.meetingTitle}>
            {job.meetingTitle}
          </span>
        </td>
        <td className="px-4 text-right font-mn-mono text-xs text-mn-text">
          {formatDuration(job.audioSeconds)}
        </td>
        <td className="px-4 text-right font-mn-mono text-xs text-mn-text">
          {formatDuration(job.elapsedSeconds)}
        </td>
        <td className="px-4">
          <StatusDot tone={status.tone} label={status.label} />
        </td>

        <td className="px-4 text-right">
          {job.status === "failed" ? (
            <Button
              size="sm"
              variant="secondary"
              disabled={retrying}
              aria-label={`Retry job ${job.id}`}
              onClick={(event) => {
                event.stopPropagation(); // 재시도 클릭이 행 펼침으로 번지지 않도록 차단
                onRetry(job);
              }}
            >
              {retrying ? "Retrying…" : "Retry"}
            </Button>
          ) : null}
          {job.status === "processing" ? (
            // 강제 종료 버튼 자체는 secondary. Red는 확인 모달의 최종 버튼에만 사용합니다.
            <Button
              size="sm"
              variant="secondary"
              aria-label={`Kill job ${job.id}`}
              onClick={(event) => {
                event.stopPropagation();
                onKill(job);
              }}
            >
              Kill job
            </Button>
          ) : null}
        </td>
      </tr>

      {expanded ? (
        <tr id={detailId} className="border-b border-mn-border bg-mn-bg">
          <td colSpan={COLUMN_COUNT} className="px-4 py-4">
            <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_18rem]">
              <section aria-label={`Error log for ${job.id}`}>
                <h3 className="mb-2 text-xs text-mn-muted">Error log</h3>
                {/* 콘솔 표시: 로그는 mono, 긴 줄은 가로 스크롤(tabIndex=0으로 키보드 스크롤 가능) */}
                <pre
                  tabIndex={0}
                  className="mn-focus max-h-56 overflow-auto rounded-mn-control border border-mn-border bg-mn-surface p-3 font-mn-mono text-xs leading-5 text-mn-text"
                >
                  {job.errorLog.length > 0 ? job.errorLog.join("\n") : "No log output."}
                </pre>
              </section>

              <section aria-label={`Worker details for ${job.id}`}>
                <h3 className="mb-2 text-xs text-mn-muted">Worker</h3>
                <dl className="grid grid-cols-[6rem_minmax(0,1fr)] gap-x-3 gap-y-2 rounded-mn-control border border-mn-border bg-mn-surface p-3 text-xs">
                  <WorkerRow label="Worker ID" value={job.worker?.id} />
                  <WorkerRow label="Host" value={job.worker?.host} />
                  <WorkerRow label="Engine" value={job.worker?.engine} />
                  <WorkerRow label="Stage" value={job.worker?.stage} />
                  <WorkerRow label="GPU" value={job.worker ? (job.worker.gpu ?? "none (API)") : undefined} />
                  <WorkerRow label="Attempt" value={String(job.attempt)} />
                  <WorkerRow label="Started" value={formatDateTime(job.startedAt)} />
                </dl>
              </section>
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function WorkerRow({ label, value }: { label: string; value: string | undefined }) {
  return (
    <>
      <dt className="text-mn-muted">{label}</dt>
      <dd className="break-words font-mn-mono text-mn-text">{value ?? "—"}</dd>
    </>
  );
}
