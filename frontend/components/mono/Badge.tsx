import type { ReactNode } from "react";

interface BadgeProps {
  children: ReactNode;
  variant?: "outline" | "solid";
  /** ID, 해시 등은 mono 폰트 */
  mono?: boolean;
}

export function Badge({ children, variant = "outline", mono = false }: BadgeProps) {
  const variantClass =
    variant === "solid"
      ? "bg-mn-accent text-mn-on-accent"
      : "border border-mn-border text-mn-text";

  return (
    <span
      className={[
        "inline-flex h-6 items-center rounded-mn-badge px-2 text-xs",
        mono ? "font-mn-mono" : "font-medium",
        variantClass,
      ].join(" ")}
    >
      {children}
    </span>
  );
}
