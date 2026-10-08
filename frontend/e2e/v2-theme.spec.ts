/*
 * v2 라이트/다크 테마 E2E(작업 68-1): 기본 라이트, 토글·저장·첫 페인트 전 적용, 토글 위치, 토큰 대비(WCAG), 하드코딩 색 가드, 주요 화면 렌더.
 * 실제 백엔드 없이 page.route 로 가로챈다(데이터는 모두 가상).
 */
import fs from "node:fs";
import path from "node:path";

import { expect, test, type Page, type Route } from "./helpers/test";
import { withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const THEME_KEY = "mi.v2.theme";
const ME = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const EMPTY_TODOS = {
  awaitingConfirmMeetings: { total: 0, items: [] }, needsCompletionItems: { total: 0, items: [] }, myItems: { total: 0, items: [] },
  unreadAutoConfirmed: { total: 0, items: [] }, pendingChangeRequests: { total: 0, items: [] },
};
const DETAIL = {
  id: 41, title: "주간 생산 현안 회의", heldAt: "2026-10-01T01:00:00Z", summary: "요약", decisions: [], status: "awaiting_confirmation", confirmKind: null,
  confirmedBy: null, confirmedAt: null, firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: "2026-10-06T01:00:00Z", registeredBy: { id: 7, name: "한팀장" },
  origin: "audio", participants: [{ id: 7, name: "한팀장" }], guestParticipants: [],
  actionItems: [{ id: 101, title: "업무", assignee: { id: 7, name: "한팀장" }, dueDate: "2026-10-05", dueUndetermined: false, status: "pending", confirmKind: null,
    evidenceStartSec: 12, evidenceQuote: "인용", needsCompletion: false, missingFields: [], origin: "ai" }],
  recentEvents: [], phase: "active", processing: null,
};

async function start(page: Page) {
  // 처리되지 않은 요청은 공용 기본 응답(e2e/helpers/test.ts)이 맡는다. 나중에 등록한 라우트가 먼저 적용된다
  await page.route("**/api/auth/me", (route) => (route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, ME) : json(route, { detail: "x" }, 401)));
  await page.route("**/api/me/todos", (route) => json(route, EMPTY_TODOS));
  await page.route("**/api/meetings?*", (route) => json(route, { items: [], total: 0, page: 1, size: 20, availablePhases: ["active", "ended", "on_hold", "deleted"] }));
  await page.route(/\/api\/meetings\/\d+$/, (route) => json(route, withAllowed(DETAIL as never, ME)));
  await page.route(/\/api\/meetings\/\d+\/transcript$/, (route) => json(route, { detail: "없음" }, 404));
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
}

const root = (page: Page) => page.evaluate(() => ({
  theme: document.documentElement.getAttribute("data-v2-theme"), scheme: getComputedStyle(document.documentElement).colorScheme,
}));
const shellBackground = (page: Page) => page.evaluate(() => getComputedStyle(document.querySelector("main")!.parentElement!.parentElement!).backgroundColor);
const toggle = (page: Page, current: "라이트" | "다크") => page.getByRole("button", { name: `화면 테마 전환, 현재 ${current}` });
const LIGHT_BG = "rgb(250, 249, 246)";
const DARK_BG = "rgb(0, 0, 0)";

