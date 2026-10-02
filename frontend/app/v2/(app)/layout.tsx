// v2 로그인 후 화면 공통: 로그인 확인(RequireAuth, 안 됐으면 /v2/login?next=...) + 앱 틀(왼쪽 메뉴·로그인 사용자).
// (app) 은 주소에 나타나지 않는 묶음 폴더라 /v2/login 에는 이 틀이 씌워지지 않는다.
import { Suspense } from "react";

import { AppShell } from "@/components/v2/AppShell";
import { RequireAuth } from "@/components/v2/AuthProvider";

export default function V2AppLayout({ children }: { children: React.ReactNode }) {
  return (
    // RequireAuth 가 useSearchParams 를 쓰므로 Suspense 경계가 필요하다(Next 15)
    <Suspense fallback={null}>
      <RequireAuth>
        <AppShell>{children}</AppShell>
      </RequireAuth>
    </Suspense>
  );
}
