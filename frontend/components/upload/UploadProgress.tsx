"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { StatusDot, type StatusDotTone } from "@/components/mono";
import { fetchJob, isAbortError } from "@/lib/api";
import type { UploadedJob, UploadReceipt } from "@/lib/types";

// 진행 상태 조회 주기(ms). 기본 3초.
const POLL_INTERVAL_MS = Number(process.env.NEXT_PUBLIC_UPLOAD_POLL_MS ?? "3000") || 3000;

const isFinished = (job: UploadedJob) => job.status === "completed" || job.status === "failed";

/** 작업 상태 → 점 색 + 글자 라벨 (색만으로 구분하지 않도록 라벨은 항상 함께) */
function statusView(job: UploadedJob): { tone: StatusDotTone; label: string } {
  switch (job.status) {
    case "queued":
      return { tone: "queued", label: "Queued" };
    case "processing":
      return { tone: "building", label: job.stage ? `Processing ${job.stage}` : "Processing" };
    case "completed":
      return { tone: "ready", label: "Completed" };
    case "failed":
      return { tone: "error", label: "Failed" };
  }
}

export function UploadProgress({ receipt }: { receipt: UploadReceipt }) {
  // 첫 조회 전에는 접수증 내용으로 "Queued"를 보여 준다
  const [job, setJob] = useState<UploadedJob>({
    id: receipt.jobId,
    meetingId: receipt.meetingId,
    status: receipt.status,
    stage: null,
    errorMessage: "",
  });
  const [tick, setTick] = useState(0); // 값이 바뀌면 한 번 더 조회
  const finished = isFinished(job);

  /* ---------- 조회: tick마다 한 번. 이전 요청은 취소, 실패하면 기존 표시를 유지하고 다음 주기에 재시도 ---------- */
  useEffect(() => {
    if (finished) return;
    const controller = new AbortController();
    fetchJob(receipt.jobId, controller.signal)
      .then(setJob)
      .catch((error: unknown) => {
        if (isAbortError(error)) return;
        // 조용한 재시도: 화면은 그대로 두고 다음 주기에 다시 조회
      });
    return () => controller.abort();
  }, [receipt.jobId, tick, finished]);

  // 주기적 조회: 탭이 보일 때만, completed/failed가 되면 멈춘다. 탭으로 돌아오면 바로 한 번 조회.
  useEffect(() => {
    if (finished) return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") setTick((value) => value + 1);
    }, POLL_INTERVAL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") setTick((value) => value + 1);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [finished]);

  const view = statusView(job);

  return (
    <section
      aria-labelledby="upload-progress-heading"
      className="flex flex-col gap-4 rounded-mn-card border border-mn-border bg-mn-surface p-6"
    >
      <h2 id="upload-progress-heading" className="text-xl font-semibold leading-7 text-mn-text">
        처리 진행 상황
      </h2>

      <dl className="grid grid-cols-[auto_1fr] items-center gap-x-6 gap-y-3 text-sm">
        <dt className="text-mn-muted">Job ID</dt>
        <dd className="font-mn-mono text-[13px] text-mn-text">{job.id}</dd>

        <dt className="text-mn-muted">Status</dt>
        {/* 상태가 바뀌면 스크린리더가 읽어 준다 */}
        <dd role="status" aria-live="polite">
          <StatusDot tone={view.tone} label={view.label} />
        </dd>
      </dl>

      {job.status === "failed" && job.errorMessage ? (
        <p className="rounded-mn-control border border-mn-border bg-mn-bg p-3 font-mn-mono text-xs text-mn-text">
          {job.errorMessage}
        </p>
      ) : null}

      {job.status === "completed" ? (
        <div>
          <Link
            href={`/meetings/${encodeURIComponent(job.meetingId)}`}
            className="mn-focus inline-flex h-10 items-center justify-center rounded-mn-control bg-mn-accent px-4 text-sm font-medium text-mn-on-accent hover:bg-mn-accent-hover"
          >
            결과 보기
          </Link>
        </div>
      ) : null}
    </section>
  );
}
