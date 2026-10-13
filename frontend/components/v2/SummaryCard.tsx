/* 요약 카드(Clean Light): 왼쪽 44×44 숫자 상자 + 제목 + 보조 문장. 숫자는 mono 글꼴 */
import type { ReactNode } from "react";

export function SummaryCard({ label, value, hint }: { label: string; value: ReactNode; hint: string }) {
  return (
    <div className="flex items-center gap-3 rounded-mn-card border border-mn-border bg-mn-surface p-4">
      <span className="flex size-11 shrink-0 items-center justify-center rounded-mn-item bg-mn-brand-tint font-mn-mono text-[17px] font-bold leading-6 text-mn-brand-ink">
        {value}
      </span>
      <div className="min-w-0">
        <p className="text-[15px] font-semibold leading-[23px] text-mn-text">{label}</p>
        <p className="text-[13px] leading-5 text-mn-muted">{hint}</p>
      </div>
    </div>
  );
}
