"use client";

/*
 * 회의록 음성 한 줄 재생기(재생·일시정지·이동·현재 시각). 열람 가능한 사람이면 누구나 쓴다(권한 판정은 서버).
 * - 마운트 시 재생 주소(약 10분 유효 서명 주소)를 한 번 발급받는다. 로그인 토큰은 주소에 넣지 않는다.
 *   발급이 거부되거나 음성이 없으면 서버 문구와 "음성 파일이 없습니다"를 보여 준다.
 * - 근거 타임스탬프·전사문 시각을 누르면 부모가 seek({sec, nonce}) 를 바꾸고, 그 위치부터 재생한다.
 * - 주소 기준은 NEXT_PUBLIC_API_V2_BASE_URL(API_V2_BASE_URL) 하나뿐이다.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { Button, StatusDot } from "@/components/mono";
import { formatOffset } from "@/components/v2/meeting-display";
import { isAbortError } from "@/lib/v2/errors";
import { API_V2_BASE_URL } from "@/lib/v2/http";
import { getAudioUrl } from "@/lib/v2/meetings";

export interface SeekRequest {
  sec: number;
  nonce: number;
}

const NO_AUDIO = "음성 파일이 없습니다";
// 발급 주소는 약 10분 유효: 8분이 지나면 다음 재생 때 다시 발급받는다
const REISSUE_AFTER_MS = 8 * 60 * 1000;

export function AudioPlayer({ meetingId, seek }: { meetingId: number; seek: SeekRequest | null }) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [src, setSrc] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const issuedAtRef = useRef(0);
  // 주소가 준비되기 전·메타데이터 전에 온 이동 요청은 보관했다가 준비되면 적용한다
  const pendingRef = useRef<{ sec: number; play: boolean } | null>(null);
  const handledNonceRef = useRef(0);
  const retriedRef = useRef(false);

  const issue = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const issued = await getAudioUrl(meetingId, signal);
        issuedAtRef.current = Date.now();
        setProblem(null);
        setSrc(`${API_V2_BASE_URL}${issued.url}`);
      } catch (error) {
        if (isAbortError(error)) return;
        const message = error instanceof Error && error.message ? error.message : "";
        setSrc(null);
        setProblem(message && message !== NO_AUDIO ? `${message} · ${NO_AUDIO}` : NO_AUDIO);
      }
    },
    [meetingId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void issue(controller.signal);
    return () => controller.abort();
  }, [issue]);

  const applyPending = useCallback(() => {
    const audio = audioRef.current;
    const pending = pendingRef.current;
    if (!audio || !pending || audio.readyState < 1) return;
    pendingRef.current = null;
    audio.currentTime = pending.sec;
    setCurrent(pending.sec);
    if (pending.play) void audio.play().catch(() => setProblem("재생하지 못했습니다"));
  }, []);

  // 타임스탬프 클릭: 그 위치로 이동해 재생
  useEffect(() => {
    if (!seek || seek.nonce === handledNonceRef.current) return;
    handledNonceRef.current = seek.nonce;
    pendingRef.current = { sec: seek.sec, play: true };
    if (problem) return;
    if (src && Date.now() - issuedAtRef.current > REISSUE_AFTER_MS) void issue();
    else applyPending();
  }, [seek, src, problem, issue, applyPending]);

  async function toggle() {
    const audio = audioRef.current;
    if (!audio || !src) return;
    if (!audio.paused) {
      audio.pause();
      return;
    }
    if (Date.now() - issuedAtRef.current > REISSUE_AFTER_MS) {
      pendingRef.current = { sec: audio.currentTime, play: true };
      await issue();
      return;
    }
    void audio.play().catch(() => setProblem("재생하지 못했습니다"));
  }

  if (problem) {
    return (
      <section aria-label="음성 재생" className="rounded-mn-card border border-mn-border bg-mn-surface px-5 py-3 text-sm">
        <span role="alert">
          <StatusDot tone="error" label={problem} />
        </span>
      </section>
    );
  }

  return (
    <section aria-label="음성 재생" className="flex items-center gap-3 rounded-mn-card border border-mn-border bg-mn-surface px-5 py-3">
      <audio
        ref={audioRef}
        src={src ?? undefined}
        preload="metadata"
        onLoadedMetadata={(event) => {
          retriedRef.current = false;
          setDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0);
          applyPending();
        }}
        onTimeUpdate={(event) => setCurrent(event.currentTarget.currentTime)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onError={() => {
          // 주소 만료 등으로 불러오지 못하면 한 번 다시 발급받아 본다. 그래도 안 되면 발급 단계가 문구를 보여 준다
          if (src && !retriedRef.current) {
            retriedRef.current = true;
            void issue();
          } else {
            setProblem(`재생하지 못했습니다 · ${NO_AUDIO}`);
          }
        }}
      />
      <Button size="sm" disabled={!src} aria-label={playing ? "일시정지" : "재생"} onClick={() => void toggle()}>
        {playing ? "일시정지" : "재생"}
      </Button>
      <input
        type="range"
        aria-label="재생 위치"
        min={0}
        max={Math.max(duration, 0)}
        step={1}
        value={Math.min(current, Math.max(duration, 0))}
        disabled={!src || duration <= 0}
        onChange={(event) => {
          const sec = Number(event.target.value);
          if (audioRef.current) audioRef.current.currentTime = sec;
          setCurrent(sec);
        }}
        className="mn-focus min-w-0 flex-1"
      />
      <span aria-label="현재 시각" className="whitespace-nowrap font-mn-mono text-[13px] text-mn-muted">
        {formatOffset(current)} / {formatOffset(duration)}
      </span>
    </section>
  );
}
