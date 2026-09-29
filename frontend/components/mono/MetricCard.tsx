import type { ReactNode } from "react";

interface MetricCardProps {
  label: string;
  /** 수치는 항상 mono 폰트(24/32, weight 500)로 표시됩니다 */
  value: ReactNode;
  /** 값 아래 보조 설명 (예: "queued 1 · processing 2") */
  hint?: ReactNode;
}

// 카드 = surface(#0A0A0A) + 1px border. 그림자는 쓰지 않습니다.
export function MetricCard({ label, value, hint }: MetricCardProps) {
  return (
    <div className="rounded-mn-card border border-mn-border bg-mn-surface p-4">
      <p className="text-xs text-mn-muted">{label}</p>
      <p className="mt-2 font-mn-mono text-2xl font-medium leading-8 text-mn-text">{value}</p>
      {hint ? <p className="mt-1 font-mn-mono text-xs text-mn-muted">{hint}</p> : null}
    </div>
  );
}
