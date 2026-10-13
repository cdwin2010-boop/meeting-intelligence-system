/*
 * 상태 라벨 한 곳(Clean Light, 작업 87-1). 승인 대기·수정 요청·보완 필요·진행 중·완료 다섯 쌍만 쓴다(색은 theme-v2.css 토큰).
 * 색만으로 구분하지 않도록 글자 라벨은 필수다. 알약 모양, 글자 13/500.
 */
export type StatusChipTone = "pending" | "revision" | "needs" | "progress" | "done" | "neutral";

const TONE_CLASS: Record<StatusChipTone, string> = {
  pending: "mn-chip-warning",
  revision: "mn-chip-revision",
  needs: "mn-chip-danger",
  progress: "mn-chip-info",
  done: "mn-chip-success",
  neutral: "mn-chip-neutral", // 상태가 아닌 안내(내용 없음 등). 다섯 상태 색과 겹치지 않는 회색
};

/** 기존 점 상태(StatusDotTone)를 칩 톤으로: ready=완료, building=진행 중, error=보완 필요, queued=확정 대기(그 외 회색) */
export function statusChipToneFromDot(tone: "ready" | "building" | "error" | "queued", label: string): StatusChipTone {
  if (tone === "ready") return "done";
  if (tone === "building") return "progress";
  if (tone === "error") return "needs";
  return label === "확정 대기" ? "pending" : "neutral";
}

export function StatusChip({ tone, label }: { tone: StatusChipTone; label: string }) {
  return <span className={`mn-chip ${TONE_CLASS[tone]} whitespace-nowrap`}>{label}</span>;
}
