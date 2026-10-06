"use client";

/*
 * v2 회의록 상세 (시안 docs/ui-v2-mockups/05-meeting-detail.html)
 * - GET /api/meetings/{id}: 개요·요약·결정사항·업무 원장. 404(없음·권한 없음)는 "찾을 수 없음"으로 따로 보여 준다
 * - 전사문은 펼칠 때 처음 한 번 GET /api/meetings/{id}/transcript 로 불러온다(전사문 없음 404 도 안내)
 * - 근거 인용·타임스탬프는 글자로만 표시(오디오 재생 API 가 아직 없음). 업무명 수정 버튼은 다음 단계
 * - 기한 칸(DueEditor): 날짜 선택 또는 "미확정"으로 PATCH. 재개된 회의록은 기한이 빈 업무 건수 안내를 머리 아래에 보여 준다
 * - 확정 대기 회의록은 머리에 "회의록 확정"(MeetingConfirmButton, 회의록만 확정). 업무는 원장 행마다 "확정"(확정 전 업무만)
 * - 상세 응답에 권한 값이 없어 확정 버튼은 모두에게 보이고, 거부(403·409 보완 필요 등)는 서버 문구를 그대로 보여 준다
 * - 수정 요청: 업무 행 "수정 요청"·머리 오른쪽 "회의록 전체 수정 요청" 버튼이 팝업(ChangeRequestDialog)을 연다(상태·확정 시계 변화 없음).
 *   본문에 수정 요청 영역은 두지 않는다. 목록은 useChangeRequests 로 한 번 받아 팝업 내역이 쓰고, 남긴 뒤에만 다시 받는다(팝업은 열린 채).
 *   팝업 내역에서 답변·해결(수락·반려)하면 응답으로 그 요청만 바꾼다
 * - 업무 원장 담당자 칸의 지정/변경(AssigneeDialog): 저장 성공 시 서버가 준 업무로 그 행만 바꾼다(권한 판정은 서버)
 * - 화자 지정: 개요의 참석자 줄 "화자 지정" 버튼이 팝업(SpeakerDialog)을 연다. 지정된 화자 이름은 같은 줄에 한 줄 요약
 *   (GET /api/meetings/{id}/speakers 를 회의록마다 한 번, 조회 실패·권한 없음이면 요약만 숨김). 저장하면 저장 응답으로 요약을 바꾸고
 *   업무 원장(상세 재조회)과 전사문(displayText)을 새로 받은 뒤 팝업이 닫힌다
 * - 전사문 보기: fullText·segments 는 원본, displayText·speakers 는 이름 적용본(서버 응답 그대로). 지정된 화자가 없으면 원본만,
 *   있으면 기본 이름 적용본과 "원본 보기"/"이름 적용본 보기" 전환. 화자를 저장하면 적용본으로 돌아온다
 * - 단계(진행중·종료·보류·삭제)는 제목 줄에 글자 라벨(종료는 자동 종료/관리자 직권 종료 구분까지). 보류·종료·삭제 기록이 있으면
 *   "처리 기록"에 처리자·시각·사유를 읽기 전용으로 보여 준다(서버 응답 그대로, 없으면 칸 생략)
 * - 보류·종료·삭제된 회의록의 쓰기 버튼은 그대로 두고, 서버가 409 로 거부하면 각 버튼이 서버 문구를 그대로 보여 준다
 * - 관리자 이상(로그인 계정 직급)에게만: 제목 줄 MeetingActions(보류·직권 종료·삭제 메뉴, 보류 중 재개)와 업무 행 "종결"(확정된 업무만)·"삭제".
 *   모두 사유 입력 확인 팝업(ReasonDialog, 재개는 사유 없음)을 거치고, 성공하면 상세·수정 요청을 다시 받아 새 상태로 바꾼다.
 *   실제 권한(지시자·총괄 등)은 서버 판정, 거부(403·409·422)는 팝업 안에 서버 문구
 * - 음성 재생: 개요 아래 한 줄 재생기(AudioPlayer). 근거 타임스탬프·전사문 구간 시각을 누르면 그 위치부터 재생(SeekTime).
 *   재생 주소는 약 10분 유효 서명 주소(GET /meetings/{id}/audio-url), 음성이 없거나 발급이 거부되면 서버 문구와 "음성 파일이 없습니다"
 * - 회의록 5개 항목(MinutesPanel): 요약·결정사항 자리를 대체. "항목 수정"(관리자 이상)은 MinutesDialog. 제목 줄 ⋯ 메뉴: 엑셀 다운로드·변경 이력(전원),
 *   수정 회의록 업로드(관리자 이상, UploadUpdateDialog). 업무 원장 머리 "업무 추가"(관리자 이상, ManualItemDialog), 수기 업무는 "수기" 배지·직권 지정 근거
 * - 다른 회의록으로 바뀌거나 화면을 떠나면 이전 요청은 AbortController 로 취소한다
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";

import { Badge, Button, StatusDot, type StatusDotTone } from "@/components/mono";
import {
  CONFIRM_KIND_LABEL,
  END_KIND_LABEL,
  formatDate,
  formatDateTime,
  formatOffset,
  meetingStatus,
  phaseText,
  V2_MEETINGS_PATH,
} from "@/components/v2/meeting-display";
import { DueEditor } from "@/components/v2/DueEditor";
import { AudioPlayer, type SeekRequest } from "@/components/v2/AudioPlayer";
import { HistoryDialog } from "@/components/v2/HistoryDialog";
import { ManualItemDialog } from "@/components/v2/ManualItemDialog";
import { ProcessingBanner } from "@/components/v2/ProcessingBanner";
import { useProcessingFinished } from "@/components/v2/ProcessingProvider";
import { MinutesDialog, MinutesPanel } from "@/components/v2/MinutesPanel";
import { UploadUpdateDialog } from "@/components/v2/UploadUpdateDialog";
import { AssigneeDialog } from "@/components/v2/AssigneeDialog";
import { ChangeRequestDialog, type ChangeRequestTarget } from "@/components/v2/ChangeRequestDialog";
import { useChangeRequests } from "@/components/v2/ChangeRequestHistory";
import { MeetingActions, type MeetingActionKind } from "@/components/v2/MeetingActions";
import { MeetingConfirmButton } from "@/components/v2/MeetingConfirmButton";
import { ReasonDialog, type ReasonAction } from "@/components/v2/ReasonDialog";
import { SpeakerDialog } from "@/components/v2/SpeakerDialog";
import { ApiError, isAbortError, type MissingField } from "@/lib/v2/errors";
import { closeActionItem, confirmActionItem, deleteActionItem } from "@/lib/v2/action-items";
import { can, ITEM_ACTION, MEETING_ACTION } from "@/lib/v2/actions";
import { FAKE_MEETING_NOTICE, PROCESSING_REFRESH_MS } from "@/lib/v2/system";
import {
  deleteMeeting,
  downloadMeetingExcel,
  endMeeting,
  getMeeting,
  getSpeakers,
  holdMeeting,
  reprocessMeeting,
  resumeMeeting,
  getTranscript,
  type ActionItem,
  type Minutes,
  type MeetingConfirmResult,
  type MeetingDetail,
  type SpeakerMapping,
  type SpeakersResponse,
  type Transcript,
} from "@/lib/v2/meetings";

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

const ORIGIN_LABEL: Record<string, string> = { audio_minutes: "음성", audio: "음성", text: "텍스트" };

/** 업무 상태 → 점 색 + 글자 라벨. 확정 전 보완 필요는 빨간 점으로 먼저 알린다 */
function itemStatus(item: ActionItem): { tone: StatusDotTone; label: string } {
  if (item.status === "pending") return item.needsCompletion ? { tone: "error", label: "보완 필요" } : { tone: "queued", label: "확정 대기" };
  if (item.status === "confirmed") return { tone: "ready", label: "확정됨" };
  if (item.status === "closed") return { tone: "ready", label: "종결" };
  return { tone: "queued", label: String(item.status) };
}

