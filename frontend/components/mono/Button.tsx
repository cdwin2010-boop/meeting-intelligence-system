import { forwardRef, type ButtonHTMLAttributes } from "react";

type ButtonVariant = "primary" | "secondary" | "danger";
type ButtonSize = "md" | "sm";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

// 변형별 색상 규칙
// - primary: 흰색 배경 (화면당 1개만 권장)
// - secondary: 투명 + 1px 테두리
// - danger: 파괴적 액션 전용 Red (유채색이 버튼 채움으로 허용되는 유일한 경우)
const VARIANT_CLASS: Record<ButtonVariant, string> = {
  primary: "bg-mn-accent text-mn-on-accent hover:bg-mn-accent-hover",
  secondary: "border border-mn-border bg-transparent text-mn-text hover:bg-mn-elevated",
  danger: "bg-mn-red text-mn-on-red hover:bg-mn-red-hover",
};

const SIZE_CLASS: Record<ButtonSize, string> = {
  md: "h-10 px-4 text-sm", // 40px
  sm: "h-8 px-3 text-[13px]", // 32px
};

// forwardRef: 부모(Modal)가 "Cancel 버튼에 포커스를 주기 위해" DOM 노드에 접근할 수 있게 해줍니다.
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", className = "", type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={[
        "mn-focus inline-flex items-center justify-center gap-2 rounded-mn-control font-medium",
        "transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        VARIANT_CLASS[variant],
        SIZE_CLASS[size],
        className,
      ].join(" ")}
      {...rest}
    />
  );
});
