// v2 화면 공통: 로그인 상태(AuthProvider)를 /v2 아래 모든 화면이 함께 쓴다. v1 화면과는 분리된 경로다.
import type { Metadata } from "next";

import { AuthProvider } from "@/components/v2/AuthProvider";
import { THEME_INIT_SCRIPT, ThemeProvider } from "@/components/v2/ThemeProvider";

export const metadata: Metadata = {
  title: "회의록 추적",
  description: "회의에서 정한 일을 놓치지 않게 추적합니다.",
};

export default function V2Layout({ children }: { children: React.ReactNode }) {
  return (
    <>
      {/* 첫 페인트 전에 테마 속성을 붙인다(저장된 다크가 라이트로 번쩍이지 않게) */}
      <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      <ThemeProvider>
        <AuthProvider>{children}</AuthProvider>
      </ThemeProvider>
    </>
  );
}
