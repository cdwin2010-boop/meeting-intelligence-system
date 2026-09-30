import { MeetingList } from "@/components/meeting/MeetingList";

// "/" = 회의 목록. 조회는 클라이언트 컴포넌트가 한다.
// (목 모드에서 업로드한 회의는 브라우저 메모리에만 있어서 서버 컴포넌트로는 보이지 않기 때문)
// 회의 상세는 /meetings/[id], 음성 등록 링크는 목록 머리글에 있다.
export default function Page() {
  return (
    <main>
      <MeetingList />
    </main>
  );
}
