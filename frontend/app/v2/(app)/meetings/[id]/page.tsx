"use client";

/*
 * v2 회의록 상세 (시안 docs/ui-v2-mockups/05-meeting-detail.html) — 조회 전용
 * - GET /api/meetings/{id}: 개요·요약·결정사항·업무 원장. 404(없음·권한 없음)는 "찾을 수 없음"으로 따로 보여 준다
 * - 전사문은 펼칠 때 처음 한 번 GET /api/meetings/{id}/transcript 로 불러온다(전사문 없음 404 도 안내)
 * - 근거 인용·타임스탬프는 글자로만 표시(오디오 재생 API 가 아직 없음). 확정·수정 같은 쓰기 버튼은 다음 단계
 * - 다른 회의록으로 바뀌거나 화면을 떠나면 이전 요청은 AbortController 로 취소한다
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";

import { Badge, Button, StatusDot, type StatusDotTone } from "@/components/mono";
import {
  CONFIRM_KIND_LABEL,
  formatDate,
  formatDateTime,
  formatOffset,
  meetingStatus,
  V2_MEETINGS_PATH,
} from "@/components/v2/meeting-display";
import { ApiError, isAbortError, type MissingField } from "@/lib/v2/errors";
import { getMeeting, getTranscript, type ActionItem, type MeetingDetail, type Transcript } from "@/lib/v2/meetings";

type LoadState =
  | { kind: "loading" }
  | { kind: "not_found" }
  | { kind: "error"; message: string }
  | { kind: "ready"; meeting: MeetingDetail };

type TranscriptState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "error"; message: string }
  | { kind: "ready"; transcript: Transcript };

const MISSING_LABEL: Record<MissingField, string> = {
  title: "업무명 필요",
  assignee: "담당자 필요",
  dueDate: "기한 필요",
};

const ORIGIN_LABEL: Record<string, string> = { audio: "음성", text: "텍스트" };

/** 업무 상태 → 점 색 + 글자 라벨. 확정 전 보완 필요는 빨간 점으로 먼저 알린다 */
function itemStatus(item: ActionItem): { tone: StatusDotTone; label: string } {
  if (item.status === "pending") return item.needsCompletion ? { tone: "error", label: "보완 필요" } : { tone: "queued", label: "확정 대기" };
  if (item.status === "confirmed") return { tone: "ready", label: "확정됨" };
  if (item.status === "closed") return { tone: "ready", label: "완료" };
  return { tone: "queued", label: String(item.status) };
}

/** 결정사항은 형식이 정해지지 않은 JSON 목록이라 글자로 읽을 수 있는 것만 보여 준다 */
function decisionText(value: unknown): string | null {
  if (typeof value === "string") return value.trim() || null;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    for (const key of ["text", "title", "content", "decision"]) {
      const v = record[key];
      if (typeof v === "string" && v.trim()) return v.trim();
    }
  }
  return null;
}

const notFoundStatus = (error: unknown) => error instanceof ApiError && error.status === 404;
const errorMessage = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

function Mono({ children, muted = false }: { children: ReactNode; muted?: boolean }) {
  return <span className={`whitespace-nowrap font-mn-mono text-[13px] ${muted ? "text-mn-muted" : ""}`}>{children}</span>;
}

function BackLink() {
  return (
    <Link href={V2_MEETINGS_PATH} className="mn-focus self-start rounded-mn-control text-sm text-mn-muted hover:text-mn-text">
      ← 회의록 목록으로
    </Link>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-mn-muted">{label}</dt>
      <dd className="mt-1 text-sm text-mn-text">{children}</dd>
    </div>
  );
}

function Overview({ meeting }: { meeting: MeetingDetail }) {
  const decisions = meeting.decisions.map(decisionText).filter((v): v is string => v !== null);
  const confirmed = meeting.status === "confirmed";
  return (
    <section aria-label="회의 개요" className="flex flex-col gap-5 rounded-mn-card border border-mn-border bg-mn-surface p-5">
      <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="회의 일시">
          <Mono>{formatDateTime(meeting.heldAt)}</Mono>
        </Field>
        <Field label="등록자">
          {meeting.registeredBy.name} · {ORIGIN_LABEL[meeting.origin] ?? meeting.origin}
        </Field>
        <Field label="참석자">{meeting.participants.length > 0 ? meeting.participants.map((p) => p.name).join(", ") : "—"}</Field>
        {confirmed ? (
          <Field label="확정">
            {meeting.confirmKind ? CONFIRM_KIND_LABEL[meeting.confirmKind] ?? meeting.confirmKind : "확정"}
            {meeting.confirmedBy ? ` · ${meeting.confirmedBy.name}` : ""} · <Mono>{formatDate(meeting.confirmedAt)}</Mono>
          </Field>
        ) : (
          <Field label="자동 확정 예정">
            <Mono>{formatDate(meeting.autoConfirmAt)}</Mono>
          </Field>
        )}
      </dl>
      <div>
        <h2 className="text-xs text-mn-muted">요약</h2>
        <p className="mt-2 whitespace-pre-line text-sm">{meeting.summary.trim() || <span className="text-mn-muted">요약이 없습니다.</span>}</p>
      </div>
      <div>
        <h2 className="text-xs text-mn-muted">결정사항</h2>
        {decisions.length > 0 ? (
          <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm">
            {decisions.map((text, index) => (
              <li key={index}>{text}</li>
            ))}
          </ol>
        ) : (
          <p className="mt-2 text-sm text-mn-muted">결정사항이 없습니다.</p>
        )}
      </div>
    </section>
  );
}

