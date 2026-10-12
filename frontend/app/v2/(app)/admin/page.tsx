"use client";

// /v2/admin: 첫 화면은 계정 관리다.
import { useRouter } from "next/navigation";
import { useEffect } from "react";

export default function AdminIndexPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/v2/admin/accounts");
  }, [router]);
  return null;
}