test.describe("테마 선택·저장", () => {
  test("저장값이 없으면 라이트(속성·배경·color-scheme)", async ({ page }) => {
    await start(page);
    await page.goto("/v2");
    await expect(toggle(page, "라이트")).toHaveText("다크 모드");
    expect(await root(page)).toEqual({ theme: "light", scheme: "light" });
    expect(await shellBackground(page)).toBe(LIGHT_BG);
  });

  test("토글: 라이트 → 다크 → 라이트, 이름·글자 변화, 새로고침 후 유지", async ({ page }) => {
    await start(page);
    await page.goto("/v2");
    await toggle(page, "라이트").click();
    await expect(toggle(page, "다크")).toHaveText("라이트 모드");
    expect(await root(page)).toEqual({ theme: "dark", scheme: "dark" });
    expect(await shellBackground(page)).toBe(DARK_BG);
    expect(await page.evaluate((k) => window.localStorage.getItem(k), THEME_KEY)).toBe("dark");
    await page.reload();
    await expect(toggle(page, "다크")).toHaveText("라이트 모드");
    expect(await root(page)).toEqual({ theme: "dark", scheme: "dark" });
    await toggle(page, "다크").click();
    await expect(toggle(page, "라이트")).toHaveText("다크 모드");
    expect(await page.evaluate((k) => window.localStorage.getItem(k), THEME_KEY)).toBe("light");
    // aria-pressed 는 쓰지 않는다
    await expect(toggle(page, "라이트")).not.toHaveAttribute("aria-pressed");
  });

  test("Enter·Space 로 조작되고 포커스가 버튼에 남는다", async ({ page }) => {
    await start(page);
    await page.goto("/v2");
    await toggle(page, "라이트").focus();
    await page.keyboard.press("Enter");
    await expect(toggle(page, "다크")).toBeFocused();
    await page.keyboard.press("Space");
    await expect(toggle(page, "라이트")).toBeFocused();
  });

  test("저장소 접근이 막혀도 라이트로 동작하고 전환된다", async ({ page }) => {
    await start(page);
    await page.addInitScript(() => {
      Object.defineProperty(window, "localStorage", { get: () => { throw new DOMException("blocked", "SecurityError"); } });
    });
    await page.goto("/v2");
    await expect(toggle(page, "라이트")).toBeVisible();
    expect((await root(page)).theme).toBe("light");
    await toggle(page, "라이트").click();
    await expect(toggle(page, "다크")).toBeVisible();
    expect((await root(page)).theme).toBe("dark");
  });

  test("잘못된 저장값은 라이트", async ({ page }) => {
    await start(page);
    await page.evaluate(([k]) => window.localStorage.setItem(k, "purple"), [THEME_KEY]);
    await page.goto("/v2");
    await expect(toggle(page, "라이트")).toBeVisible();
    expect((await root(page)).theme).toBe("light");
  });

  test("저장된 값이 dark 면 DOMContentLoaded 시점(첫 페인트 전)에 속성이 이미 dark", async ({ page }) => {
    await start(page);
    await page.evaluate(([k]) => window.localStorage.setItem(k, "dark"), [THEME_KEY]);
    await page.addInitScript(() => {
      document.addEventListener("DOMContentLoaded", () => {
        (window as unknown as { __atDcl: string | null }).__atDcl = document.documentElement.getAttribute("data-v2-theme");
      });
    });
    await page.goto("/v2");
    await expect(toggle(page, "다크")).toBeVisible();
    expect(await page.evaluate(() => (window as unknown as { __atDcl: string | null }).__atDcl)).toBe("dark");
  });
});

for (const width of [1280, 768]) {
  test(`토글 위치(데스크톱 ${width}px): 헤더의 사용자 표시 왼쪽, 패널 접기는 로그아웃 오른쪽 그대로`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    await start(page);
    await page.goto("/v2");
    const header = page.locator("header").first();
    await expect(page.getByRole("button", { name: /화면 테마 전환/ })).toHaveCount(1);
    const box = async (l: ReturnType<Page["locator"]>) => (await l.boundingBox())!;
    const theme = await box(header.getByRole("button", { name: /화면 테마 전환/ }));
    const user = await box(header.getByRole("group", { name: "로그인 사용자" }));
    const logout = await box(header.getByRole("button", { name: "로그아웃" }));
    const panel = await box(header.getByRole("button", { name: /회의록 추적 패널/ }));
    expect(theme.x + theme.width).toBeLessThanOrEqual(user.x + 0.5);
    expect(user.x + user.width).toBeLessThanOrEqual(logout.x + 0.5);
    expect(logout.x + logout.width).toBeLessThanOrEqual(panel.x + 0.5);
    for (const other of [user, logout, panel]) expect(Math.abs(theme.y + theme.height / 2 - (other.y + other.height / 2))).toBeLessThan(6); // 한 줄
    expect(await header.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    // Tab 순서 = 화면 순서(테마 → 로그아웃 → 패널 접기)
    await page.locator("body").click({ position: { x: 5, y: 5 } });
    const order: string[] = [];
    for (let i = 0; i < 4; i += 1) {
      await page.keyboard.press("Tab");
      order.push(await page.evaluate(() => document.activeElement?.getAttribute("aria-label") ?? document.activeElement?.textContent ?? ""));
    }
    const headerOrder = order.filter((name) => /화면 테마 전환|로그아웃|회의록 추적 패널/.test(name));
    expect(headerOrder.map((n) => (n.startsWith("화면 테마") ? "theme" : n === "로그아웃" ? "logout" : "panel"))).toEqual(["theme", "logout", "panel"]);
  });
}

