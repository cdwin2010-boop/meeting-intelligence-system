"use client";

/*
 * v2 로그인 후 공통 앱 틀 (시안 docs/ui-v2-mockups/02-home.html 의 왼쪽 메뉴)
 * - 왼쪽: 서비스 이름, 회의록 올리기, 주 메뉴(할 일·회의록). 현재 화면은 aria-current="page"
 * - 위: 로그인 사용자 이름·직급, 로그아웃(토큰 삭제 후 로그인 화면)
 * - 좁은 화면에서는 메뉴가 위로 올라가 세로로 쌓인다
 */
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";

import { Badge, Button } from "@/components/mono";
import { useAuth } from "@/components/v2/AuthProvider";
import { FakeEngineNotice, useEngineIsFake } from "@/components/v2/EngineNotice";
import { ProcessingPanel } from "@/components/v2/ProcessingPanel";
import { V2_HOME, V2_LOGIN } from "@/lib/v2/next-path";
import { RANK_LABEL } from "@/lib/v2/types";

export const V2_MEETINGS = "/v2/meetings";
export const V2_UPLOAD = "/v2/upload";

const NAV_ITEMS = [
  { href: V2_HOME, label: "할 일" },
  { href: V2_MEETINGS, label: "회의록" },
];

// 할 일(/v2)은 정확히 일치할 때만, 나머지는 하위 경로까지 현재 메뉴로 본다
const isCurrent = (pathname: string, href: string) =>
  href === V2_HOME ? pathname === V2_HOME : pathname === href || pathname.startsWith(`${href}/`);

export function AppShell({ children }: { children: ReactNode }) {
  const { account, logout } = useAuth();
  const engineIsFake = useEngineIsFake(account !== null);
  const pathname = usePathname();
  const router = useRouter();

  function onLogout() {
    logout(); // 토큰 삭제
    router.replace(V2_LOGIN);
  }

  const uploadCurrent = isCurrent(pathname, V2_UPLOAD);

  return (
    <div className="flex min-h-screen flex-col bg-mn-bg text-mn-text md:flex-row">
      <aside className="flex shrink-0 flex-col gap-6 border-b border-mn-border px-4 py-6 md:w-[248px] md:border-r md:border-b-0">
        <Link href={V2_HOME} className="mn-focus rounded-mn-control text-base font-semibold tracking-tight">
          회의록 추적
        </Link>
        <Link
          href={V2_UPLOAD}
          aria-current={uploadCurrent ? "page" : undefined}
          className="mn-focus inline-flex h-10 w-full items-center justify-center rounded-mn-control border border-mn-accent bg-mn-accent px-4 text-sm font-medium text-mn-on-accent hover:bg-mn-accent-hover"
        >
          회의록 올리기
        </Link>
        <nav aria-label="주 메뉴" className="flex flex-col gap-1">
          {NAV_ITEMS.map((item) => {
            const current = isCurrent(pathname, item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={current ? "page" : undefined}
                className={[
                  "mn-focus flex h-10 items-center rounded-mn-control px-3 text-sm",
                  current ? "bg-mn-elevated font-medium text-mn-text" : "text-mn-muted hover:bg-mn-elevated hover:text-mn-text",
                ].join(" ")}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <ProcessingPanel />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-end gap-3 border-b border-mn-border px-4 md:px-12">
          {account ? (
            <div role="group" className="flex items-center gap-2" aria-label="로그인 사용자">
              <span className="text-sm font-medium text-mn-text">{account.name}</span>
              <Badge>{RANK_LABEL[account.rank] ?? account.rank}</Badge>
            </div>
          ) : null}
          <Button size="sm" onClick={onLogout}>
            로그아웃
          </Button>
        </header>
        <main className="flex min-w-0 flex-1 flex-col gap-6 px-4 pt-8 pb-16 md:px-12">
          <FakeEngineNotice show={engineIsFake} />
          {children}
        </main>
      </div>
    </div>
  );
}

/** 아직 만들지 않은 화면의 빈 자리 */
export function PlaceholderPage({ title }: { title: string }) {
  return (
    <>
      <h1 className="text-[28px] font-semibold leading-9 tracking-tight text-mn-text">{title}</h1>
      <div className="rounded-mn-card border border-mn-border bg-mn-surface p-6 text-sm text-mn-muted">
        이 화면은 준비 중입니다.
      </div>
    </>
  );
}
