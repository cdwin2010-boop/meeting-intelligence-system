export type StatusDotTone = "ready" | "building" | "error" | "queued";

// 색상 = 상태 (Teal 완료 / Blue 진행 / Red 오류 / 회색 대기)
const DOT_CLASS: Record<StatusDotTone, string> = {
  ready: "bg-mn-teal",
  building: "bg-mn-blue",
  error: "bg-mn-red",
  queued: "bg-mn-muted",
};

interface StatusDotProps {
  tone: StatusDotTone;
  /** 라벨은 필수: 색만으로 의미를 전달하지 않기 위해서입니다 (색각 이상 사용자 배려). */
  label: string;
}

export function StatusDot({ tone, label }: StatusDotProps) {
  return (
    <span className="inline-flex items-center gap-2 font-mn-mono text-xs text-mn-text">
      {/* 점은 장식이므로 스크린리더가 읽지 않도록 숨깁니다 */}
      <span aria-hidden="true" className={`size-2 shrink-0 rounded-full ${DOT_CLASS[tone]}`} />
      {label}
    </span>
  );
}