for (const width of [767, 375]) {
  test(`토글 위치(모바일 ${width}px): 드로어 하단 사용자 표시·로그아웃과 함께, DOM 에 하나뿐`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    await start(page);
    await page.goto("/v2");
    await expect(page.getByRole("button", { name: "메뉴 열기" })).toBeVisible(); // 모바일 구조로 바뀐 뒤(닫힌 드로어 안의 버튼은 접근성 트리에 없으므로 CSS 로 센다)
    await expect(page.locator('button[aria-label^="화면 테마 전환"]')).toHaveCount(1);
    await expect(page.locator("header").first().locator('button[aria-label^="화면 테마 전환"]')).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.getByRole("button", { name: "메뉴 열기" }).click();
    const aside = page.locator("aside");
    const theme = (await aside.getByRole("button", { name: /화면 테마 전환/ }).boundingBox())!;
    const logout = (await aside.getByRole("button", { name: "로그아웃" }).boundingBox())!;
    expect(theme.y).toBeLessThan(logout.y); // 같은 하단 묶음(위: 테마, 사용자 표시, 아래: 로그아웃)
    expect(logout.y + logout.height).toBeGreaterThan(700);
    expect(theme.height).toBeGreaterThanOrEqual(44);
    await aside.getByRole("button", { name: /화면 테마 전환/ }).click();
    expect((await root(page)).theme).toBe("dark");
  });
}

// ---------------- 토큰 대비(WCAG) ----------------
function channel(v: number) {
  const c = v / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}
function luminance(hex: string) {
  const h = hex.trim().replace("#", "");
  const full = h.length === 3 ? h.split("").map((x) => x + x).join("") : h.slice(0, 6);
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16));
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}
function ratio(a: string, b: string) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

async function tokens(page: Page, theme: "light" | "dark") {
  await page.evaluate((t) => document.documentElement.setAttribute("data-v2-theme", t), theme);
  return page.evaluate(() => {
    const style = getComputedStyle(document.documentElement);
    const names = ["background", "surface", "surface-elevated", "border", "control-border", "text", "text-muted", "text-disabled", "link", "accent", "on-accent",
      "red", "on-red", "focus", "selected"];
    const chips = ["neutral", "info", "success", "warning", "muted", "danger", "superseded", "cancelled"];
    const out: Record<string, string> = {};
    for (const n of names) out[n] = style.getPropertyValue(`--mn-${n}`).trim();
    for (const c of chips) for (const part of ["bg", "fg"]) out[`chip-${c}-${part}`] = style.getPropertyValue(`--mn-chip-${c}-${part}`).trim();
    return out;
  });
}

const CHIPS = ["neutral", "info", "success", "warning", "muted", "danger", "superseded", "cancelled"];

test.describe("토큰 대비", () => {
  test("라이트: 글자 4.5:1, 컨트롤 보더·포커스 링 3:1, 칩 4.5:1", async ({ page }) => {
    await page.goto("/v2/login");
    const t = await tokens(page, "light");
    const grounds = [t.background, t["surface-elevated"], t.surface];
    for (const ground of grounds) {
      expect(ratio(t.text, ground)).toBeGreaterThanOrEqual(4.5);
      expect(ratio(t["text-muted"], ground)).toBeGreaterThanOrEqual(4.5);
      expect(ratio(t.link, ground)).toBeGreaterThanOrEqual(4.5);
      expect(ratio(t["control-border"], ground)).toBeGreaterThanOrEqual(3);
      expect(ratio(t.focus, ground)).toBeGreaterThanOrEqual(3);
    }
    expect(ratio(t["text-muted"], t.selected)).toBeGreaterThanOrEqual(4.5);
    expect(ratio(t["on-accent"], t.accent)).toBeGreaterThanOrEqual(4.5);
    expect(ratio(t["on-red"], t.red)).toBeGreaterThanOrEqual(4.5);
    for (const chip of CHIPS) expect(ratio(t[`chip-${chip}-fg`], t[`chip-${chip}-bg`]), `칩 ${chip}`).toBeGreaterThanOrEqual(4.5);
    // 비활성 글자는 WCAG 대비 예외(보고만): 값이 정의돼 있고 본문 글자보다 옅다
    expect(ratio(t["text-disabled"], t.surface)).toBeLessThan(ratio(t["text-muted"], t.surface));
    // 카드 보더는 장식(구분선)이라 3:1 대상이 아니다: 컨트롤 보더는 카드 보더보다 진하다
    expect(ratio(t["control-border"], t.surface)).toBeGreaterThan(ratio(t.border, t.surface));
  });

  test("다크: 현재 값 그대로(본문·보조·주 버튼·칩 4.5:1, 포커스 3:1). 컨트롤 보더 3:1 미달은 알려진 항목", async ({ page }) => {
    await page.goto("/v2/login");
    const t = await tokens(page, "dark");
    expect([t.background, t.surface, t.border, t.text, t["text-muted"], t.accent, t["on-accent"], t.red, t.focus, t["control-border"]]).toEqual(
      ["#000000", "#0a0a0a", "#262626", "#ededed", "#a1a1a1", "#ffffff", "#000000", "#ee0000", "#a3a3a3", "#262626"],
    );
    for (const ground of [t.background, t.surface]) {
      expect(ratio(t.text, ground)).toBeGreaterThanOrEqual(4.5);
      expect(ratio(t["text-muted"], ground)).toBeGreaterThanOrEqual(4.5);
      expect(ratio(t.focus, ground)).toBeGreaterThanOrEqual(3);
    }
    expect(ratio(t["on-accent"], t.accent)).toBeGreaterThanOrEqual(4.5);
    expect(ratio(t["on-red"], t.red)).toBeGreaterThanOrEqual(4.5);
    for (const chip of CHIPS) expect(ratio(t[`chip-${chip}-fg`], t[`chip-${chip}-bg`]), `칩 ${chip}`).toBeGreaterThanOrEqual(4.5);
    // 알려진 항목: 다크 컨트롤 테두리(#262626)는 검정 위 약 1.4 로 3:1 미달(이후 3단계에서 보정). 단언에서 제외한다.
  });
});

