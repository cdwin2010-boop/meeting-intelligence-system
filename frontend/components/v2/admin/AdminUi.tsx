"use client";

/*
 * 시스템 관리자 화면 공용 조각(작업 82-2): 입력칸, 오류 문구, 상태 칩, 날짜 표시.
 * 색은 토큰만 쓴다(하드코딩 없음). 가로 넘침을 막기 위해 긴 값은 break-words/min-w-0 로 줄바꿈한다.
 */
import { useId, type ReactNode } from "react";

import { ApiError, NetworkError } from "@/lib/v2/errors";

export const inputClass =
  "mn-focus h-10 w-full min-w-0 rounded-mn-control border border-mn-control bg-mn-bg px-3 text-sm text-mn-text outline-none placeholder:text-mn-muted disabled:opacity-50";

/** 서버가 준 문구가 있으면 그대로(자기 자신·마지막 관리자 해제 거부 같은 규칙 문구 포함), 없으면 일반 문구 */
export function messageOf(error: unknown, fallback = "처리하지 못했습니다. 잠시 뒤 다시 시도해 주세요."): string {
  if (error instanceof ApiError || error instanceof NetworkError) return error.message || fallback;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

export function TextField({
  label,
  value,
  onChange,
  type = "text",
  autoComplete,
  disabled,
  required,
  hint,
  name,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: "text" | "password" | "email";
  autoComplete?: string;
  disabled?: boolean;
  required?: boolean;
  hint?: string;
  name?: string;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <label htmlFor={id} className="text-sm font-medium">
        {label} {required ? <span className="font-normal text-mn-muted">(필수)</span> : null}
      </label>
      <input
        id={id}
        name={name}
        type={type}
        autoComplete={autoComplete}
        value={value}
        disabled={disabled}
        aria-describedby={hint ? hintId : undefined}
        onChange={(event) => onChange(event.target.value)}
        className={inputClass}
      />
      {hint ? (
        <p id={hintId} className="text-xs text-mn-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function SelectField({
  label,
  value,
  onChange,
  options,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <select id={id} value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)} className={inputClass}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}

export function Chip({ tone, children }: { tone: "neutral" | "info" | "success" | "warning" | "muted" | "danger"; children: ReactNode }) {
  return <span className={`mn-chip mn-chip-${tone} whitespace-nowrap`}>{children}</span>;
}

/** ISO 시각 → 브라우저 현지 "YYYY-MM-DD HH:mm". 값이 없거나 잘못되면 "—" */
export function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return `${date.toLocaleDateString("sv-SE")} ${date.toLocaleTimeString("sv-SE", { hour: "2-digit", minute: "2-digit" })}`;
}

export function Card({ children, label }: { children: ReactNode; label?: string }) {
  return (
    <section aria-label={label} className="min-w-0 rounded-mn-card border border-mn-border bg-mn-surface p-4 md:p-5">
      {children}
    </section>
  );
}
