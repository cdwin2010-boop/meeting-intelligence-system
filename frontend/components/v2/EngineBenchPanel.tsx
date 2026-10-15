"use client";

/*
 * 원격(시험용) 엔진 연결 점검·L-3 벤치마크 측정 영역(작업 L-3a, 시험 전용). 엔진 시험 화면 안에서 쓴다.
 * 주소·키 입력칸은 위쪽 원격 엔진 설정을 그대로 쓴다(새 입력칸 없음). 측정은 서버가 백그라운드로 실행하고 이 화면은 상태만 조회한다.
 * 연결이 끊기면 상태가 "중단됨(연결 끊김)"이 되고, 위 설정에서 새 주소를 저장한 뒤 이어하기를 누르면 남은 항목만 실행한다.
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { Button, StatusDot } from "@/components/mono";
import { messageOf } from "@/components/v2/admin/AdminUi";
import {
  BENCH_RESULT_FILE_NAME,
  BENCH_STATE_LABEL,
  downloadBenchResult,
  formatElapsed,
  getBenchStatus,
  resumeBench,
  startBench,
  stopBench,
  type BenchStatus,
} from "@/lib/v2/engine-bench";
import { isAbortError } from "@/lib/v2/errors";

const POLL_MS = 2000;
const DISCONNECT_NOTICE = "연결이 끊겼습니다. 새 주소를 입력하고 이어하기를 눌러 주세요";

export function EngineBenchPanel() {
  const headingId = useId();
  const [status, setStatus] = useState<BenchStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    const controller = new AbortController();
    controllerRef.current?.abort();
    controllerRef.current = controller;
    try {
      setStatus(await getBenchStatus(controller.signal));
    } catch (e) {
      if (!isAbortError(e)) setError(messageOf(e, "측정 상태를 불러오지 못했습니다."));
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => controllerRef.current?.abort();
  }, [refresh]);

  // 측정 중에는 2초마다 상태를 다시 조회한다(끝나면 멈춘다)
  const running = status?.status === "running";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [running, refresh]);

  async function act(run: () => Promise<BenchStatus>) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      setStatus(await run());
    } catch (e) {
      if (!isAbortError(e)) setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  async function download() {
    setError(null);
    try {
      const blob = await downloadBenchResult();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = BENCH_RESULT_FILE_NAME;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      if (!isAbortError(e)) setError(messageOf(e, "결과 파일을 내려받지 못했습니다."));
    }
  }

  const state = status?.status ?? "idle";
  const tone = state === "completed" ? "ready" : state === "running" ? "building" : state === "idle" ? "queued" : "error";
  const interrupted = state === "interrupted";

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3 rounded-mn-card border border-mn-border bg-mn-surface p-5">
      <div className="flex flex-col gap-1">
        <h3 id={headingId} className="text-[15px] font-bold leading-6">
          연결 점검 및 벤치마크 측정
        </h3>
        <p className="text-sm text-mn-muted">
          도구에 내장한 가상 전사본으로 측정합니다(실제 회의 데이터 사용 안 함). 결과 파일에는 건수·시간·비율만 담기고 주소·키는 담기지 않습니다.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button onClick={() => void act(() => startBench("quick"))} disabled={busy || running} aria-label="빠른 점검">
          빠른 점검(약 5분)
        </Button>
        <Button onClick={() => void act(() => startBench("full"))} disabled={busy || running} aria-label="전체 측정">
          전체 측정
        </Button>
        <Button onClick={() => void act(() => resumeBench())} disabled={busy || running || !status?.canResume} aria-label="이어하기">
          이어하기
        </Button>
        <Button onClick={() => void act(() => stopBench())} disabled={busy || !running} aria-label="중단">
          중단
        </Button>
      </div>

      <div aria-label="측정 진행 상태" role="status" className="flex flex-col gap-1 text-sm">
        <StatusDot tone={tone} label={BENCH_STATE_LABEL[state]} />
        {status && state !== "idle" ? (
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1">
            <dt className="text-mn-muted">현재 항목</dt>
            <dd>{status.currentLabel || "—"}</dd>
            <dt className="text-mn-muted">완료</dt>
            <dd className="font-mn-mono text-[13px]">
              {status.doneUnits}/{status.totalUnits}
            </dd>
            <dt className="text-mn-muted">경과 시간</dt>
            <dd className="font-mn-mono text-[13px]">{formatElapsed(status.elapsedSeconds)}</dd>
          </dl>
        ) : null}
        {interrupted ? <p role="alert">{status?.message || DISCONNECT_NOTICE}</p> : null}
        {!interrupted && status?.message ? <p className="text-mn-muted">{status.message}</p> : null}
      </div>

      {error ? (
        <p role="alert" className="text-sm">
          <StatusDot tone="error" label={error} />
        </p>
      ) : null}

      <div>
        <Button onClick={() => void download()} disabled={!status?.canDownload} aria-label="결과 파일 내려받기">
          결과 파일 내려받기
        </Button>
      </div>
    </section>
  );
}