// ---------------- 하드코딩 색 가드 ----------------
const SCAN_DIRS = ["app/v2", "components/v2", "components/mono"];
const PALETTE = "white|black|slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose";
const FORBIDDEN = [
  /#[0-9a-fA-F]{3,8}\b/,
  /\brgba?\(/,
  /\bhsla?\(/,
  new RegExp(`\\b(?:bg|text|border|ring|fill|stroke|from|to|via|divide|placeholder|accent|caret|outline|decoration|shadow)-(?:${PALETTE})(?:-\\d+)?\\b`),
];
// 허용 목록: 없음(토큰 정의는 app/mono-dark/*.css 에만 있고 이 검사 범위 밖이다)
const ALLOWED: string[] = [];

function walk(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    return e.isDirectory() ? walk(full) : /\.(ts|tsx|css)$/.test(e.name) ? [full] : [];
  });
}
function stripComments(source: string) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .map((line) => (/^\s*\/\//.test(line) ? "" : line.replace(/\s\/\/\s.*$/, "")))
    .join("\n");
}

test("하드코딩 색 가드: v2·mono 컴포넌트 소스에 #hex·rgb()·hsl()·Tailwind 팔레트 색이 없다", () => {
  const offenders: string[] = [];
  for (const dir of SCAN_DIRS) {
    for (const file of walk(path.join(process.cwd(), dir))) {
      const rel = path.relative(process.cwd(), file).replaceAll("\\", "/");
      if (ALLOWED.includes(rel)) continue;
      stripComments(fs.readFileSync(file, "utf-8")).split("\n").forEach((line, i) => {
        if (FORBIDDEN.some((re) => re.test(line))) offenders.push(`${rel}:${i + 1}: ${line.trim().slice(0, 100)}`);
      });
    }
  }
  expect(offenders).toEqual([]);
});

// ---------------- 주요 화면 렌더 ----------------
for (const theme of ["light", "dark"] as const) {
  test(`주요 화면이 ${theme} 테마에서 콘솔 오류 없이 토큰 배경으로 그려진다`, async ({ page }) => {
    const problems: string[] = [];
    page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()}`); });
    page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
    await start(page);
    await page.evaluate(([k, v]) => window.localStorage.setItem(k, v), [THEME_KEY, theme]);
    const expected = theme === "light" ? LIGHT_BG : DARK_BG;
    await page.goto("/v2/login");
    await expect(page.locator("form").first()).toBeVisible();
    expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe(expected);
    for (const [url, ready] of [
      ["/v2", page.getByRole("heading", { level: 1 })],
      ["/v2/meetings", page.getByRole("heading", { level: 1 })],
      ["/v2/meetings/41", page.getByRole("heading", { level: 1, name: "주간 생산 현안 회의" })],
      ["/v2/upload", page.getByRole("heading", { level: 1, name: "회의록 올리기" })],
    ] as const) {
      await page.goto(url);
      await expect(ready.first()).toBeVisible();
      expect(await root(page)).toEqual({ theme, scheme: theme });
      expect(await shellBackground(page), url).toBe(expected);
    }
    expect(problems).toEqual([]);
  });
}
