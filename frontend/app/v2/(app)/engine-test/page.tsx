"use client";

/*
 * 엔진 시험(작업 91-1, 시험 전용): 관리자 이상이 원격(OpenAI 호환) 엔진 주소·모델·키를 저장하고 로딩 테스트를 한다.
 * 시험 모드가 꺼져 있거나 담당자이면 /v2 로 돌려보낸다(화면 숨김은 편의이고 실제 거부는 서버가 한다).
 */
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/components/v2/AuthProvider";
import { useEngineTestMode } from "@/components/v2/EngineChoice";
import { EngineTestPanel } from "@/components/v2/EngineTestPanel";
import { isManager } from "@/lib/v2/types";

export default function EngineTestPage() {
  const { account } = useAuth();
  const router = useRouter();
  const allowedRole = isManager(account?.rank);
  const { testMode } = useEngineTestMode(account !== null);
  // 담당자는 바로 돌려보낸다. 시험 모드 여부는 조회가 끝나기 전에 판단하지 않기 위해 본문(EngineTestPanel)이 스스로 숨긴다
  useEffect(() => {
    if (account && !allowedRole) router.replace("/v2");
  }, [account, allowedRole, router]);
  if (!allowedRole || !testMode) return null;
  return (
    <>
      <h1 className="mn-page-title text-mn-text">엔진 시험</h1>
      <EngineTestPanel />
    </>
  );
}
