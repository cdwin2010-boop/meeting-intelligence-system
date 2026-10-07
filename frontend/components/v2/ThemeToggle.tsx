"use client";

/* 화면 테마 전환 버튼. 보이는 글자는 전환될 대상("다크 모드" / "라이트 모드"), 접근성 이름은 현재 상태를 알린다. aria-pressed 는 쓰지 않는다 */
import { Button } from "@/components/mono";
import { useTheme } from "@/components/v2/ThemeProvider";

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  return (
    <Button
      size="sm"
      aria-label={`화면 테마 전환, 현재 ${theme === "light" ? "라이트" : "다크"}`}
      onClick={toggle}
      className="shrink-0 whitespace-nowrap max-md:min-h-11"
    >
      {theme === "light" ? "다크 모드" : "라이트 모드"}
    </Button>
  );
}
