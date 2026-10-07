"use client";

/*
 * v2 로그인 후 공통 앱 틀 (시안 docs/ui-v2-mockups/02-home.html 의 메뉴, v3.0 에서 화면 오른쪽으로 이동)
 * - DOM 순서는 화면 순서와 같게 헤더·본문(content) → 메뉴(aside) 로 둔다(키보드 Tab 순서 = 화면 순서).
 * - 기준 너비 768px: 768 이상은 데스크톱(화면 오른쪽 고정 메뉴·헤더 오른쪽에 사용자·로그아웃), 768 미만은 모바일.
 * - 모바일: 메뉴(같은 aside 요소)는 숨고, 헤더 오른쪽 햄버거로 오른쪽에서 드로어처럼 열린다. 메뉴 링크가 DOM 에 두 번 생기지 않는다.
 *   드로어: 포커스 가두기·Esc·바깥(배경) 클릭 닫기·경로 이동 시 자동 닫기·닫히면 햄버거로 포커스 복귀·열린 동안 본문 스크롤 잠금·배경 내용 inert.
 *   사용자 표시·로그아웃은 한 요소만 두고 모바일에서는 드로어 하단에, 데스크톱에서는 헤더에 둔다(접근성 이름 "로그인 사용자"·"로그아웃" 유지).
 * - 헤더: 모바일에는 처리 현황 요약 뱃지(ProcessingBadge, 같은 ProcessingProvider 상태·새 폴링 없음). 업로드 진입은 패널(모바일은 드로어)의 "회의록 올리기" 하나만 둔다(v3.0 에서 헤더 "새 회의 업로드" 제거).
 * - 데스크톱 패널 접기·펼치기(768 이상): 토글은 패널 위쪽 줄 왼쪽 끝. 접으면 48px 띠(토글+처리 중 건수), 나머지는 hidden 이라 접근성 트리·Tab 에서 빠진다.
 *   상태는 계정별 localStorage(저장소가 막혀도 정상, 기본 펼침)에서 마운트 뒤 적용한다. 접혀 있어도 처리 현황 알림(aria-live)은 띠에서 유지한다.
 * - 화면 높이는 dvh 로(모바일 주소창 변화 대응), 드로어 안 스크롤은 본문과 분리(overscroll-behavior: contain).
 */
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { Badge, Button } from "@/components/mono";
import { useAuth } from "@/components/v2/AuthProvider";
import { FakeEngineNotice, useEngineIsFake } from "@/components/v2/EngineNotice";
import { ProcessingBadge } from "@/components/v2/ProcessingBadge";
import { useProcessing } from "@/components/v2/ProcessingProvider";
import { ProcessingPanel } from "@/components/v2/ProcessingPanel";
import { V2_HOME, V2_LOGIN } from "@/lib/v2/next-path";
import { isActiveStatus } from "@/lib/v2/processing-status";
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

/** 모바일(768 미만) 여부. 서버 렌더링·첫 렌더는 false(데스크톱 구조) 로 시작하고 마운트 뒤 맞춘다 */
function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 767px)");
    setMobile(query.matches);
    const onChange = (event: MediaQueryListEvent) => setMobile(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  return mobile;
}

const COLLAPSE_KEY_PREFIX = "mi.v2.shell.panelCollapsed.";

function readCollapsed(accountId: number): boolean {
  try {
    return window.localStorage.getItem(`${COLLAPSE_KEY_PREFIX}${accountId}`) === "1";
  } catch {
    return false; // 저장소가 막혀 있어도 화면은 정상(펼침)
  }
}