function DueCell({ item }: { item: ActionItem }) {
  if (item.dueUndetermined) return <Badge>미확정</Badge>;
  if (!item.dueDate) return <span className="text-mn-muted">—</span>;
  return <Mono>{item.dueDate}</Mono>;
}

function LedgerTable({ items }: { items: ActionItem[] }) {
  const needsCount = items.filter((item) => item.needsCompletion).length;
  return (
    <section aria-label="업무 원장" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold tracking-tight">업무 원장</h2>
        <span className="flex items-center gap-3 text-sm text-mn-muted">
          <span>
            <span className="font-mn-mono">{items.length}</span>건
          </span>
          {needsCount > 0 ? <StatusDot tone="error" label={`보완 필요 ${needsCount}건`} /> : null}
        </span>
      </div>
      <div className="overflow-x-auto rounded-mn-card border border-mn-border bg-mn-surface">
        <table aria-label="업무 원장" className="w-full min-w-[880px] table-fixed border-collapse text-sm">
          <colgroup>
            <col className="w-[120px]" />
            <col />
            <col className="w-[120px]" />
            <col className="w-[128px]" />
            <col className="w-[160px]" />
            <col className="w-[240px]" />
          </colgroup>
          <thead>
            <tr className="h-10 border-b border-mn-border text-left text-xs text-mn-muted">
              <th scope="col" className="px-4 font-medium">상태</th>
              <th scope="col" className="px-4 font-medium">업무명</th>
              <th scope="col" className="px-4 font-medium">담당자</th>
              <th scope="col" className="px-4 font-medium">완료 기한</th>
              <th scope="col" className="px-4 font-medium">보완 필요</th>
              <th scope="col" className="px-4 font-medium">근거</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr className="h-12">
                <td colSpan={6} className="px-4 text-center text-mn-muted">
                  추출된 업무가 없습니다.
                </td>
              </tr>
            ) : (
              items.map((item) => {
                const status = itemStatus(item);
                return (
                  <tr key={item.id} className="h-12 border-b border-mn-border align-middle last:border-b-0">
                    <td className="px-4">
                      <StatusDot tone={status.tone} label={status.label} />
                    </td>
                    <td className="px-4 py-3">{item.title || <span className="text-mn-muted">(업무명 없음)</span>}</td>
                    <td className="px-4">{item.assignee?.name ?? <span className="text-mn-muted">—</span>}</td>
                    <td className="px-4">
                      <DueCell item={item} />
                    </td>
                    <td className="px-4 py-3">
                      {item.needsCompletion && item.missingFields.length > 0 ? (
                        <span className="flex flex-col gap-1">
                          {item.missingFields.map((field) => (
                            <StatusDot key={field} tone="error" label={MISSING_LABEL[field] ?? field} />
                          ))}
                        </span>
                      ) : (
                        <span className="text-mn-muted">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {item.evidenceStartSec !== null || item.evidenceQuote ? (
                        <span className="flex flex-col gap-1">
                          {item.evidenceStartSec !== null ? <Mono muted>{formatOffset(item.evidenceStartSec)}</Mono> : null}
                          {item.evidenceQuote ? <q className="text-xs text-mn-muted">{item.evidenceQuote}</q> : null}
                        </span>
                      ) : (
                        <span className="text-mn-muted">—</span>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function TranscriptBody({ state, onRetry }: { state: TranscriptState; onRetry: () => void }) {
  switch (state.kind) {
    case "idle":
    case "loading":
      return (
        <p role="status" className="text-sm text-mn-muted">
          전사문을 불러오는 중…
        </p>
      );
    case "missing":
      return <p className="text-sm text-mn-muted">전사문이 없습니다.</p>;
    case "error":
      return (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-sm">
            <StatusDot tone="error" label={`전사문을 불러오지 못했습니다 · ${state.message}`} />
          </span>
          <Button size="sm" onClick={onRetry}>
            다시 시도
          </Button>
        </div>
      );
    case "ready": {
      const segments = (state.transcript.segments ?? []).filter((s) => (s.text ?? "").trim());
      // 구간이 있으면 시각·화자별로, 없으면 원문 전체를 그대로
      if (segments.length === 0) {
        return state.transcript.fullText.trim() ? (
          <p className="whitespace-pre-wrap text-sm leading-6">{state.transcript.fullText}</p>
        ) : (
          <p className="text-sm text-mn-muted">전사문 내용이 비어 있습니다.</p>
        );
      }
      return (
        <ol className="flex flex-col gap-3">
          {segments.map((segment, index) => (
            <li key={index} className="flex gap-4 text-sm">
              <Mono muted>{formatOffset(segment.start_sec)}</Mono>
              <div className="min-w-0">
                {segment.speaker ? <p className="text-xs text-mn-muted">{segment.speaker}</p> : null}
                <p className="leading-6">{segment.text}</p>
              </div>
            </li>
          ))}
        </ol>
      );
    }
  }
}

/** 전사문 접고 펼치기. 처음 펼칠 때만 불러온다 */
function TranscriptPanel({ meetingId }: { meetingId: number }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<TranscriptState>({ kind: "idle" });
  const controllerRef = useRef<AbortController | null>(null);
  const panelId = useId();

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ kind: "loading" });
    try {
      setState({ kind: "ready", transcript: await getTranscript(meetingId, controller.signal) });
    } catch (error) {
      if (isAbortError(error)) return;
      if (notFoundStatus(error)) setState({ kind: "missing" });
      else setState({ kind: "error", message: errorMessage(error, "전사문을 불러오지 못했습니다.") });
    }
  }, [meetingId]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next && state.kind === "idle") void load();
  }

  return (
    <section aria-label="전사문" className="rounded-mn-card border border-mn-border bg-mn-surface">
      <h2>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={toggle}
          className="mn-focus flex h-12 w-full items-center justify-between rounded-mn-card px-5 text-left text-base font-semibold tracking-tight"
        >
          전사문
          <span className="text-sm font-normal text-mn-muted">{open ? "접기" : "펼치기"}</span>
        </button>
      </h2>
      {open ? (
        <div id={panelId} className="max-h-[480px] overflow-y-auto border-t border-mn-border px-5 py-4">
          <TranscriptBody state={state} onRetry={() => void load()} />
        </div>
      ) : null}
    </section>
  );
}

export default function V2MeetingDetailPage() {
  const params = useParams<{ id: string }>();
  const rawId = params?.id ?? "";
  // 숫자가 아닌 주소는 서버에 묻지 않고 바로 "찾을 수 없음"
  const meetingId = /^\d+$/.test(rawId) ? Number(rawId) : null;

  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    if (meetingId === null) {
      setState({ kind: "not_found" });
      return;
    }
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ kind: "loading" });
    try {
      setState({ kind: "ready", meeting: await getMeeting(meetingId, controller.signal) });
    } catch (error) {
      if (isAbortError(error)) return;
      if (notFoundStatus(error)) setState({ kind: "not_found" });
      else setState({ kind: "error", message: errorMessage(error, "회의록을 불러오지 못했습니다.") });
    }
  }, [meetingId]);

  useEffect(() => {
    void load();
    return () => controllerRef.current?.abort();
  }, [load]);

  if (state.kind === "loading") {
    return (
      <>
        <BackLink />
        <p role="status" className="text-sm text-mn-muted">
          회의록을 불러오는 중…
        </p>
      </>
    );
  }
  if (state.kind === "not_found") {
    return (
      <>
        <BackLink />
        <h1 className="text-[28px] font-semibold leading-9 tracking-tight text-mn-text">회의록을 찾을 수 없습니다</h1>
        <p className="text-sm text-mn-muted">삭제되었거나 열람 권한이 없는 회의록입니다.</p>
      </>
    );
  }
  if (state.kind === "error") {
    return (
      <>
        <BackLink />
        <div role="alert" className="flex flex-wrap items-center justify-between gap-4 rounded-mn-card border border-mn-border bg-mn-surface p-6">
          <span className="text-sm">
            <StatusDot tone="error" label={`회의록을 불러오지 못했습니다 · ${state.message}`} />
          </span>
          <Button size="sm" onClick={() => void load()}>
            다시 시도
          </Button>
        </div>
      </>
    );
  }

  const { meeting } = state;
  const status = meetingStatus(meeting);
  return (
    <>
      <BackLink />
      <header className="flex flex-col gap-2">
        <h1 className="text-[28px] font-semibold leading-9 tracking-tight text-mn-text">{meeting.title || "(제목 없음)"}</h1>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <StatusDot tone={status.tone} label={status.label} />
          <Mono muted>{formatDateTime(meeting.heldAt)}</Mono>
        </div>
      </header>
      <Overview meeting={meeting} />
      <LedgerTable items={meeting.actionItems} />
      <TranscriptPanel key={meeting.id} meetingId={meeting.id} />
    </>
  );
}
