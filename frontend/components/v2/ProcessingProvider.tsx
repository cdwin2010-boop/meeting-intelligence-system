"use client";

/*
 * 처리 현황 상태 저장소(왼쪽 메뉴 "처리 현황"과 업로드 화면·회의록 상세가 함께 쓴다).
 * - 앱이 열릴 때(로그인 직후·새로고침) 한 번 조회해 진행 중·완료·실패 항목을 복원한다. 업로드 직후에는 refresh() 로 바로 조회한다.
 * - 처리 중(queued·running) 항목이 하나라도 있는 동안만 주기적으로 조회한다(주기 NEXT_PUBLIC_PROCESSING_REFRESH_SEC, 기본 5초).
 *   없으면 멈추고, 화면을 떠나거나 로그아웃하면 진행 중인 요청을 취소한다.
 * - 처리 중이던 항목이 완료·실패·내용 없음으로 바뀌면 구독자에게 알린다(상세 화면이 조용히 다시 불러온다) + 상태 변경 안내문(aria-live).
 * - 닫은 항목은 계정별로 브라우저 저장소(localStorage)에 작업 번호로 저장해 다시 보이지 않게 한다. 저장소 접근이 막혀도 화면은 정상(메모리에서만 유지).
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { useAuth } from "@/components/v2/AuthProvider";
import { isAbortError } from "@/lib/v2/errors";
import { isActiveStatus, listMyProcessing, type ProcessingItem } from "@/lib/v2/processing-status";
import { PROCESSING_REFRESH_MS } from "@/lib/v2/system";

export interface TrackedItem extends ProcessingItem {
  /** 응답을 받은 시각(performance.now). 경과 시간 = elapsedSec + 이후 흐른 시간 */
  receivedAt: number;
}

export interface FinishedEvent {
  meetingId: number;
  status: ProcessingItem["status"];
}

interface ProcessingContextValue {
  /** 닫지 않은 항목(최신순) */
  items: TrackedItem[];
  /** 조용히 다시 조회(업로드 직후·재처리 직후) */
  refresh: () => void;
  dismiss: (jobId: number) => void;
  subscribe: (listener: (event: FinishedEvent) => void) => () => void;
  /** 상태가 바뀔 때 읽어 줄 안내문(aria-live) */
  announcement: string;
}

const NOOP: ProcessingContextValue = { items: [], refresh: () => undefined, dismiss: () => undefined, subscribe: () => () => undefined, announcement: "" };
const ProcessingContext = createContext<ProcessingContextValue>(NOOP);

export const useProcessing = () => useContext(ProcessingContext);

/** 지정한 회의록이 처리 중에서 끝나면(완료·실패·내용 없음) callback 을 부른다 */
export function useProcessingFinished(meetingId: number | null, callback: (event: FinishedEvent) => void): void {
  const { subscribe } = useProcessing();
  const ref = useRef(callback);
  useEffect(() => {
    ref.current = callback;
  }, [callback]);
  useEffect(() => {
    if (meetingId === null) return;
    return subscribe((event) => {
      if (event.meetingId === meetingId) ref.current(event);
    });
  }, [meetingId, subscribe]);
}

const STORAGE_PREFIX = "mi.v2.processing.dismissed.";
const MAX_REMEMBERED = 100;

function readDismissed(accountId: number): number[] {
  try {
    const raw = window.localStorage.getItem(`${STORAGE_PREFIX}${accountId}`);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((n): n is number => typeof n === "number") : [];
  } catch {
    return []; // 저장소가 막혀 있거나 값이 깨져 있어도 화면은 정상
  }
}

function writeDismissed(accountId: number, ids: number[]): void {
  try {
    window.localStorage.setItem(`${STORAGE_PREFIX}${accountId}`, JSON.stringify(ids.slice(-MAX_REMEMBERED)));
  } catch {
    // 저장 실패는 무시(이번 방문 동안만 유지)
  }
}

const FINISH_TEXT: Record<string, string> = { completed: "처리 완료", failed: "처리 실패", no_content: "내용 없음" };

export function ProcessingProvider({ children }: { children: ReactNode }) {
  const { account } = useAuth();
  const accountId = account?.id ?? null;
  const [raw, setRaw] = useState<TrackedItem[]>([]);
  const [dismissed, setDismissed] = useState<number[]>([]);
  const [announcement, setAnnouncement] = useState("");
  const previous = useRef<Map<number, string>>(new Map());
  const listeners = useRef(new Set<(event: FinishedEvent) => void>());
  const controllerRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    if (accountId === null) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      const list = await listMyProcessing(controller.signal);
      const receivedAt = performance.now();
      const messages: string[] = [];
      for (const item of list) {
        const before = previous.current.get(item.jobId);
        if (before && isActiveStatus(before) && !isActiveStatus(item.status)) {
          messages.push(`${item.title}: ${FINISH_TEXT[item.status] ?? item.status}`);
          listeners.current.forEach((listener) => listener({ meetingId: item.meetingId, status: item.status }));
        }
      }
      previous.current = new Map(list.map((item) => [item.jobId, item.status]));
      if (messages.length > 0) setAnnouncement(messages.join(" / "));
      setRaw(list.map((item) => ({ ...item, receivedAt })));
    } catch (error) {
      if (isAbortError(error)) return;
      // 조회 실패(서버 없음 등)는 조용히 넘어간다: 이전 목록을 그대로 두고 다음 주기에 다시 시도
    }
  }, [accountId]);

  // 계정이 정해지면(로그인 직후·새로고침) 닫은 목록을 읽고 한 번 조회한다. 계정이 없어지면(로그아웃) 비우고 요청을 취소한다
  useEffect(() => {
    previous.current = new Map();
    if (accountId === null) {
      setRaw([]);
      setDismissed([]);
      return;
    }
    setDismissed(readDismissed(accountId));
    void refresh();
    return () => controllerRef.current?.abort();
  }, [accountId, refresh]);

  // 처리 중인 항목이 있는 동안만 주기적으로 조회
  const hasActive = raw.some((item) => isActiveStatus(item.status));
  useEffect(() => {
    if (!hasActive) return;
    const timer = setInterval(() => void refresh(), PROCESSING_REFRESH_MS);
    return () => clearInterval(timer);
  }, [hasActive, refresh]);

  const dismiss = useCallback(
    (jobId: number) => {
      setDismissed((prev) => {
        const next = prev.includes(jobId) ? prev : [...prev, jobId];
        if (accountId !== null) writeDismissed(accountId, next);
        return next;
      });
    },
    [accountId],
  );

  const subscribe = useCallback((listener: (event: FinishedEvent) => void) => {
    listeners.current.add(listener);
    return () => {
      listeners.current.delete(listener);
    };
  }, []);

  const value = useMemo<ProcessingContextValue>(
    () => ({ items: raw.filter((item) => !dismissed.includes(item.jobId)), refresh: () => void refresh(), dismiss, subscribe, announcement }),
    [raw, dismissed, refresh, dismiss, subscribe, announcement],
  );
  return <ProcessingContext.Provider value={value}>{children}</ProcessingContext.Provider>;
}
