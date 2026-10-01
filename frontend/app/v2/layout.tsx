// v2 화면 공통: 로그인 상태(AuthProvider)를 /v2 아래 모든 화면이 함께 쓴다. v1 화면과는 분리된 경로다.
import type { Metadata } from "next";

import { AuthProvider } from "@/components/v2/AuthProvider";

export const metadata: Metadata = {
  title: "회의록 추적",
  description: "회의에서 정한 일을 놓치지 않게 추적합니다.",
};

export default function V2Layout({ children }: { children: React.ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>;
}