/** 결정사항은 형식이 정해지지 않은 JSON 목록이라 글자로 읽을 수 있는 것만 보여 준다 */
/** 내려받는 엑셀 파일명 "{회의명}_회의록.xlsx": 파일명에 쓸 수 없는 문자(경로·제어 문자 등)는 지운다 */
function excelFileName(title: string): string {
  // eslint-disable-next-line no-control-regex
  const safe = title.replace(/[\\/:*?"<>|\u0000-\u001f]/g, "").replace(/\s+/g, " ").trim().replace(/^\.+|\.+$/g, "");
  return `${safe || "회의록"}_회의록.xlsx`;
}

/** 참석자 이름 한 줄: 계정 참석자 + 계정이 없는 참석자는 "이름(미등록)" */
function participantNames(meeting: MeetingDetail): string {
  return [...meeting.participants.map((p) => p.name), ...(meeting.guestParticipants ?? []).map((g) => `${g}(미등록)`)].join(", ");
}

const notFoundStatus = (error: unknown) => error instanceof ApiError && error.status === 404;
const errorMessage = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

function Mono({ children, muted = false }: { children: ReactNode; muted?: boolean }) {
  return <span className={`whitespace-nowrap font-mn-mono text-[13px] ${muted ? "text-mn-muted" : ""}`}>{children}</span>;
}

/** 누르면 그 위치부터 재생하는 시각(Geist Mono). 시각 값이 없으면 "—" */
function SeekTime({ sec, onSeek }: { sec: number | null | undefined; onSeek: (sec: number) => void }) {
  if (sec === null || sec === undefined || !Number.isFinite(sec) || sec < 0) return <Mono muted>—</Mono>;
  return (
    <button
      type="button"
      aria-label={`${formatOffset(sec)}부터 재생`}
      onClick={() => onSeek(sec)}
      className="mn-focus whitespace-nowrap rounded-mn-control font-mn-mono text-[13px] text-mn-muted underline decoration-dotted underline-offset-4 hover:text-mn-text"
    >
      {formatOffset(sec)}
    </button>
  );
}

/** 이 회의록에 기록된 처리 엔진이 fake 일 때 안내(색만으로 구분하지 않고 글자로) */
function FakeMeetingNotice({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <p role="status" aria-label="회의록 처리 엔진 안내" className="rounded-mn-card border border-mn-border bg-mn-surface px-4 py-3 text-sm">
      <StatusDot tone="queued" label={FAKE_MEETING_NOTICE} />
    </p>
  );
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

interface SpeakerSummaryProps {
  /** 저장된 화자 매핑. null 이면(조회 전·실패·권한 없음) 요약을 보이지 않는다 */
  speakers: SpeakerMapping[] | null;
  notice: string | null;
  /** 화자 지정 허용(edit_speakers) */
  canEdit: boolean;
  onOpen: () => void;
}

/** 참석자 줄 아래: "화자 지정" 버튼 + 지정된 화자 이름 한 줄 요약 + 저장 결과 안내 */
function SpeakerSummary({ speakers, notice, canEdit, onOpen }: SpeakerSummaryProps) {
  const names = (speakers ?? []).map((sp) => sp.displayName).filter(Boolean);
  return (
    <span className="mt-2 flex flex-col gap-1">
      <span className="flex min-w-0 items-center gap-2">
        {canEdit ? (
          <Button size="sm" onClick={onOpen} className="shrink-0">
            화자 지정
          </Button>
        ) : null}
        {names.length > 0 ? (
          <span aria-label="지정된 화자" title={names.join(", ")} className="min-w-0 truncate text-xs text-mn-muted">
            화자 {names.join(", ")}
          </span>
        ) : null}
      </span>
      {notice ? (
        <span aria-live="polite" className="text-xs">
          <StatusDot tone="ready" label={notice} />
        </span>
      ) : null}
    </span>
  );
}

function Overview({ meeting, speakerSummary, minutesPanel }: { meeting: MeetingDetail; speakerSummary: ReactNode; minutesPanel: ReactNode }) {
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
        <Field label="참석자">
          <span className="block">{participantNames(meeting) || "—"}</span>
          {speakerSummary}
        </Field>
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
      {minutesPanel}
    </section>
  );
}

interface PhaseRecord {
  label: string;
  by: string | null;
  at: string | null | undefined;
  reason: string | null | undefined;
}

/** 보류·재개·종료·삭제 기록(처리자·시각·사유). 서버가 준 값만, 하나도 없으면 그리지 않는다 */
function PhaseRecords({ meeting }: { meeting: MeetingDetail }) {
  const records: PhaseRecord[] = [];
  if (meeting.onHoldAt) {
    records.push({ label: meeting.onHold ? "보류" : "보류(재개됨)", by: meeting.onHoldBy?.name ?? null, at: meeting.onHoldAt, reason: meeting.onHoldReason });
  }
  if (meeting.resumedAt) {
    records.push({ label: "재개", by: meeting.resumedBy?.name ?? null, at: meeting.resumedAt, reason: null });
  }
  if (meeting.endedAt) {
    const kind = meeting.endKind ? END_KIND_LABEL[meeting.endKind] ?? meeting.endKind : null;
    // 자동 종료는 처리자 없음
    records.push({
      label: kind ? `종료 · ${kind}` : "종료",
      by: meeting.endedBy?.name ?? (meeting.endKind === "auto" ? "자동" : null),
      at: meeting.endedAt,
      reason: meeting.endReason,
    });
  }
  if (meeting.deletedAt) {
    records.push({ label: "삭제", by: meeting.deletedBy?.name ?? null, at: meeting.deletedAt, reason: meeting.deleteReason });
  }
  if (records.length === 0) return null;
  return (
    <section aria-label="처리 기록" className="rounded-mn-card border border-mn-border bg-mn-surface p-5">
      <h2 className="text-xs text-mn-muted">처리 기록</h2>
      <ul className="mt-2 flex flex-col divide-y divide-mn-border">
        {records.map((record) => (
          <li key={record.label} className="flex flex-col gap-1 py-2 text-sm">
            <span className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{record.label}</span>
              {record.by ? <span className="text-mn-muted">{record.by}</span> : null}
              {record.at ? <Mono muted>{formatDateTime(record.at)}</Mono> : null}
            </span>
            {record.reason ? (
              <span className="whitespace-pre-wrap">
                <span className="text-mn-muted">사유 </span>
                {record.reason}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

interface LedgerTableProps {
  items: ActionItem[];
  onEditAssignee: (item: ActionItem) => void;
  onItemConfirmed: (item: ActionItem) => void;
  /** 기한 저장 응답으로 그 행만 바꾼다 */
  onItemUpdated: (item: ActionItem) => void;
  /** 근거 타임스탬프를 누르면 그 위치부터 재생 */
  onSeek: (sec: number) => void;
  /** 관리자 이상이면 머리의 "업무 추가"(수기 등록) */
  onAddItem: () => void;
  onRequestChange: (item: ActionItem) => void;
  /** 관리자 이상이면 종결(확정된 업무만)·삭제 버튼을 그린다(권한 판정은 서버) */
  /** 서버가 내려준 회의록 단위 허용 동작(업무 추가 버튼 판정). 업무 행은 각 업무의 allowedActions 를 쓴다 */
  allowed: readonly string[] | undefined;
  onCloseItem: (item: ActionItem) => void;
  onDeleteItem: (item: ActionItem) => void;
}

function LedgerTable({ items, onEditAssignee, onItemConfirmed, onItemUpdated, onSeek, onAddItem, onRequestChange, allowed, onCloseItem, onDeleteItem }: LedgerTableProps) {
  const needsCount = items.filter((item) => item.needsCompletion).length;
  // 종결·삭제 버튼이 보이는 업무가 하나라도 있으면 동작 열을 넓힌다
  const wideActions = items.some((it) => can(it.allowedActions, ITEM_ACTION.closeItem) || can(it.allowedActions, ITEM_ACTION.deleteItem));
  // 업무 확정: 한 번에 한 건. 거부되면 서버 문구(보완 필요 409·권한 403 등)를 원장 위에 보여 준다
  const [confirmingId, setConfirmingId] = useState<number | null>(null);
  const [notice, setNotice] = useState<{ tone: "ready" | "error"; text: string } | null>(null);
  const confirmRef = useRef<AbortController | null>(null);
  useEffect(() => () => confirmRef.current?.abort(), []);

  async function confirmItem(item: ActionItem) {
    if (confirmingId !== null) return;
    const controller = new AbortController();
    confirmRef.current = controller;
    setConfirmingId(item.id);
    setNotice(null);
    const name = item.title || "(업무명 없음)";
    try {
      const saved = await confirmActionItem(item.id, controller.signal);
      onItemConfirmed(saved);
      setNotice({ tone: "ready", text: `${name}: 확정했습니다` });
    } catch (error) {
      if (isAbortError(error)) return;
      setNotice({ tone: "error", text: `${name}: 확정하지 못했습니다 · ${errorMessage(error, "서버 오류")}` });
    } finally {
      setConfirmingId(null);
    }
  }

  return (
    <section aria-label="업무 원장" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold tracking-tight">업무 원장</h2>
        <span className="flex items-center gap-3 text-sm text-mn-muted">
          {can(allowed, MEETING_ACTION.addItem) ? (
            <Button size="sm" onClick={onAddItem}>
              업무 추가
            </Button>
          ) : null}
          <span>
            <span className="font-mn-mono">{items.length}</span>건
          </span>
          {needsCount > 0 ? <StatusDot tone="error" label={`보완 필요 ${needsCount}건`} /> : null}
        </span>
      </div>
      <div aria-live="polite" className="text-sm empty:hidden">
        {notice ? (
          notice.tone === "error" ? (
            <span role="alert">
              <StatusDot tone="error" label={notice.text} />
            </span>
          ) : (
            <StatusDot tone="ready" label={notice.text} />
          )
        ) : null}
      </div>
      <div className="overflow-x-auto rounded-mn-card border border-mn-border bg-mn-surface">
        <table aria-label="업무 원장" className="w-full min-w-[1220px] table-fixed border-collapse text-sm">
          <colgroup>
            <col className="w-[120px]" />
            <col />
            <col className="w-[180px]" />
            <col className="w-[210px]" />
            <col className="w-[160px]" />
            <col className="w-[240px]" />
            <col className={wideActions ? "w-[320px]" : "w-[200px]"} />
          </colgroup>
          <thead>
            <tr className="h-10 border-b border-mn-border text-left text-xs text-mn-muted">
              <th scope="col" className="px-4 font-medium">상태</th>
              <th scope="col" className="px-4 font-medium">업무명</th>
              <th scope="col" className="px-4 font-medium">담당자</th>
              <th scope="col" className="px-4 font-medium">완료 기한</th>
              <th scope="col" className="px-4 font-medium">보완 필요</th>
              <th scope="col" className="px-4 font-medium">근거</th>
              <th scope="col" className="px-4 font-medium">동작</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr className="h-12">
                <td colSpan={7} className="px-4 text-center text-mn-muted">
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
                    <td className="px-4 py-3">
                      {item.origin === "manual" ? (
                        <span className="mr-2 inline-block align-middle">
                          <Badge>수기</Badge>
                        </span>
                      ) : null}
                      {item.title || <span className="text-mn-muted">(업무명 없음)</span>}
                    </td>
                    <td className="px-4">
                      <span className="flex items-center justify-between gap-2">
                        <span className="min-w-0 truncate">{item.assignee?.name ?? <span className="text-mn-muted">—</span>}</span>
                        {can(item.allowedActions, ITEM_ACTION.setAssignee) ? (
                          <Button
                            size="sm"
                            aria-label={`${item.title || "업무명 없음"} 담당자 ${item.assignee ? "변경" : "지정"}`}
                            onClick={() => onEditAssignee(item)}
                          >
                            {item.assignee ? "변경" : "지정"}
                          </Button>
                        ) : null}
                      </span>
                    </td>
                    <td className="px-4">
                      <DueEditor item={item} editable={can(item.allowedActions, ITEM_ACTION.setDue)} onSaved={onItemUpdated} />
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
                          {item.evidenceStartSec !== null ? <SeekTime sec={item.evidenceStartSec} onSeek={onSeek} /> : null}
                          {item.evidenceQuote ? (
                            item.origin === "manual" ? (
                              <span className="text-xs text-mn-muted">{item.evidenceQuote}</span>
                            ) : (
                              <q className="text-xs text-mn-muted">{item.evidenceQuote}</q>
                            )
                          ) : null}
                        </span>
                      ) : (
                        <span className="text-mn-muted">—</span>
                      )}
                    </td>
                    <td className="px-4">
                      <span className="flex items-center gap-2">
                        {can(item.allowedActions, ITEM_ACTION.confirmItem) ? (
                          <Button
                            size="sm"
                            disabled={confirmingId !== null}
                            aria-label={`${item.title || "업무명 없음"} 업무 확정`}
                            onClick={() => void confirmItem(item)}
                          >
                            {confirmingId === item.id ? "확정 중…" : "확정"}
                          </Button>
                        ) : null}
                        {can(item.allowedActions, ITEM_ACTION.requestChange) ? (
                          <Button
                            size="sm"
                            aria-label={`${item.title || "업무명 없음"} 수정 요청`}
                            onClick={() => onRequestChange(item)}
                          >
                            수정 요청
                          </Button>
                        ) : null}
                        {can(item.allowedActions, ITEM_ACTION.closeItem) ? (
                          <Button size="sm" aria-label={`${item.title || "업무명 없음"} 업무 종결`} onClick={() => onCloseItem(item)}>
                            종결
                          </Button>
                        ) : null}
                        {can(item.allowedActions, ITEM_ACTION.deleteItem) ? (
                          <Button size="sm" aria-label={`${item.title || "업무명 없음"} 업무 삭제`} onClick={() => onDeleteItem(item)}>
                            삭제
                          </Button>
                        ) : null}
                      </span>
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

type TranscriptView = "applied" | "original";

/** 지정된 화자가 있어야 이름 적용본이 의미가 있다 */
const hasSpeakers = (transcript: Transcript) => (transcript.speakers ?? []).length > 0;

function TranscriptBody({ state, view, onRetry, onSeek }: { state: TranscriptState; view: TranscriptView; onRetry: () => void; onSeek: (sec: number) => void }) {
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
      // 이름 적용본이면 화자 매핑 표시 이름(서버가 준 speakers 그대로)으로 구간의 화자 표기를 바꾸고, 원본이면 표기 그대로
      const applied = view === "applied";
      const names = new Map(applied ? (state.transcript.speakers ?? []).map((sp) => [sp.label, sp.displayName]) : []);
      // 구간이 있으면 시각·화자별로, 없으면 적용본은 displayText(없으면 원문), 원본은 fullText 를 그대로
      if (segments.length === 0) {
        const text = applied ? state.transcript.displayText ?? state.transcript.fullText : state.transcript.fullText;
        return text.trim() ? (
          <p className="whitespace-pre-wrap text-sm leading-6">{text}</p>
        ) : (
          <p className="text-sm text-mn-muted">전사문 내용이 비어 있습니다.</p>
        );
      }
      return (
        <ol className="flex flex-col gap-3">
          {segments.map((segment, index) => (
            <li key={index} className="flex gap-4 text-sm">
              <SeekTime sec={segment.start_sec} onSeek={onSeek} />
              <div className="min-w-0">
                {segment.speaker ? <p className="text-xs text-mn-muted">{names.get(segment.speaker) ?? segment.speaker}</p> : null}
                <p className="leading-6">{segment.text}</p>
              </div>
            </li>
          ))}
        </ol>
      );
    }
  }
}

/** 전사문 접고 펼치기. 처음 펼칠 때만 불러온다. version 이 바뀌면(화자 저장 등) 펼쳐져 있으면 다시, 접혀 있으면 다음에 펼칠 때 불러온다 */
function TranscriptPanel({ meetingId, version, onSeek }: { meetingId: number; version: number; onSeek: (sec: number) => void }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<TranscriptState>({ kind: "idle" });
  // 원하는 보기. 지정된 화자가 없으면 아래에서 원본으로 고정한다
  const [view, setView] = useState<TranscriptView>("applied");
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

  const openRef = useRef(open);
  openRef.current = open;
  const firstVersion = useRef(true);
  useEffect(() => {
    if (firstVersion.current) {
      firstVersion.current = false;
      return;
    }
    // 화자를 저장하면 이름 적용본 보기로 돌아온다
    setView("applied");
    if (openRef.current) void load();
    else {
      controllerRef.current?.abort();
      setState({ kind: "idle" });
    }
  }, [version, load]);

  const shownView: TranscriptView = state.kind === "ready" && hasSpeakers(state.transcript) ? view : "original";

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
        <div id={panelId} className="border-t border-mn-border">
          {state.kind === "ready" ? (
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-mn-border px-5 py-3 text-sm">
              <span aria-live="polite" className="text-mn-muted">
                현재 보기 <span className="font-medium text-mn-text">{shownView === "applied" ? "이름 적용본" : "원본"}</span>
              </span>
              {hasSpeakers(state.transcript) ? (
                <Button size="sm" onClick={() => setView(shownView === "applied" ? "original" : "applied")}>
                  {shownView === "applied" ? "원본 보기" : "이름 적용본 보기"}
                </Button>
              ) : null}
            </div>
          ) : null}
          <div className="max-h-[480px] overflow-y-auto px-5 py-4">
            <TranscriptBody state={state} view={shownView} onRetry={() => void load()} onSeek={onSeek} />
          </div>
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
  // 사유 입력 확인 팝업(종결·삭제·보류·재개·직권 종료 공용)
  const [reasonAction, setReasonAction] = useState<ReasonAction | null>(null);
  const [transcriptVersion, setTranscriptVersion] = useState(0);
  const [speakerOpen, setSpeakerOpen] = useState(false);
  const [speakers, setSpeakers] = useState<SpeakerMapping[] | null>(null);
  const [speakerNotice, setSpeakerNotice] = useState<string | null>(null);
  const [assigneeTarget, setAssigneeTarget] = useState<ActionItem | null>(null);
  const [changeTarget, setChangeTarget] = useState<ChangeRequestTarget | null>(null);
  const [seek, setSeek] = useState<SeekRequest | null>(null);
  // 5개 항목 수정·변경 이력·업로드 갱신·업무 추가 팝업, 그 결과 안내
  const [minutesOpen, setMinutesOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [minutesNotice, setMinutesNotice] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<{ tone: "ready" | "error"; text: string } | null>(null);
  const onSeek = useCallback((sec: number) => setSeek((prev) => ({ sec, nonce: (prev?.nonce ?? 0) + 1 })), []);
  const controllerRef = useRef<AbortController | null>(null);
  const changeRequests = useChangeRequests(meetingId);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    if (meetingId === null) {
      setState({ kind: "not_found" });
      return;
    }
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ kind: "loading" });
    setChangeTarget(null);
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

  // 화자 요약: 회의록마다 한 번 조회. 실패(권한 없음 포함)하면 요약만 숨긴다
  useEffect(() => {
    setSpeakers(null);
    setSpeakerNotice(null);
    setSpeakerOpen(false);
    if (meetingId === null) return;
    const controller = new AbortController();
    getSpeakers(meetingId, controller.signal)
      .then((data) => setSpeakers(data.speakers))
      .catch(() => undefined);
    return () => controller.abort();
  }, [meetingId]);

  /** 화자 저장 뒤: 화면을 비우지 않고 상세(업무 원장)만 다시 받아 바꾸고, 전사문도 다시 불러오게 한다 */
  const onSpeakersSaved = useCallback(async () => {
    setTranscriptVersion((v) => v + 1);
    if (meetingId === null) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      setState({ kind: "ready", meeting: await getMeeting(meetingId, controller.signal) });
    } catch (error) {
      // 갱신 실패는 지금 보이는 내용을 그대로 두고, 화자 패널이 안내한다
      if (isAbortError(error)) return;
      throw error;
    }
  }, [meetingId]);

  /** 종결·삭제·보류·재개·직권 종료 뒤: 화면을 비우지 않고 상세와 수정 요청 목록을 다시 받는다(실패해도 보이는 내용은 그대로) */
  const refreshAfterAction = useCallback(async () => {
    if (meetingId === null) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    void changeRequests.reload();
    try {
      setState({ kind: "ready", meeting: await getMeeting(meetingId, controller.signal) });
    } catch (error) {
      if (isAbortError(error)) return;
      throw error;
    }
  }, [meetingId, changeRequests]);

  /** 5개 항목 저장 뒤: 서버가 돌려준 항목으로 그 칸만 바꾸고, 바뀐 항목(서버 응답 기준)을 안내한다 */
  const onMinutesSaved = useCallback((saved: Minutes, changedLabels: string[]) => {
    setMinutesOpen(false);
    setState((prev) => (prev.kind === "ready" ? { ...prev, meeting: { ...prev.meeting, minutes: saved } } : prev));
    setMinutesNotice(changedLabels.length > 0 ? `저장했습니다 · 바뀐 항목 ${changedLabels.join(", ")}` : "저장했습니다 · 바뀐 항목이 없습니다");
  }, []);

  /** 엑셀 다운로드: 인증 헤더가 필요해 fetch 로 받아 저장한다(파일명은 여기서 만든다) */
  const downloadRef = useRef<AbortController | null>(null);
  const runDownload = useCallback(async (id: number, title: string) => {
    downloadRef.current?.abort();
    const controller = new AbortController();
    downloadRef.current = controller;
    setActionNotice(null);
    try {
      const blob = await downloadMeetingExcel(id, controller.signal);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = excelFileName(title);
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      if (isAbortError(error)) return;
      setActionNotice({ tone: "error", text: `엑셀을 내려받지 못했습니다 · ${errorMessage(error, "서버 오류")}` });
    }
  }, []);
  useEffect(() => () => downloadRef.current?.abort(), []);

  /** 화자 팝업 저장 성공: 요약·안내를 저장 응답으로 바꾸고 상세·전사문을 새로 받는다(실패하면 팝업이 안내) */
  const onSpeakerDialogSaved = useCallback(
    async (saved: SpeakersResponse) => {
      setSpeakers(saved.speakers);
      const auto = saved.autoAssignedItemIds.length;
      setSpeakerNotice(auto > 0 ? `화자를 저장했습니다 · 담당자 자동 지정 ${auto}건` : "화자를 저장했습니다");
      await onSpeakersSaved();
    },
    [onSpeakersSaved],
  );

  /** 조용한 상세 재조회(화면을 비우지 않는다): 서버 기준의 업무 상태·허용 동작(allowedActions)·회의록 허용 동작으로 한꺼번에 바꾼다.
   *  fallback: 재조회가 실패하면 서버가 준 업무로 그 행의 값만 바꾼다(허용 동작은 이전 값 유지, 다음 조회 때 서버 값으로 맞춰짐) */
  const reloadDetail = useCallback(
    async (fallback?: ActionItem) => {
      if (meetingId === null) return;
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      try {
        setState({ kind: "ready", meeting: await getMeeting(meetingId, controller.signal) });
      } catch (error) {
        if (isAbortError(error) || !fallback) return;
        setState((prev) =>
          prev.kind === "ready"
            ? {
                ...prev,
                meeting: {
                  ...prev.meeting,
                  actionItems: prev.meeting.actionItems.map((it) => (it.id === fallback.id ? { ...fallback, allowedActions: it.allowedActions } : it)),
                },
              }
            : prev,
        );
      }
    },
    [meetingId],
  );

  // 처리 중이면 일정 주기로 상세를 조용히 다시 받는다. 처리 중이 아니게 되면 멈추고, 화면을 떠나면 진행 중인 요청을 취소한다(load 효과의 정리)
  const processingNow = state.kind === "ready" && state.meeting.status === "processing";
  useEffect(() => {
    if (!processingNow) return;
    const timer = setInterval(() => void reloadDetail(), PROCESSING_REFRESH_MS);
    return () => clearInterval(timer);
  }, [processingNow, reloadDetail]);

  // 왼쪽 메뉴 처리 현황이 이 회의록의 완료·실패 전환을 알리면 상세를 조용히 다시 받는다(팝업·입력값·스크롤·전사문 펼침은 그대로).
  // 상세 자체의 자동 새로고침(위)은 올린 사람이 아닌 열람자에게도 필요해서 합치지 않고 둔다. 같은 요청이 겹치면 reloadDetail 이 앞 요청을 취소한다
  useProcessingFinished(meetingId, () => void reloadDetail());

  /** 업무 동작(담당자·기한·확정) 뒤: 응답의 업무는 allowedActions 가 null 이므로 행을 그대로 바꾸지 않고 상세를 다시 받아 서버 기준으로 바꾼다 */
  const replaceItem = useCallback((saved: ActionItem) => void reloadDetail(saved), [reloadDetail]);

  const onAssigneeSaved = useCallback(
    (saved: ActionItem) => {
      setAssigneeTarget(null);
      replaceItem(saved);
    },
    [replaceItem],
  );

  /** 회의록 확정 뒤: 응답의 상태·확정 정보만 바꾼다(업무는 그대로) */
  const onMeetingConfirmed = useCallback((result: MeetingConfirmResult) => {
    setState((prev) =>
      prev.kind === "ready"
        ? {
            ...prev,
            meeting: {
              ...prev.meeting,
              status: result.status,
              confirmKind: result.confirmKind,
              confirmedBy: result.confirmedBy,
              confirmedAt: result.confirmedAt,
            },
          }
        : prev,
    );
    void reloadDetail(); // 확정 뒤에는 허용 동작(회의록 확정 버튼 등)도 서버 기준으로
  }, [reloadDetail]);

  const onRequestChange = useCallback((item: ActionItem) => {
    setChangeTarget({ itemId: item.id, label: item.title || "(업무명 없음)" });
  }, []);


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
  const meetingName = meeting.title || "(제목 없음)";
  // 재개된 회의록에서 기한이 비어 있는 업무(날짜도 '미확정'도 아님): 모두 입력하면 안내가 사라진다
  const dueNeededCount =
    meeting.resumedAt && meeting.phase !== "on_hold"
      ? meeting.actionItems.filter((it) => it.missingFields.includes("dueDate")).length
      : 0;

  /** 제목 줄 동작 → 팝업 내용 */
  const openMeetingAction = (kind: MeetingActionKind) => {
    const id = meeting.id;
    if (kind === "download") return void runDownload(id, meeting.title);
    if (kind === "history") return setHistoryOpen(true);
    if (kind === "upload") return setUploadOpen(true);
    const actions: Record<"hold" | "end" | "delete" | "resume", ReasonAction> = {
      hold: {
        title: "회의록 보류", target: meetingName, confirmLabel: "보류하기", requireReason: true,
        notice: "보류하면 딸린 업무도 모두 보류되어 할 일·자동 확정에서 빠집니다.",
        run: (reason, signal) => holdMeeting(id, reason, signal),
      },
      end: {
        title: "회의록 직권 종료", target: meetingName, confirmLabel: "직권 종료하기", requireReason: true,
        notice: "확정 전 업무도 함께 종결됩니다.",
        run: (reason, signal) => endMeeting(id, reason, signal),
      },
      delete: {
        title: "회의록 삭제", target: meetingName, confirmLabel: "삭제하기", requireReason: true,
        notice: "기록은 남기고 목록·할 일에서 숨깁니다.",
        run: (reason, signal) => deleteMeeting(id, reason, signal),
      },
      resume: {
        title: "회의록 재개", target: meetingName, confirmLabel: "재개하기", requireReason: false,
        notice: "업무 기한이 모두 비워집니다. 날짜를 다시 설정해야 합니다.",
        run: (_reason, signal) => resumeMeeting(id, signal),
      },
    };
    setReasonAction(actions[kind]);
  };

  const openReprocess = () =>
    setReasonAction({
      title: "회의록 재처리", target: meetingName, confirmLabel: "재처리하기", requireReason: false,
      notice: "음성이 다시 처리되며 Gemini 모드이면 외부로 전송되고 비용이 생길 수 있습니다.",
      run: async (_reason, signal) => {
        await reprocessMeeting(meeting.id, signal);
      },
    });

  const openItemClose = (item: ActionItem) =>
    setReasonAction({
      title: "업무 종결", target: item.title || "(업무명 없음)", confirmLabel: "업무 종결하기", requireReason: true,
      run: async (reason, signal) => {
        await closeActionItem(item.id, reason, signal);
      },
    });

  const openItemDelete = (item: ActionItem) =>
    setReasonAction({
      title: "업무 삭제", target: item.title || "(업무명 없음)", confirmLabel: "삭제하기", requireReason: true,
      notice: "기록은 남기고 업무 원장에서 숨깁니다.",
      run: async (reason, signal) => {
        await deleteActionItem(item.id, reason, signal);
      },
    });

  return (
    <>
      <BackLink />
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-2">
          <h1 className="text-[28px] font-semibold leading-9 tracking-tight text-mn-text">{meeting.title || "(제목 없음)"}</h1>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <StatusDot tone={status.tone} label={status.label} />
            <span aria-label="회의록 단계">
              <Badge>{phaseText(meeting)}</Badge>
            </span>
            <Mono muted>{formatDateTime(meeting.heldAt)}</Mono>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {can(meeting.allowedActions, MEETING_ACTION.requestChange) ? (
            <Button onClick={() => setChangeTarget({ itemId: null, label: "회의록 전체" })}>회의록 전체 수정 요청</Button>
          ) : null}
          {can(meeting.allowedActions, MEETING_ACTION.confirmMeeting) ? (
            <MeetingConfirmButton meetingId={meeting.id} title={meeting.title} onConfirmed={onMeetingConfirmed} />
          ) : null}
          <MeetingActions allowed={meeting.allowedActions} onSelect={openMeetingAction} />
        </div>
      </header>
      <PhaseRecords meeting={meeting} />
      <ProcessingBanner
        status={meeting.status}
        processing={meeting.processing}
        canReprocess={can(meeting.allowedActions, MEETING_ACTION.reprocessMeeting)}
        onReprocess={openReprocess}
      />
      <FakeMeetingNotice show={meeting.minutes?.engine === "fake"} />
      {actionNotice ? (
        <p role={actionNotice.tone === "error" ? "alert" : "status"} className="text-sm">
          <StatusDot tone={actionNotice.tone} label={actionNotice.text} />
        </p>
      ) : null}
      {dueNeededCount > 0 ? (
        <p role="status" className="text-sm">
          <StatusDot tone="error" label={`기한을 다시 설정해야 하는 업무 ${dueNeededCount}건`} />
        </p>
      ) : null}
      <Overview
        meeting={meeting}
        minutesPanel={
          <MinutesPanel
            minutes={meeting.minutes}
            canEdit={can(meeting.allowedActions, MEETING_ACTION.editMinutes)}
            notice={minutesNotice}
            onEdit={() => {
              setMinutesNotice(null);
              setMinutesOpen(true);
            }}
          />
        }
        speakerSummary={
          <SpeakerSummary
            speakers={speakers}
            notice={speakerNotice}
            canEdit={can(meeting.allowedActions, MEETING_ACTION.editSpeakers)}
            onOpen={() => {
              setSpeakerNotice(null);
              setSpeakerOpen(true);
            }}
          />
        }
      />
      <AudioPlayer key={`audio-${meeting.id}`} meetingId={meeting.id} seek={seek} />
      <LedgerTable
        items={meeting.actionItems}
        onEditAssignee={setAssigneeTarget}
        onItemConfirmed={replaceItem}
        onItemUpdated={replaceItem}
        onSeek={onSeek}
        onAddItem={() => setManualOpen(true)}
        onRequestChange={onRequestChange}
        allowed={meeting.allowedActions}
        onCloseItem={openItemClose}
        onDeleteItem={openItemDelete}
      />
      <MinutesDialog meetingId={meeting.id} open={minutesOpen} minutes={meeting.minutes} onClose={() => setMinutesOpen(false)} onSaved={onMinutesSaved} />
      <HistoryDialog meetingId={meeting.id} open={historyOpen} onClose={() => setHistoryOpen(false)} />
      <UploadUpdateDialog
        meetingId={meeting.id}
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onApplied={async (result) => {
          setActionNotice({
            tone: "ready",
            text: `수정 회의록을 적용했습니다 · 업무 ${result.updatedItemIds.length}건 갱신, ${result.addedItemIds.length}건 추가, ${result.skipped.length}건 건너뜀`,
          });
          await refreshAfterAction();
        }}
      />
      <ManualItemDialog
        meetingId={meeting.id}
        open={manualOpen}
        onClose={() => setManualOpen(false)}
        onCreated={async () => {
          await refreshAfterAction();
        }}
      />
      <ReasonDialog action={reasonAction} onClose={() => setReasonAction(null)} onDone={refreshAfterAction} />
      <AssigneeDialog item={assigneeTarget} onClose={() => setAssigneeTarget(null)} onSaved={onAssigneeSaved} />
      <SpeakerDialog
        meetingId={meeting.id}
        open={speakerOpen}
        onClose={() => setSpeakerOpen(false)}
        onSaved={onSpeakerDialogSaved}
      />
      <ChangeRequestDialog
        meetingId={meeting.id}
        target={changeTarget}
        list={changeRequests.list}
        onRetryList={() => void changeRequests.reload()}
        onClose={() => setChangeTarget(null)}
        onCreated={changeRequests.reload}
        onResolved={changeRequests.replace}
        canResolve={can(meeting.allowedActions, MEETING_ACTION.resolveChangeRequest)}
      />
      <TranscriptPanel key={meeting.id} meetingId={meeting.id} version={transcriptVersion} onSeek={onSeek} />
    </>
  );
}
