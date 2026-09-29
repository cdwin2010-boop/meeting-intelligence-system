import { Badge } from "@/components/mono";
import type { Meeting } from "@/lib/types";

// 서버/클라이언트가 같은 결과를 내도록 시간대를 고정합니다 (하이드레이션 불일치 방지).
const dateTimeFormatter = new Intl.DateTimeFormat("ko-KR", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "Asia/Seoul",
});

export function MeetingHeader({ meeting }: { meeting: Meeting }) {
  return (
    <header className="rounded-mn-card border border-mn-border bg-mn-surface p-6">
      <h1 className="text-[32px] font-semibold leading-10 tracking-tight text-mn-text">
        {meeting.title}
      </h1>

      {/* 일시는 숫자 데이터이므로 mono 폰트 */}
      <p className="mt-2 font-mn-mono text-[13px] text-mn-muted">
        <time dateTime={meeting.startedAt}>
          {dateTimeFormatter.format(new Date(meeting.startedAt))}
        </time>
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <span className="mr-1 text-xs text-mn-muted">Attendees ({meeting.attendees.length})</span>
        <ul className="flex flex-wrap gap-2">
          {meeting.attendees.map((attendee) => (
            <li key={attendee.id}>
              <Badge>
                {attendee.name}
                <span className="ml-1 text-mn-muted">· {attendee.role}</span>
              </Badge>
            </li>
          ))}
        </ul>
      </div>
    </header>
  );
}
