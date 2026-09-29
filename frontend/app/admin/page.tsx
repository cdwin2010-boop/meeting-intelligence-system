import type { Metadata } from "next";
import { AdminConsole } from "@/components/admin/AdminConsole";

export const metadata: Metadata = {
  title: "Admin console",
  description: "STT/LLM 작업 큐 모니터링 및 관리",
};

// 서버 컴포넌트는 껍데기만 담당하고, 조회/인터랙션은 클라이언트 컴포넌트(AdminConsole)가 처리합니다.
// → 나중에 인증(관리자 권한 확인)을 이 페이지 레벨에 붙이기 좋은 구조입니다.
export default function AdminPage() {
  return (
    <main>
      <AdminConsole />
    </main>
  );
}
