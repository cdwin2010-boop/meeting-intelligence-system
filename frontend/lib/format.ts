/** 초 → "m:ss" 또는 "h:mm:ss" (예: 412 → "6:52", 3120 → "52:00", 4260 → "1:11:00") */
export function formatDuration(totalSeconds: number | null): string {
  if (totalSeconds === null) return "—";
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const mm = hours > 0 ? String(minutes).padStart(2, "0") : String(minutes);
  const ss = String(seconds).padStart(2, "0");
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
}

// 서버/클라이언트가 같은 결과를 내도록 시간대를 고정합니다 (하이드레이션 불일치 방지).
const timeFormatter = new Intl.DateTimeFormat("ko-KR", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
  timeZone: "Asia/Seoul",
});

const dateTimeFormatter = new Intl.DateTimeFormat("ko-KR", {
  dateStyle: "medium",
  timeStyle: "medium",
  hourCycle: "h23",
  timeZone: "Asia/Seoul",
});

export const formatTime = (date: Date) => timeFormatter.format(date);
export const formatDateTime = (iso: string | null) =>
  iso === null ? "—" : dateTimeFormatter.format(new Date(iso));
