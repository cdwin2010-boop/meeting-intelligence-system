"use client";

/*
 * 시스템 관리 화면 틀(작업 82-2): 시스템 관리자(isSystemAdmin)만 들어올 수 있다. 아니면 /v2 로 돌려보낸다(화면 숨김은 편의이고
 * 실제 보호는 서버의 /api/admin 권한 검사다). 상단에 하위 화면 탭(계정·부서·시스템 설정·라이선스)을 둔다.
 */
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useAuth } from "@/components/v2/AuthProvider";

const TABS = [
  { href: "/v2/admin/accounts", label: "계정 관리" },
  { href: "/v2/admin/departments", label: "부서 관리" },
  { href: "/v2/admin/settings", label: "시스템 설정" },
  { href: "/v2/admin/license", label: "라이선스" },
];

export default function AdminLayout({ children }: { children: ReactNode }) {
  const { account, status } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const allowed = account?.isSystemAdmin === true;

  useEffect(() => {
    if (status === "authenticated" && !allowed) router.replace("/v2");
  }, [status, allowed, router]);

  if (!allowed) return null;

  return (
    <>
      <h1 className="mn-page-title text-mn-text">시스템 관리</h1>
      <nav aria-label="시스템 관리 메뉴" className="-mt-2 flex flex-wrap gap-2">
        {TABS.map((tab) => {
          const current = pathname === tab.href || pathname.startsWith(`${tab.href}/`);
          return (
            <Link
              key={tab.href}
              href={tab.href}
              aria-current={current ? "page" : undefined}
              className={[
                "mn-focus inline-flex min-h-10 items-center rounded-mn-control border px-3 text-sm whitespace-nowrap",
                current ? "border-mn-accent bg-mn-elevated font-medium text-mn-text" : "border-mn-control text-mn-muted hover:text-mn-text",
              ].join(" ")}
            >
              {tab.label}
            </Link>
          );
        })}
      </nav>
      {children}
    </>
  );
}
