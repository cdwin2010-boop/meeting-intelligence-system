"use client";

/*
 * v2 화면 테마(작업 68-1): 라이트(기본) / 다크(기존 Mono Dark). 선택은 html 의 data-v2-theme 속성으로 표현하고 CSS 토큰이 따라간다(app/mono-dark/theme-v2.css).
 * - 저장: 기기별 브라우저 저장소 mi.v2.theme = "light" | "dark". 값이 없거나 잘못됐거나 저장소가 막히면 라이트. 운영체제 설정은 따르지 않는다.
 * - 첫 페인트 전 적용: app/v2/layout.tsx 가 THEME_INIT_SCRIPT(인라인 스크립트)를 body 맨 앞에 넣어, 첫 화면이 그려지기 전에 속성을 붙인다.
 *   (루트 html 에 suppressHydrationWarning 을 둬서 스크립트가 붙인 속성이 하이드레이션 경고를 만들지 않게 한다)
 * - 첫 렌더 상태는 항상 "light" 로 서버와 맞추고, 마운트 직후(페인트 전) 속성에서 실제 값을 읽는다.
 * - v2 를 떠나면 속성을 지워 v1 화면이 바뀌지 않게 한다.
 */
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from "react";

export type Theme = "light" | "dark";
export const THEME_KEY = "mi.v2.theme";
const ATTRIBUTE = "data-v2-theme";

/** 첫 페인트 전에 실행되는 인라인 스크립트(저장소 접근이 막혀도 라이트로 진행) */
export const THEME_INIT_SCRIPT = `(function(){try{var t=null;try{t=window.localStorage.getItem("${THEME_KEY}")}catch(e){}if(t!=="dark")t="light";document.documentElement.setAttribute("${ATTRIBUTE}",t)}catch(e){}})();`;

function readStored(): Theme {
  try {
    return window.localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

interface ThemeContextValue {
  theme: Theme;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeContextValue>({ theme: "light", toggle: () => undefined });
export const useTheme = () => useContext(ThemeContext);

const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>("light");

  useIsoLayoutEffect(() => {
    const root = document.documentElement;
    const attr = root.getAttribute(ATTRIBUTE);
    const initial: Theme = attr === "dark" || attr === "light" ? attr : readStored();
    root.setAttribute(ATTRIBUTE, initial);
    setTheme(initial);
    return () => root.removeAttribute(ATTRIBUTE);
  }, []);

  const toggle = useCallback(() => {
    const next: Theme = document.documentElement.getAttribute(ATTRIBUTE) === "dark" ? "light" : "dark";
    document.documentElement.setAttribute(ATTRIBUTE, next);
    setTheme(next);
    try {
      window.localStorage.setItem(THEME_KEY, next);
    } catch {
      // 저장 실패는 무시(이번 방문 동안만 유지)
    }
  }, []);

  const value = useMemo(() => ({ theme, toggle }), [theme, toggle]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
