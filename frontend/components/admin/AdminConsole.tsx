"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, MetricCard, StatusDot } from "@/components/mono";
import { ApiError, fetchAdminJobs, isAbortError, killJob, retryJob } from "@/lib/api";
import { formatTime } from "@/lib/format";
import { nextSort } from "@/lib/table-sort";
import type { AdminJob, JobSortKey, JobSortState } from "@/lib/types";
import { JobQueueTable } from "./JobQueueTable";
import { KillJobModal } from "./KillJobModal";

// 자동 새로고침 주기(ms). NEXT_PUBLIC_ADMIN_POLL_MS=0 이면 끔.
const POLL_INTERVAL_MS = Number(process.env.NEXT_PUBLIC_ADMIN_POLL_MS ?? "10000") || 0;

/** 목록에서 상단 메트릭 3종을 계산합니다 (서버 집계 API가 생기면 이 함수만 교체). */
function computeMetrics(jobs: AdminJob[]) {
  const queued = jobs.filter((j) => j.status === "queued").length;
  const processing = jobs.filter((j) => j.status === "processing").length;
  const completed = jobs.filter((j) => j.status === "completed").length;
  const failed = jobs.filter((j) => j.status === "failed").length;
  const finished = completed + failed; // 성공/실패가 확정된 작업만 성공률 분모로 사용
  return {
    queueCount: queued + processing,
    queued,
    processing,
    successRate: finished === 0 ? null : (completed / finished) * 100,
    completed,
    finished,
    failed,
  };
}

