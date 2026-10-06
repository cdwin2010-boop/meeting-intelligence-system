"use client";

/*
 * 변경 이력 팝업(GET /api/meetings/{id}/history): 구분(직권 수정·업무 갱신·직권 등록)·변경자·시각·변경 전·후.
 * - 서버가 쪽 인자를 지원하지 않아(최신순 최대 200건) 받은 목록을 화면에서 20건 단위로 나눈다. 길면 팝업 안에서 스크롤한다.
 * - 같은 업로드 묶음(batchId)은 한 묶음으로 묶어 보여 준다(묶음 하나가 한 단위).
 * - 열 때마다 새로 불러온다. 닫으면 진행 중인 요청을 취소한다.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge, Button, Modal, StatusDot } from "@/components/mono";
import { formatDateTime } from "@/components/v2/meeting-display";
import { isAbortError } from "@/lib/v2/errors";
import { getHistory, MINUTES_LABEL, type HistoryEntry, type MinutesField } from "@/lib/v2/meetings";

export const HISTORY_PAGE_SIZE = 20;

const errorMessage = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

/** 변경 칸 이름 → 표시 이름 */
const KEY_LABEL: Record<string, string> = {
  title: "업무명",
  assigneeId: "담당자",
  dueDate: "기한",
  dueUndetermined: "기한 미확정",
  participants: "참석자",
  next_agenda: "다음 안건",
  ...MINUTES_LABEL,
};

/** 변경 값 → 글자 */
export function formatValue(key: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.length > 0 ? value.join(", ") : "—";
  if (key === "assigneeId") return `계정 #${String(value)}`;
  if (key === "dueUndetermined") return value ? "예" : "아니오";
  return String(value);
}

type Unit = { batchId: string | null; entries: HistoryEntry[] };

/** 같은 batchId 가 이어진 이력은 한 묶음, 나머지는 한 건씩 */
export function groupHistory(entries: HistoryEntry[]): Unit[] {
  const units: Unit[] = [];
  for (const entry of entries) {
    const last = units[units.length - 1];
    if (entry.batchId && last && last.batchId === entry.batchId) last.entries.push(entry);
    else units.push({ batchId: entry.batchId ?? null, entries: [entry] });
  }
  return units;
}

function targetLabel(entry: HistoryEntry): string {
  return entry.targetType === "action_item" ? `업무 #${entry.targetId}` : "회의록";
}

function EntryView({ entry }: { entry: HistoryEntry }) {
  const keys = Array.from(new Set([...Object.keys(entry.before ?? {}), ...Object.keys(entry.after ?? {})]));
  return (
    <li className="flex flex-col gap-2 py-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge>{entry.kind}</Badge>
        <span>{targetLabel(entry)}</span>
        <span className="text-mn-muted">· {entry.changedBy?.name ?? "시스템"}</span>
        <span className="font-mn-mono text-[13px] text-mn-muted">{formatDateTime(entry.changedAt)}</span>
      </div>
      <dl className="flex flex-col gap-2">
        {keys.map((key) => (
          <div key={key} className="text-sm">
            <dt className="text-xs text-mn-muted">{KEY_LABEL[key as MinutesField] ?? KEY_LABEL[key] ?? key}</dt>
            <dd className="mt-1 grid grid-cols-1 gap-2 sm:grid-cols-2">
              <span className="whitespace-pre-line">
                <span className="text-mn-muted">변경 전 </span>
                {formatValue(key, entry.before?.[key])}
              </span>
              <span className="whitespace-pre-line">
                <span className="text-mn-muted">변경 후 </span>
                {formatValue(key, entry.after?.[key])}
              </span>
            </dd>
          </div>
        ))}
      </dl>
    </li>
  );
}

type State = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; entries: HistoryEntry[] };

export function HistoryDialog({ meetingId, open, onClose }: { meetingId: number; open: boolean; onClose: () => void }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [page, setPage] = useState(1);
  const runRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    runRef.current?.abort();
    const controller = new AbortController();
    runRef.current = controller;
    setState({ kind: "loading" });
    try {
      setState({ kind: "ready", entries: await getHistory(meetingId, controller.signal) });
    } catch (error) {
      if (isAbortError(error)) return;
      setState({ kind: "error", message: errorMessage(error, "변경 이력을 불러오지 못했습니다.") });
    }
  }, [meetingId]);

  useEffect(() => {
    if (!open) return;
    setPage(1);
    void load();
    return () => runRef.current?.abort();
  }, [open, load]);

  const units = state.kind === "ready" ? groupHistory(state.entries) : [];
  const pages = Math.max(1, Math.ceil(units.length / HISTORY_PAGE_SIZE));
  const shown = units.slice((page - 1) * HISTORY_PAGE_SIZE, page * HISTORY_PAGE_SIZE);

  return (
    <Modal open={open} onClose={onClose} title="변경 이력" panelClassName="w-full max-w-2xl">
      <div className="mt-3 flex flex-col gap-3">
        <div className="flex justify-end">
          <Button size="sm" onClick={onClose}>
            닫기
          </Button>
        </div>
        <div className="max-h-[60vh] overflow-y-auto pr-1">
          {state.kind === "loading" ? (
            <p role="status" className="text-sm text-mn-muted">
              변경 이력을 불러오는 중…
            </p>
          ) : state.kind === "error" ? (
            <div role="alert" className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-sm">
                <StatusDot tone="error" label={`변경 이력을 불러오지 못했습니다 · ${state.message}`} />
              </span>
              <Button size="sm" onClick={() => void load()}>
                다시 시도
              </Button>
            </div>
          ) : units.length === 0 ? (
            <p className="text-sm text-mn-muted">변경 이력이 없습니다.</p>
          ) : (
            <ul className="flex flex-col divide-y divide-mn-border">
              {shown.map((unit) =>
                unit.batchId && unit.entries.length > 0 ? (
                  <li key={`${unit.batchId}-${unit.entries[0].id}`} className="py-3">
                    <div aria-label="같은 업로드 묶음" className="rounded-mn-card border border-mn-border p-3">
                      <p className="text-sm font-medium">
                        수정 회의록 업로드 <span className="font-normal text-mn-muted">· {unit.entries.length}건 한 묶음</span>
                      </p>
                      <ul className="mt-1 flex flex-col divide-y divide-mn-border">
                        {unit.entries.map((entry) => (
                          <EntryView key={entry.id} entry={entry} />
                        ))}
                      </ul>
                    </div>
                  </li>
                ) : (
                  <EntryView key={unit.entries[0].id} entry={unit.entries[0]} />
                ),
              )}
            </ul>
          )}
        </div>
        {state.kind === "ready" && units.length > HISTORY_PAGE_SIZE ? (
          <nav aria-label="변경 이력 쪽" className="flex items-center justify-between gap-3">
            <Button size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              이전
            </Button>
            <span className="font-mn-mono text-[13px] text-mn-muted">
              {page} / {pages}
            </span>
            <Button size="sm" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>
              다음
            </Button>
          </nav>
        ) : null}
      </div>
    </Modal>
  );
}
