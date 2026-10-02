/*
 * v2 회의록 표시 규칙(목록·상세 공용): 상태 → 점 색 + 글자 라벨, 단계(진행중·종료·보류·삭제) 글자 라벨, 시각 형식.
 */
import type { StatusDotTone } from "@/components/mono";
import type { EndKind, MeetingPhase, MeetingStatus } from "@/lib/v2/meetings";
import type { ConfirmKind } from "@/lib/v2/todos";

const AUTO_CONFIRM_KINDS = new Set<string>(["period_elapsed", "due_reached"]);

/** 회의록 상태 → 점 색 + 글자 라벨 (확정은 자동/그 외를 구분) */
export function meetingStatus(meeting: { status: MeetingStatus; confirmKind: ConfirmKind | null }): {
  tone: StatusDotTone;
  label: string;
} {
  switch (meeting.status) {
    case "processing":
      return { tone: "building", label: "전사·추출 중" };
    case "awaiting_confirmation":
      return { tone: "queued", label: "확정 대기" };
    case "confirmed":
      return { tone: "ready", label: meeting.confirmKind && AUTO_CONFIRM_KINDS.has(meeting.confirmKind) ? "자동 확정됨" : "확정 완료" };
    case "failed":
      return { tone: "error", label: "처리 실패" };
    case "no_content":
      return { tone: "queued", label: "내용 없음" };
    default:
      return { tone: "queued", label: String(meeting.status) };
  }
}

/** 단계 탭 순서와 글자 라벨 */
export const MEETING_PHASES: MeetingPhase[] = ["active", "ended", "on_hold", "deleted"];

export const PHASE_LABEL: Record<MeetingPhase, string> = {
  active: "진행중",
  ended: "종료",
  on_hold: "보류",
  deleted: "삭제",
};

export const END_KIND_LABEL: Record<EndKind, string> = {
  auto: "자동 종료",
  manager: "관리자 직권 종료",
};

/** 단계 글자(종료는 구분까지): 예) "종료 · 자동 종료". 서버가 단계를 주지 않으면 진행중 */
export function phaseText(meeting: { phase?: MeetingPhase; endKind?: EndKind | null }): string {
  const phase = meeting.phase ?? "active";
  const label = PHASE_LABEL[phase] ?? String(phase);
  return phase === "ended" && meeting.endKind ? `${label} · ${END_KIND_LABEL[meeting.endKind] ?? meeting.endKind}` : label;
}

export const CONFIRM_KIND_LABEL: Record<ConfirmKind, string> = {
  manager: "관리자 확정",
  registration: "등록 시 확정",
  period_elapsed: "기간 경과로 자동 확정",
  due_reached: "마감일 도달로 자동 확정",
};

/** ISO 시각 → 브라우저 현지 시각 YYYY-MM-DD HH:mm (sv-SE 형식이 ISO 와 같다) */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("sv-SE", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/** ISO 시각 → 브라우저 현지 날짜 YYYY-MM-DD */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString("sv-SE");
}

/** 초 → HH:MM:SS (녹음 안 위치) */
export function formatOffset(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || !Number.isFinite(sec) || sec < 0) return "—";
  const total = Math.floor(sec);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}`;
}

export const detailPath = (id: number) => `/v2/meetings/${id}`;
export const V2_MEETINGS_PATH = "/v2/meetings";