function writeCollapsed(accountId: number, collapsed: boolean): void {
  try {
    window.localStorage.setItem(`${COLLAPSE_KEY_PREFIX}${accountId}`, collapsed ? "1" : "0");
  } catch {
    // 저장 실패는 무시(이번 방문 동안만 유지)
  }
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function AppShell({ children }: { children: ReactNode }) {
  const { account, logout } = useAuth();
  const engineIsFake = useEngineIsFake(account !== null);
  const pathname = usePathname();
  const router = useRouter();
  const mobile = useIsMobile();
  const asideId = useId();
  const asideRef = useRef<HTMLElement>(null);
  const hamburgerRef = useRef<HTMLButtonElement>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const { items, announcement } = useProcessing();
  const accountId = account?.id ?? null;
  const [collapsed, setCollapsed] = useState(false);
  const activeCount = items.filter((item) => isActiveStatus(item.status)).length;
  // 접힘은 데스크톱에서만 적용한다(모바일 드로어는 항상 펼친 모양)
  const folded = collapsed && !mobile;
  const contentId = `${asideId}-content`;
  const wasOpen = useRef(false);
  const open = drawerOpen && mobile;

  // 저장된 접힘 상태는 마운트 뒤에 적용한다(서버 렌더링과 불일치 방지)
  useEffect(() => {
    if (accountId !== null) setCollapsed(readCollapsed(accountId));
  }, [accountId]);

  function toggleCollapsed() {
    const next = !collapsed;
    setCollapsed(next);
    if (accountId !== null) writeCollapsed(accountId, next);
  }

  function onLogout() {
    logout(); // 토큰 삭제
    router.replace(V2_LOGIN);
  }

  // 경로가 바뀌거나 데스크톱 너비가 되면 드로어를 닫는다
  useEffect(() => {
    setDrawerOpen(false);
  }, [pathname]);
  useEffect(() => {
    if (!mobile) setDrawerOpen(false);
  }, [mobile]);

  // 열려 있는 동안: 본문 스크롤 잠금, 첫 항목으로 포커스, Esc 닫기, Tab 가두기. 닫히면 햄버거로 포커스 복귀
  useEffect(() => {
    if (!open) {
      if (wasOpen.current) hamburgerRef.current?.focus();
      wasOpen.current = false;
      return;
    }
    wasOpen.current = true;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    asideRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (document.querySelector('[aria-modal="true"]')) return; // 팝업이 열려 있으면 팝업이 키를 처리한다
      if (event.key === "Escape") {
        event.preventDefault();
        setDrawerOpen(false);
        return;
      }
      if (event.key !== "Tab") return;
      const items = Array.from(asideRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (!asideRef.current?.contains(active)) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  const uploadCurrent = isCurrent(pathname, V2_UPLOAD);

  // 사용자 표시·로그아웃: 한 요소만 두고 모바일에서는 드로어 하단, 데스크톱에서는 헤더에 둔다
  const userBlock = (
    <>
      {account ? (
        <div role="group" className="flex items-center gap-2" aria-label="로그인 사용자">
          <span className="text-sm font-medium text-mn-text">{account.name}</span>
          <Badge>{RANK_LABEL[account.rank] ?? account.rank}</Badge>
        </div>
      ) : null}
      <Button size="sm" onClick={onLogout} className="max-md:min-h-11 max-md:min-w-11">
        로그아웃
      </Button>
    </>
  );

  return (
    <div className="flex min-h-dvh flex-col bg-mn-bg text-mn-text md:flex-row">
      {open ? <div aria-hidden="true" onMouseDown={(event) => {
            // 기본 동작(누른 곳으로 포커스 이동)을 막아야 닫힌 뒤 햄버거로 돌려준 포커스가 body 로 옮겨지지 않는다(공용 Modal 과 같은 이유)
            event.preventDefault();
            setDrawerOpen(false);
          }} className="fixed inset-0 z-30 bg-mn-overlay md:hidden" /> : null}
      <div inert={open} className="flex min-w-0 flex-1 flex-col">
        <header className="flex min-h-14 items-center gap-3 border-b border-mn-border px-4 md:h-14 md:px-12">
          <div className="ml-auto flex min-w-0 items-center gap-3">
            {mobile ? (
              <ProcessingBadge
                onOpen={() => {
                  setDrawerOpen(true);
                  requestAnimationFrame(() => document.getElementById("processing-panel")?.scrollIntoView({ block: "nearest" }));
                }}
              />
            ) : null}
            {!mobile ? userBlock : null}
            {mobile ? (
              <Button
                ref={hamburgerRef}
                size="sm"
                aria-label="메뉴 열기"
                aria-expanded={open}
                aria-controls={asideId}
                onClick={() => setDrawerOpen(true)}
                className="min-h-11 min-w-11"
              >
                ☰
              </Button>
            ) : null}
          </div>
        </header>
        <main className="flex min-w-0 flex-1 flex-col gap-6 px-4 pt-8 pb-16 md:px-12">
          <FakeEngineNotice show={engineIsFake} />
          {children}
        </main>
      </div>

      <aside
        id={asideId}
        ref={asideRef}
        aria-label={open ? "메뉴 드로어" : undefined}
        className={[
          "shrink-0 flex-col gap-6 border-mn-border px-4 py-6",
          open ? "fixed inset-y-0 right-0 z-40 flex h-dvh w-[min(86vw,320px)] overflow-y-auto overscroll-contain border-l bg-mn-bg" : "hidden",
          `md:static md:z-auto md:flex md:h-auto md:overflow-visible md:border-l md:bg-transparent ${folded ? "md:w-12 md:items-center md:px-0" : "md:w-[248px]"}`,
        ].join(" ")}
      >
        <div className="flex items-center justify-between gap-2">
          {!mobile ? (
            <Button
              size="sm"
              aria-label={collapsed ? "회의록 추적 패널 펼치기" : "회의록 추적 패널 접기"}
              aria-expanded={!collapsed}
              aria-controls={contentId}
              onClick={toggleCollapsed}
            >
              {collapsed ? "‹" : "›"}
            </Button>
          ) : null}
          <Link href={V2_HOME} className={`mn-focus rounded-mn-control text-base font-semibold tracking-tight ${folded ? "hidden" : ""} ${!mobile ? "mr-auto" : ""}`}>
            회의록 추적
          </Link>
          {open ? (
            <Button size="sm" aria-label="메뉴 닫기" onClick={() => setDrawerOpen(false)} className="min-h-11 min-w-11">
              ✕
            </Button>
          ) : null}
        </div>
        {folded ? (
          <>
            {activeCount > 0 ? (
              <span role="group" aria-label={`처리 중 ${activeCount}건`} className="font-mn-mono text-xs text-mn-text">
                {activeCount}
              </span>
            ) : null}
            <p className="sr-only" aria-live="polite" aria-label="처리 상태 변경 안내">
              {announcement}
            </p>
          </>
        ) : null}
        <div id={contentId} className={`flex-col gap-6 ${folded ? "hidden" : "contents"}`}>
        <Link
          href={V2_UPLOAD}
          aria-current={uploadCurrent ? "page" : undefined}
          className="mn-focus inline-flex h-10 w-full items-center justify-center rounded-mn-control border border-mn-accent bg-mn-accent px-4 text-sm font-medium text-mn-on-accent hover:bg-mn-accent-hover max-md:h-11"
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
                  "mn-focus flex h-10 items-center rounded-mn-control px-3 text-sm max-md:h-11",
                  current ? "bg-mn-elevated font-medium text-mn-text" : "text-mn-muted hover:bg-mn-elevated hover:text-mn-text",
                ].join(" ")}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <ProcessingPanel />
        {mobile ? (
          <div className="sticky bottom-0 mt-auto flex flex-col gap-3 border-t border-mn-border bg-mn-bg pt-4">{userBlock}</div>
        ) : null}
        </div>
      </aside>
    </div>
  );
}

/** 아직 만들지 않은 화면의 빈 자리 */
export function PlaceholderPage({ title }: { title: string }) {
  return (
    <>
      <h1 title={title} className="mn-page-title text-mn-text">
        {title}
      </h1>
      <div className="rounded-mn-card border border-mn-border bg-mn-surface p-6 text-sm text-mn-muted">
        이 화면은 준비 중입니다.
      </div>
    </>
  );
}
