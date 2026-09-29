"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { StatusDot } from "@/components/mono";
import { fetchMeeting, isAbortError } from "@/lib/api";
import type { Meeting } from "@/lib/types";
import { MeetingDetail } from "./MeetingDetail";

/*
 * 목 모드 전용: 업로드로 만든 회의는 "브라우저 메모리"의 목 저장소에만 있어서
 * 서버 컴포넌트(/meetings/[id])가 찾지 못한다. 그때 브라우저에서 한 번 더 찾아본다.
 * 실서버 모드(NEXT_PUBLIC_USE_MOCK=false)에서는 쓰이지 않는다.
 */
export function MockMeetingLoader({ meetingId }: { meetingId: string }) {
  const [meeting, setMeeting] = useState<Meeting | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetchMeeting(meetingId, controller.signal)
      .then(setMeeting)
      .catch((error: unknown) => {
        if (isAbortError(error)) return;
        setMissing(true);
      });
    return () => controller.abort();
  }, [meetingId]);

  if (meeting) return <MeetingDetail meeting={meeting} />;

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-3 px-4 py-12">
      {missing ? (
        <>
          <p role="alert">
            <StatusDot tone="error" label="회의를 찾을 수 없습니다." />
          </p>
          <p className="text-sm text-mn-muted">
            목 모드에서는 새로고침하면 업로드한 회의가 사라집니다.{" "}
            <Link href="/upload" className="mn-focus rounded-mn-control text-mn-text underline">
              음성 등록
            </Link>
          </p>
        </>
      ) : (
        <p className="text-sm text-mn-muted">Loading…</p>
      )}
    </div>
  );
}
