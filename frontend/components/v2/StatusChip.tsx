/*
 * 상태 라벨 한 곳(Clean Light, 작업 87-1). 승인 대기·수정 요청·보완 필요·진행 중·완료 다섯 쌍만 쓴다(색은 theme-v2.css 토큰).
 * 색만으로 구분하지 않도록 글자 라벨은 필수다. 알약 모양, 글자 13/500.
 */
export type StatusChipTone = "pending" | "revision" | "needs" | "progress" | "done";

const TONE_CLASS: Record<StatusChipTone, string> = {
  pending: "mn-chip-warning",
  revision: "mn-chip-revision",
  needs: "mn-chip-danger",
  progress: "mn-chip-info",
  done: "mn-chip-success",
};

export function StatusChip({ tone, label }: { tone: StatusChipTone; label: string }) {
  return <span className={`mn-chip ${TONE_CLASS[tone]} whitespace-nowrap`}>{label}</span>;
}
