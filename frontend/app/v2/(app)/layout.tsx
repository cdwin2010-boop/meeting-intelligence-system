// v2 로그인 후 화면 공통: 로그인 확인(RequireAuth, 안 됐으면 /v2/login?next=...) + 앱 틀(왼쪽 메뉴·로그인 사용자).
// (app) 은 주소에 나타나지 않는 묶음 폴더라 /v2/login 에는 이 틀이 씌워지지 않는다.
import { cookies } from "next/headers";
import { Suspense } from "react";

import { AppShell } from "@/components/v2/AppShell";
import { ProcessingProvider } from "@/components/v2/ProcessingProvider";
import { RequireAuth } from "@/components/v2/AuthProvider";
import { parseSidePosition, SIDE_COOKIE } from "@/lib/v2/side-position";

export default async function V2AppLayout({ children }: { children: React.ReactNode }) {
  // 보조창 좌우 위치는 쿠키에서 서버 렌더 때 읽는다(첫 화면에서 위치가 바뀌는 깜빡임 방지). left|right 만 인정
  const side = parseSidePosition((await cookies()).get(SIDE_COOKIE)?.value);
  return (
    // RequireAuth 가 useSearchParams 를 쓰므로 Suspense 경계가 필요하다(Next 15)
    <Suspense fallback={null}>
      <RequireAuth>
        <ProcessingProvider>
          <AppShell initialSidePosition={side}>{children}</AppShell>
        </ProcessingProvider>
      </RequireAuth>
    </Suspense>
  );
}
