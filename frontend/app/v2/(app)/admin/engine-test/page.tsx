"use client";

/* 시스템 관리 · 엔진 시험(작업 91-1, 시험 전용): 시험 모드가 켜져 있을 때만 본문이 그려진다(서버도 꺼져 있으면 403). */
import { EngineTestPanel } from "@/components/v2/EngineTestPanel";

export default function AdminEngineTestPage() {
  return <EngineTestPanel />;
}