export function AdminConsole() {
  const [jobs, setJobs] = useState<AdminJob[]>([]);
  const [sort, setSort] = useState<JobSortState>(null);
  const [refreshKey, setRefreshKey] = useState(0); // 값이 바뀌면 목록을 다시 조회
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [expandedIds, setExpandedIds] = useState<ReadonlySet<string>>(new Set());

  const [retryingIds, setRetryingIds] = useState<ReadonlySet<string>>(new Set());
  const [actionError, setActionError] = useState<string | null>(null);

  const [killTarget, setKillTarget] = useState<AdminJob | null>(null);
  const [killing, setKilling] = useState(false);
  const [killError, setKillError] = useState<string | null>(null);

  const [announcement, setAnnouncement] = useState(""); // 스크린리더용 결과 안내

  const killAbortRef = useRef<AbortController | null>(null);
  const retryAbortRef = useRef(new Map<string, AbortController>());
  const regionRef = useRef<HTMLDivElement>(null);
  const pendingFocusRef = useRef(false); // 다음 조회가 끝나면 표 영역으로 포커스를 옮길지 여부
  const silentRef = useRef(false); // true면 이번 조회는 "조용히"(로딩 표시·표 흐림 없이) 수행 = 자동 새로고침용

  /* ---------- 조회: 정렬 변경/새로고침마다 다시 가져오고, 이전 요청은 취소 ---------- */
  useEffect(() => {
    const controller = new AbortController(); // 이 요청 전용 "취소 리모컨"
    // 자동 새로고침(silent)이면 표를 흐리게 하거나 버튼 글자를 바꾸지 않습니다(10초마다 깜빡이는 것 방지).
    const silent = silentRef.current;
    silentRef.current = false;
    if (!silent) {
      setLoading(true);
      setLoadError(null);
    }

    fetchAdminJobs(sort, controller.signal)
      .then((rows) => {
        setJobs(rows);
        setUpdatedAt(new Date());
        setLoadError(null);
        setLoading(false);

        if (pendingFocusRef.current) {
          pendingFocusRef.current = false;
          // 강제 종료로 행의 버튼이 바뀌면 포커스가 사라지므로, 표 영역으로 옮깁니다.
          requestAnimationFrame(() => regionRef.current?.focus());
        }
      })
      .catch((error: unknown) => {
        if (isAbortError(error)) return; // 우리가 취소한 요청 → 에러가 아니므로 상태를 건드리지 않음
        // 조용한 갱신이 실패하면 기존 표를 그대로 두고 넘어갑니다("Updated" 시각이 멈춰 있는 것으로 알 수 있음).
        if (silent) {
          setLoading(false); // 직전의 일반 조회를 취소하고 끼어든 경우를 대비해 로딩 표시만 해제
          return;
        }
        setLoadError("Failed to load the job queue.");
        setLoading(false);
      });

    // 정렬이 또 바뀌거나 화면을 떠나면 진행 중 요청 취소 (오래된 응답이 최신 결과를 덮어쓰는 것 방지)
    return () => controller.abort();
  }, [sort, refreshKey]);

  // 일정 주기로 조용히 다시 조회 (탭이 화면에 보일 때만). 0이면 자동 새로고침을 끕니다.
  useEffect(() => {
    if (POLL_INTERVAL_MS <= 0) return;
    const timer = setInterval(() => {
      if (document.visibilityState !== "visible") return;
      silentRef.current = true;
      setRefreshKey((key) => key + 1); // 조회 effect가 다시 실행되며, 아직 끝나지 않은 이전 요청은 취소됨(겹침 방지)
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer); // 화면을 떠나면 타이머 정리(정리하지 않으면 계속 돌아감)
  }, []);

  // 화면을 떠날 때 진행 중인 재시도/종료 요청도 모두 정리
  useEffect(() => {
    const retryControllers = retryAbortRef.current;
    return () => {
      killAbortRef.current?.abort();
      retryControllers.forEach((controller) => controller.abort());
    };
  }, []);

  const metrics = useMemo(() => computeMetrics(jobs), [jobs]);
  const initialLoaded = updatedAt !== null; // 첫 응답 전에는 수치 대신 "—" 표시

  /* ---------- 핸들러 ---------- */
  const handleSort = useCallback((key: JobSortKey) => {
    setSort((current) => nextSort(current, key));
  }, []);

  const handleRefresh = useCallback(() => setRefreshKey((key) => key + 1), []);

  const handleToggleExpand = useCallback((id: string) => {
    setExpandedIds((current) => {
      const next = new Set(current); // 상태는 직접 수정하지 않고 복사본을 만든다(불변성)
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const handleRetry = useCallback(async (job: AdminJob) => {
    if (retryAbortRef.current.has(job.id)) return; // 이미 진행 중이면 중복 요청 방지
    const controller = new AbortController();
    retryAbortRef.current.set(job.id, controller);
    setActionError(null);
    setRetryingIds((current) => new Set(current).add(job.id));

    try {
      await retryJob(job.id, controller.signal);
      setAnnouncement(`Job ${job.id} was queued for retry.`);
      setRefreshKey((key) => key + 1); // 최신 상태(정렬 포함)로 다시 조회
    } catch (error: unknown) {
      if (isAbortError(error)) return;
      if (error instanceof ApiError && error.code === "invalid_state") {
        setActionError(`Job ${job.id} is no longer failed. The list was refreshed.`);
        setRefreshKey((key) => key + 1);
      } else {
        setActionError(`Could not retry job ${job.id}. Try again.`);
      }
    } finally {
      retryAbortRef.current.delete(job.id);
      setRetryingIds((current) => {
        const next = new Set(current);
        next.delete(job.id);
        return next;
      });
    }
  }, []);

  const closeKillModal = useCallback(() => {
    killAbortRef.current?.abort(); // 종료 요청이 진행 중이었다면 취소
    killAbortRef.current = null;
    setKilling(false);
    setKillError(null);
    setKillTarget(null);
  }, []);

  const handleConfirmKill = useCallback(async () => {
    if (!killTarget) return;
    const target = killTarget;
    const controller = new AbortController();
    killAbortRef.current = controller;
    setKilling(true);
    setKillError(null);

    try {
      await killJob(target.id, controller.signal);
      setAnnouncement(`Job ${target.id} was terminated.`);
      killAbortRef.current = null;
      setKilling(false);
      setKillTarget(null);
      pendingFocusRef.current = true;
      setRefreshKey((key) => key + 1);
    } catch (error: unknown) {
      if (isAbortError(error)) return; // 사용자가 Cancel/ESC로 취소한 경우
      setKilling(false);
      if (error instanceof ApiError && error.code === "invalid_state") {
        // 화면을 보는 사이 작업이 이미 끝난 경우: 안내하고 목록을 새로고침
        setKillError("This job is no longer processing. The list was refreshed.");
        setRefreshKey((key) => key + 1);
      } else {
        setKillError("Could not kill the job. Try again.");
      }
    }
  }, [killTarget]);

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-12">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[32px] font-semibold leading-10 tracking-tight text-mn-text">
            Admin console
          </h1>
          <p className="mt-1 text-sm text-mn-muted">STT and LLM job queue for today.</p>
        </div>
        <div className="flex items-center gap-3">
          {updatedAt ? (
            <span className="font-mn-mono text-xs text-mn-muted">
              Updated {formatTime(updatedAt)}
            </span>
          ) : null}
          <Button variant="secondary" size="sm" onClick={handleRefresh} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </Button>
        </div>
      </header>

      {/* 상단 시스템 메트릭 3종 */}
      <section aria-label="System metrics" className="grid gap-4 sm:grid-cols-3">
        <MetricCard
          label="Queue count"
          value={initialLoaded ? metrics.queueCount : "—"}
          hint={initialLoaded ? `queued ${metrics.queued} · processing ${metrics.processing}` : undefined}
        />
        <MetricCard
          label="Success rate (today)"
          value={
            initialLoaded && metrics.successRate !== null ? `${metrics.successRate.toFixed(1)}%` : "—"
          }
          hint={initialLoaded ? `${metrics.completed} of ${metrics.finished} finished jobs` : undefined}
        />
        <MetricCard
          label="Failed jobs"
          value={initialLoaded ? metrics.failed : "—"}
          hint={initialLoaded ? "select a failed row for its log" : undefined}
        />
      </section>

      <section aria-labelledby="job-queue-heading" className="flex flex-col gap-3">
        <div className="flex items-baseline justify-between">
          <h2 id="job-queue-heading" className="text-xl font-semibold leading-7 text-mn-text">
            Job queue
          </h2>
          <span className="font-mn-mono text-xs text-mn-muted">{jobs.length} jobs</span>
        </div>

        {actionError ? (
          <p role="alert">
            <StatusDot tone="error" label={actionError} />
          </p>
        ) : null}

        {/* tabIndex=-1: 코드로만 포커스를 받는 영역 (강제 종료 후 포커스 복귀용) */}
        <div ref={regionRef} tabIndex={-1} className="outline-none">
          <JobQueueTable
            jobs={jobs}
            sort={sort}
            onSort={handleSort}
            loading={loading}
            error={loadError}
            expandedIds={expandedIds}
            onToggleExpand={handleToggleExpand}
            retryingIds={retryingIds}
            onRetry={handleRetry}
            onKill={setKillTarget}
          />
        </div>
      </section>

      <KillJobModal
        job={killTarget}
        killing={killing}
        error={killError}
        onCancel={closeKillModal}
        onConfirm={handleConfirmKill}
      />

      <p role="status" aria-live="polite" className="mn-sr-only">
        {announcement}
      </p>
    </div>
  );
}
