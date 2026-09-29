import type { Metadata } from "next";
import { UploadForm } from "@/components/upload/UploadForm";

export const metadata: Metadata = {
  title: "Upload audio",
  description: "회의 음성 등록: 업로드 후 전사·액션아이템 추출 진행 상황 확인",
};

// 서버 컴포넌트는 껍데기만 담당하고, 입력·업로드·진행 조회는 클라이언트 컴포넌트가 처리합니다.
export default function UploadPage() {
  return (
    <main>
      <UploadForm />
    </main>
  );
}
