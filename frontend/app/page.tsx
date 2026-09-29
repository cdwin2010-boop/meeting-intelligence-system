import { notFound } from "next/navigation";
import { MeetingDetail } from "@/components/meeting/MeetingDetail";
import { ApiError, fetchMeeting } from "@/lib/api";

// 실제 서버를 쓸 때 매 요청마다 최신 회의 정보를 가져오도록 정적 생성을 끕니다.
export const dynamic = "force-dynamic";

// 처음에는 "/" 한 화면에서 기본 회의 하나를 보여 줍니다. (회의별 주소 /meetings/[id]는 다음 단계)
const DEFAULT_MEETING_ID = process.env.NEXT_PUBLIC_DEFAULT_MEETING_ID ?? "mtg-2026-0925";

// 서버 컴포넌트: 회의 정보를 API(목 또는 실서버)에서 가져와 넘기고, 인터랙션은 클라이언트 컴포넌트가 담당합니다.
export default async function Page() {
  try {
    const meeting = await fetchMeeting(DEFAULT_MEETING_ID);
    return (
      <main>
        <MeetingDetail meeting={meeting} />
      </main>
    );
  } catch (error) {
    // 서버가 "없음(404)"이라고 답한 경우만 404 페이지로. 그 외(서버 꺼짐 등)는 Next 기본 오류 화면.
    if (error instanceof ApiError && error.code === "not_found") notFound();
    throw error;
  }
}
