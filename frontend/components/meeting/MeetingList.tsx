"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Button, StatusDot, type StatusDotTone } from "@/components/mono";
import { fetchMeetings, isAbortError } from "@/lib/api";
import type { JobStatus, MeetingSummary } from "@/lib/types";

/* 작업 상태 → 점 색 + 한국어 라벨 (색만으로 구분하지 않도록 라벨은 항상 함께) */
const STATUS_VIEW: Record<JobStatus, { tone: StatusDotTone; label: string }> = {
  queued: { tone: "queued", label: "대기" },
  processing: { tone: "building", label: "처리 중" },
  completed: { tone: "ready", label: "완료" },
  failed: { tone: "error", label: "실패" },
};

// 서버/클라이언트가 같은 결과를 내도록 시간대를 고정합니다 (MeetingHeader와 같은 형식).
const dateTimeFormatter = new Intl.DateTimeFormat("ko-KR", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "Asia/Seoul",
});

/** 읽을 수 없는 일시는 format이 RangeError를 던지므로 원문을 그대로 보여 준다 */
function formatStartedAt(iso: string): string {
  const time = Date.parse(iso);
  return Number.isNaN(time) ? iso : dateTimeFormatter.format(time);
}

const COLUMN_COUNT = 3;

export function MeetingList() {
  const [meetings, setMeetings] = useState<MeetingSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0); // "다시 시도"를 누르면 +1 → 다시 조회

  /* ---------- 조회: 마운트·다시 시도 때마다 한 번. 화면을 떠나면 요청 취소 ---------- */
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);

    fetchMeetings(controller.signal)
      .then((rows) => {
        setMeetings(rows);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) return; // 우리가 취소한 것 → 에러 아님
        setError("회의 목록을 불러오지 못했습니다. 네트워크 연결을 확인한 뒤 다시 시도해 주세요.");
        setLoading(false);
      });

    return () => controller.abort();
  }, [reloadKey]);

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-4 py-12">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-[32px] font-semibold leading-10 tracking-tight text-mn-text">회의 목록</h1>
          <p className="mt-1 text-sm text-mn-muted">최근 회의 일시 순으로 표시합니다.</p>
        </div>
        <Link
          href="/upload"
          className="mn-focus inline-flex h-8 items-center rounded-mn-control border border-mn-border px-3 text-[13px] font-medium text-mn-text hover:bg-mn-elevated"
        >
          음성 등록
        </Link>
      </header>

      <section aria-labelledby="meeting-list-heading" className="flex flex-col gap-3">
        <div className="flex items-baseline justify-between">
          <h2 id="meeting-list-heading" className="text-xl font-semibold leading-7 text-mn-text">
            Meetings
          </h2>
          <span className="font-mn-mono text-xs text-mn-muted">{meetings.length} total</span>
        </div>

        <div className="overflow-x-auto rounded-mn-card border border-mn-border bg-mn-surface">
          <table className="w-full border-collapse text-left" aria-busy={loading}>
            <caption className="mn-sr-only">회의 목록. 제목을 선택하면 회의 상세 화면으로 이동합니다.</caption>

            <thead>
              <tr className="h-12 border-b border-mn-border">
                <th scope="col" className="px-4 text-xs font-medium text-mn-muted">
                  제목
                </th>
                <th scope="col" className="w-56 px-4 text-xs font-medium text-mn-muted">
                  일시
                </th>
                <th scope="col" className="w-40 px-4 text-xs font-medium text-mn-muted">
                  상태
                </th>
              </tr>
            </thead>

            <tbody className={loading && meetings.length > 0 ? "opacity-60" : undefined}>
              {error ? (
                <tr className="h-12">
                  <td colSpan={COLUMN_COUNT} className="px-4 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      {/* 에러는 색(Red 점)과 함께 반드시 문구로도 표시 */}
                      <span role="alert">
                        <StatusDot tone="error" label={error} />
                      </span>
                      <Button size="sm" variant="secondary" onClick={() => setReloadKey((key) => key + 1)}>
                        다시 시도
                      </Button>
                    </div>
                  </td>
                </tr>
              ) : null}

              {!error && loading && meetings.length === 0 ? (
                <tr className="h-12">
                  <td colSpan={COLUMN_COUNT} className="px-4 text-sm text-mn-muted">
                    회의 목록을 불러오는 중…
                  </td>
                </tr>
              ) : null}

              {!error && !loading && meetings.length === 0 ? (
                <tr className="h-12">
                  <td colSpan={COLUMN_COUNT} className="px-4 text-sm text-mn-muted">
                    등록된 회의가 없습니다. 음성 등록에서 회의 음성을 올려 주세요.
                  </td>
                </tr>
              ) : null}

              {/* 오류가 나면 이전 목록 대신 오류 안내만 보여 준다 */}
              {error
                ? null
                : meetings.map((meeting) => {
                    const status = meeting.jobStatus ? STATUS_VIEW[meeting.jobStatus] : null;
                    return (
                      <tr key={meeting.id} className="h-12 border-b border-mn-border last:border-b-0 hover:bg-mn-elevated">
                        <td className="max-w-0 px-4">
                          {/* 행 높이 48px을 지키기 위해 한 줄로 자르고, 전체 제목은 title로 확인 */}
                          <Link
                            href={`/meetings/${encodeURIComponent(meeting.id)}`}
                            title={meeting.title}
                            className="mn-focus -mx-2 block truncate rounded-mn-control px-2 py-1 text-sm text-mn-text hover:underline"
                          >
                            {meeting.title}
                          </Link>
                        </td>
                        <td className="px-4 font-mn-mono text-xs text-mn-text">
                          <time dateTime={meeting.startedAt}>{formatStartedAt(meeting.startedAt)}</time>
                        </td>
                        <td className="px-4">
                          {status ? (
                            <StatusDot tone={status.tone} label={status.label} />
                          ) : (
                            <span className="text-xs text-mn-muted">처리 기록 없음</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
