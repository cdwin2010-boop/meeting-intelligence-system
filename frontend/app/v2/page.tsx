"use client";

/*
 * v2 홈(임시): 로그인 확인용. 다음 단계에서 "할 일" 화면(시안 02-home)으로 교체한다.
 */
import { useRouter } from "next/navigation";
import { Suspense } from "react";

import { Badge, Button } from "@/components/mono";
import { RequireAuth, useAuth } from "@/components/v2/AuthProvider";
import { V2_LOGIN } from "@/lib/v2/next-path";
import { RANK_LABEL } from "@/lib/v2/types";

function Home() {
  const { account, logout } = useAuth();
  const router = useRouter();
  if (!account) return null;

  function onLogout() {
    logout(); // 토큰 삭제
    router.replace(V2_LOGIN);
  }

  return (
    <main className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-4 py-12">
      <h1 className="text-[28px] font-semibold leading-9 tracking-tight text-mn-text">로그인됨</h1>
      <div className="flex items-center justify-between gap-4 rounded-mn-card border border-mn-border bg-mn-surface p-6">
        <div className="flex items-center gap-3">
          <span className="text-sm font-medium text-mn-text">{account.name}</span>
          <Badge>{RANK_LABEL[account.rank] ?? account.rank}</Badge>
        </div>
        <Button onClick={onLogout}>로그아웃</Button>
      </div>
    </main>
  );
}

export default function V2HomePage() {
  return (
    <Suspense fallback={null}>
      <RequireAuth>
        <Home />
      </RequireAuth>
    </Suspense>
  );
}
