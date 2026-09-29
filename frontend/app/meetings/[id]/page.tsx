import { notFound } from "next/navigation";
import { MeetingDetail } from "@/components/meeting/MeetingDetail";
import { MockMeetingLoader } from "@/components/meeting/MockMeetingLoader";
import { ApiError, fetchMeeting, isMockApi } from "@/lib/api";

// 매 요청마다 최신 회의 정보(전사 원문 포함)를 가져오도록 정적 생성을 끕니다.
export const dynamic = "force-dynamic";

// Next 15: 동적 경로의 params는 Promise이므로 await 해서 꺼냅니다.
export default async function MeetingPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  try {
    const meeting = await fetchMeeting(id);
    return (
      <main>
        <MeetingDetail meeting={meeting} />
      </main>
    );
  } catch (error) {
    if (error instanceof ApiError && error.code === "not_found") {
      // 목 모드: 업로드한 회의는 브라우저 메모리에만 있으므로 브라우저에서 다시 찾는다
      if (isMockApi) {
        return (
          <main>
            <MockMeetingLoader meetingId={id} />
          </main>
        );
      }
      notFound();
    }
    throw error;
  }
}
