/*
 * v3.0 UI/UX 1단계 E2E: 앱 틀(드로어·헤더 뱃지·업로드 버튼·보는 중)과 회의록 목록 반응형(카드·탭·페이지 이동).
 * 실제 백엔드 없이 page.route 로 가로챈다(데이터는 모두 가상). 기준 너비는 768(768 미만 = 모바일).
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const ME = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };

const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const meetingRow = (id: number, title: string) => ({
  id, title, heldAt: "2026-10-01T01:00:00Z", registeredBy: { id: 7, name: "한팀장" }, origin: "audio_minutes", status: "confirmed",
  confirmKind: "manager", itemCount: 3, needsCompletionCount: 0, autoConfirmAt: null, phase: "active",
});

const detailOf = (id: number) => ({
  id, title: `회의 ${id}`, heldAt: "2026-10-01T01:00:00Z", summary: "", decisions: [], status: "confirmed", confirmKind: null, confirmedBy: null,
  confirmedAt: null, firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: null, registeredBy: { id: 7, name: "한팀장" }, origin: "audio_minutes",
  participants: [], guestParticipants: [], recentEvents: [], phase: "active", processing: null, actionItems: [],
});

const proc = (jobId: number, extra: Record<string, unknown> = {}) => ({
  meetingId: 100 + jobId, jobId, title: `회의 ${jobId}`, status: "running", errorCode: null, elapsedSec: 5, finishedAt: null, canReprocess: false, ...extra,
});

interface Server {
  processing: unknown[];
  list: { items: unknown[]; total: number };
}

async function start(page: Page, opts: Partial<Server> = {}): Promise<Server> {
  const server: Server = { processing: [], list: { items: [meetingRow(1, "주간 생산 현안 회의"), meetingRow(2, "아주 긴 회의명이 들어가면 카드에서는 여러 줄로 줄바꿈되어야 합니다 3호기 납기 조정 관련 협력사 일정 공유 회의")], total: 2 }, ...opts };
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, ME) : json(route, { detail: "인증 필요" }, 401),
  );
  await page.route("**/api/me/processing", (route) => json(route, server.processing));
  await page.route("**/api/meetings?*", (route) => json(route, { ...server.list, page: 1, size: 20, availablePhases: ["active", "ended", "on_hold", "deleted"] }));
  await page.route(/\/api\/meetings\/(\d+)$/, (route) => json(route, withAllowed(detailOf(Number(/meetings\/(\d+)$/.exec(route.request().url())![1])) as never, ME)));
  await page.route(/\/api\/meetings\/\d+\/(transcript|audio-url)$/, (route) => json(route, { detail: "없음" }, 404));
  await page.route(/\/api\/meetings\/\d+\/(speakers|change-requests)$/, (route) => json(route, []));
  await page.route("**/api/accounts", (route) => json(route, []));
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
  return server;
}

const aside = (page: Page) => page.locator("aside");
const hamburger = (page: Page) => page.getByRole("button", { name: "메뉴 열기" });
const mainNav = (page: Page) => page.getByRole("navigation", { name: "주 메뉴" });

for (const width of [767, 375]) {
  test.describe(`모바일 ${width}px: 앱 틀 드로어`, () => {
    test.use({ viewport: { width, height: 800 } });

    test("사이드바가 숨고 햄버거가 보이며 aria-expanded 가 열림 상태를 따른다", async ({ page }) => {
      await start(page);
      await page.goto("/v2");
      await expect(hamburger(page)).toBeVisible();
      await expect(hamburger(page)).toHaveAttribute("aria-expanded", "false");
      await expect(aside(page)).toBeHidden();
      await expect(mainNav(page).getByRole("link", { name: "회의록" })).toBeHidden();
      await hamburger(page).click();
      await expect(hamburger(page)).toHaveAttribute("aria-expanded", "true");
      await expect(hamburger(page)).toHaveAttribute("aria-controls", /.+/);
      expect(await aside(page).getAttribute("id")).toBe(await hamburger(page).getAttribute("aria-controls"));
      await expect(aside(page)).toBeVisible();
      await expect(mainNav(page).getByRole("link", { name: "회의록" })).toBeVisible();
      await expect(page.getByRole("link", { name: "회의록 올리기" })).toBeVisible();
    });

    test("드로어 하단에 사용자 표시와 로그아웃(접근성 이름 유지), 메뉴 링크는 DOM 에 한 번만", async ({ page }) => {
      await start(page);
      await page.goto("/v2");
      await hamburger(page).click();
      const group = aside(page).getByRole("group", { name: "로그인 사용자" });
      await expect(group).toContainText("한팀장");
      await expect(group).toContainText("중간관리자");
      await expect(aside(page).getByRole("button", { name: "로그아웃" })).toBeVisible();
      await expect(page.getByLabel("로그인 사용자")).toHaveCount(1);
      await expect(page.getByRole("button", { name: "로그아웃" })).toHaveCount(1);
      await expect(page.getByRole("navigation", { name: "주 메뉴" })).toHaveCount(1);
      await expect(page.getByRole("link", { name: "회의록", exact: true })).toHaveCount(1);
      // 하단에 고정(드로어 바닥 근처)
      const box = await aside(page).getByRole("button", { name: "로그아웃" }).boundingBox();
      expect(box!.y + box!.height).toBeGreaterThan(700);
      await aside(page).getByRole("button", { name: "로그아웃" }).click();
      await expect(page).toHaveURL(/\/v2\/login/);
    });

    test("Esc 로 닫히고 햄버거로 포커스가 돌아온다", async ({ page }) => {
      await start(page);
      await page.goto("/v2");
      await hamburger(page).click();
      await page.keyboard.press("Escape");
      await expect(aside(page)).toBeHidden();
      await expect(hamburger(page)).toBeFocused();
      await expect(hamburger(page)).toHaveAttribute("aria-expanded", "false");
    });

    test("바깥(배경) 클릭으로 닫히고 햄버거로 포커스 복귀", async ({ page }) => {
      await start(page);
      await page.goto("/v2");
      await hamburger(page).click();
      await page.mouse.click(10, 400); // 드로어가 오른쪽에서 열리므로 배경은 왼쪽
      await expect(aside(page)).toBeHidden();
      await expect(hamburger(page)).toBeFocused();
    });

    test("경로가 바뀌면 자동으로 닫힌다", async ({ page }) => {
      await start(page);
      await page.goto("/v2");
      await hamburger(page).click();
      await mainNav(page).getByRole("link", { name: "회의록" }).click();
      await expect(page).toHaveURL(/\/v2\/meetings$/);
      await expect(aside(page)).toBeHidden();
      await expect(hamburger(page)).toHaveAttribute("aria-expanded", "false");
    });

    test("포커스가 드로어 안에 갇힌다(Tab·Shift+Tab 순환)", async ({ page }) => {
      await start(page);
      await page.goto("/v2");
      await hamburger(page).click();
      const insideAside = () => page.evaluate(() => document.querySelector("aside")!.contains(document.activeElement));
      expect(await insideAside()).toBe(true);
      for (let i = 0; i < 14; i += 1) {
        await page.keyboard.press("Tab");
        expect(await insideAside()).toBe(true);
      }
      for (let i = 0; i < 14; i += 1) {
        await page.keyboard.press("Shift+Tab");
        expect(await insideAside()).toBe(true);
      }
    });

    test("열려 있는 동안 본문 스크롤이 잠기고 닫으면 풀린다", async ({ page }) => {
      await start(page);
      await page.goto("/v2");
      expect(await page.evaluate(() => document.body.style.overflow)).toBe("");
      await hamburger(page).click();
      expect(await page.evaluate(() => document.body.style.overflow)).toBe("hidden");
      await page.keyboard.press("Escape");
      expect(await page.evaluate(() => document.body.style.overflow)).toBe("");
    });

    test("터치 영역: 메뉴 링크·햄버거·닫기 버튼이 44px 이상", async ({ page }) => {
      await start(page);
      await page.goto("/v2");
      const size = async (locator: ReturnType<Page["locator"]>) => (await locator.boundingBox())!;
      const burger = await size(hamburger(page));
      expect(burger.width).toBeGreaterThanOrEqual(44);
      expect(burger.height).toBeGreaterThanOrEqual(44);
      await hamburger(page).click();
      for (const name of ["할 일", "회의록"]) expect((await size(mainNav(page).getByRole("link", { name, exact: true }))).height).toBeGreaterThanOrEqual(44);
      const close = await size(page.getByRole("button", { name: "메뉴 닫기" }));
      expect(close.width).toBeGreaterThanOrEqual(44);
      expect(close.height).toBeGreaterThanOrEqual(44);
      expect((await size(aside(page).getByRole("button", { name: "로그아웃" }))).height).toBeGreaterThanOrEqual(44);
    });

    test("드로어 내부 스크롤은 본문과 분리(overscroll-behavior: contain)", async ({ page }) => {
      await start(page);
      await page.goto("/v2");
      await hamburger(page).click();
      expect(await aside(page).evaluate((el) => getComputedStyle(el).overscrollBehaviorY)).toBe("contain");
      expect(await aside(page).evaluate((el) => getComputedStyle(el).height)).toBe("800px"); // dvh 기준 화면 높이
    });
  });
}

test.describe("모바일: 헤더 처리 현황 뱃지", () => {
  test.use({ viewport: { width: 375, height: 800 } });

  test("처리 중 N건 → 누르면 드로어와 처리 현황이 보인다", async ({ page }) => {
    await start(page, { processing: [proc(1), proc(2, { status: "queued" })] });
    await page.goto("/v2");
    const badge = page.getByRole("button", { name: /처리 현황 요약/ });
    await expect(badge).toHaveText("처리 중 2건");
    await badge.click();
    await expect(aside(page)).toBeVisible();
    await expect(aside(page).getByLabel("처리 현황", { exact: true })).toContainText("처리 중 · 경과");
    await expect(aside(page).getByLabel("처리 현황", { exact: true })).toBeInViewport();
  });

  test("완료·실패가 있으면 '완료 N · 실패 N' 글자, 건수가 모두 0이면 숨김", async ({ page }) => {
    const server = await start(page, { processing: [proc(1, { status: "completed" }), proc(2, { status: "failed", errorCode: "timeout" }), proc(3, { status: "no_content" })] });
    await page.goto("/v2");
    await expect(page.getByRole("button", { name: /처리 현황 요약/ })).toHaveText("완료 1 · 실패 2");
    server.processing = [];
    await page.reload();
    await expect(hamburger(page)).toBeVisible();
    await expect(page.getByRole("button", { name: /처리 현황 요약/ })).toHaveCount(0);
  });

  test("진행 중과 완료가 함께 있으면 두 글자를 모두 보여 준다", async ({ page }) => {
    await start(page, { processing: [proc(1), proc(2, { status: "completed" })] });
    await page.goto("/v2");
    await expect(page.getByRole("button", { name: /처리 현황 요약/ })).toHaveText("처리 중 1건 · 완료 1 · 실패 0");
  });
});

for (const width of [768, 1280]) {
  test.describe(`데스크톱 ${width}px: 사이드바 고정`, () => {
    test.use({ viewport: { width, height: 800 } });

    test("사이드바가 보이고 메뉴 링크가 DOM 에 한 번만, 햄버거·뱃지 없음", async ({ page }) => {
      await start(page, { processing: [proc(1)] });
      await page.goto("/v2");
      await expect(aside(page)).toBeVisible();
      await expect(page.getByRole("navigation")).toHaveCount(1);
      await expect(mainNav(page).getByRole("link", { name: "회의록" })).toHaveCount(1);
      await expect(page.getByRole("link", { name: "회의록 올리기" })).toHaveCount(1);
      await expect(hamburger(page)).toHaveCount(0);
      await expect(page.getByRole("button", { name: /처리 현황 요약/ })).toHaveCount(0);
      await expect(page.getByRole("group", { name: "로그인 사용자" })).toContainText("한팀장"); // 헤더에 그대로
      await expect(page.locator("header").getByRole("button", { name: "로그아웃" })).toBeVisible();
      await expect(aside(page).getByLabel("처리 현황", { exact: true })).toContainText("처리 중 · 경과");
    });
  });
}

test.describe("헤더 '새 회의 업로드'와 '보는 중'", () => {
  test("누르면 업로드 화면으로 이동하고 업로드 화면에서는 숨는다(기존 메뉴 '회의록 올리기'와 이름이 다르다)", async ({ page }) => {
    await start(page);
    await page.goto("/v2");
    const button = page.getByRole("link", { name: "새 회의 업로드" });
    await expect(button).toBeVisible();
    await expect(page.getByRole("link", { name: "회의록 올리기" })).toHaveCount(1);
    await button.click();
    await expect(page).toHaveURL(/\/v2\/upload$/);
    await expect(page.getByRole("heading", { level: 1, name: "회의록 올리기" })).toBeVisible();
    await expect(page.getByRole("link", { name: "새 회의 업로드" })).toHaveCount(0);
  });

  test("모바일에서도 보인다", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await start(page);
    await page.goto("/v2/meetings");
    await expect(page.getByRole("link", { name: "새 회의 업로드" })).toBeVisible();
  });

  test("지금 보는 회의록이면 [열기] 대신 '보는 중' 글자, 다른 회의록이면 [열기]로 이동", async ({ page }) => {
    await start(page, { processing: [proc(1, { status: "completed", title: "보는 회의" }), proc(2, { status: "completed", title: "다른 회의" })] });
    await page.goto("/v2/meetings/101");
    await expect(page.getByRole("heading", { level: 1, name: "회의 101" })).toBeVisible();
    const panel = aside(page).getByLabel("처리 현황", { exact: true });
    await expect(panel.getByLabel("보는 회의 보는 중")).toHaveText("보는 중");
    await expect(panel.getByRole("button", { name: "보는 회의 열기" })).toHaveCount(0);
    await expect(panel.getByRole("button", { name: "다른 회의 열기" })).toBeVisible();
    await panel.getByRole("button", { name: "다른 회의 열기" }).click();
    await expect(page).toHaveURL(/\/v2\/meetings\/102$/);
    await expect(panel.getByLabel("다른 회의 보는 중")).toBeVisible();
    await expect(panel.getByRole("button", { name: "보는 회의 열기" })).toBeVisible();
  });
});

test.describe("회의록 목록 반응형", () => {
  test.describe("375px 카드", () => {
    test.use({ viewport: { width: 375, height: 800 } });

    test("같은 DOM 의 셀 순서가 그대로이고 카드 라벨(data-label)이 보이며 표 role 이 유지된다", async ({ page }) => {
      await start(page);
      await page.goto("/v2/meetings");
      const table = page.getByRole("table", { name: "회의록 목록" });
      const rows = table.locator("tbody tr");
      await expect(rows).toHaveCount(2);
      const cells = rows.nth(0).locator("td");
      await expect(cells).toHaveCount(5);
      await expect(cells.nth(1)).toContainText("주간 생산 현안 회의");
      await expect(cells.nth(3)).toHaveText("3");
      expect(await cells.evaluateAll((els) => els.map((el) => el.getAttribute("data-label")))).toEqual(["회의 일시", "회의명", "상태", "업무", "단계"]);
      expect(await cells.evaluateAll((els) => els.map((el) => el.getAttribute("role")))).toEqual(Array(5).fill("cell"));
      // CSS ::before 로 라벨이 보인다(글자 내용은 그대로)
      expect(await cells.nth(0).evaluate((el) => getComputedStyle(el, "::before").content)).toBe('"회의 일시"');
      expect(await cells.nth(2).evaluate((el) => getComputedStyle(el, "::before").content)).toBe('"상태"');
      expect(await rows.nth(0).evaluate((el) => getComputedStyle(el).display)).toBe("flex"); // 카드처럼
      await expect(page.getByRole("columnheader", { name: "회의명" })).toHaveCount(1); // 머리글은 접근성 트리에 유지(화면에서만 숨김)
      // 긴 회의명은 줄바꿈
      const longCell = rows.nth(1).locator("td").nth(1);
      expect(await longCell.evaluate((el) => getComputedStyle(el).whiteSpace)).toBe("normal");
      expect((await longCell.boundingBox())!.height).toBeGreaterThan(40);
    });

    test("페이지에 가로 스크롤이 없다", async ({ page }) => {
      await start(page);
      await page.goto("/v2/meetings");
      await expect(page.getByRole("table", { name: "회의록 목록" })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    });

    test("단계 탭: 한 줄 가로 스크롤 영역, 높이 44px 이상, 현재 탭은 aria-selected 와 표시 글자로 구분", async ({ page }) => {
      await start(page);
      await page.goto("/v2/meetings");
      const tabs = page.getByRole("tablist", { name: "회의록 단계" });
      await expect(tabs.getByRole("tab")).toHaveText(["진행중", "종료", "보류", "삭제"]);
      expect(await tabs.evaluate((el) => getComputedStyle(el).overflowX)).toBe("auto");
      for (const tab of await tabs.getByRole("tab").all()) expect((await tab.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      expect(await tabs.evaluate((el) => getComputedStyle(el).flexWrap)).toBe("nowrap");
      const current = tabs.getByRole("tab", { name: "진행중" });
      await expect(current).toHaveAttribute("aria-selected", "true");
      expect(await current.evaluate((el) => getComputedStyle(el, "::before").content)).toContain("●");
    });

    test("페이지 이동 버튼은 높이 44px 이상, 범위 글자는 숨기고 '현재 / 전체 쪽' 만", async ({ page }) => {
      await start(page, { list: { items: Array.from({ length: 20 }, (_, i) => meetingRow(100 + i, `회의 ${i}`)), total: 25 } });
      await page.goto("/v2/meetings");
      await expect(page.getByRole("button", { name: "다음" })).toBeVisible();
      for (const name of ["이전", "다음"]) {
        const box = (await page.getByRole("button", { name }).boundingBox())!;
        expect(box.height).toBeGreaterThanOrEqual(44);
        expect(box.width).toBeGreaterThanOrEqual(44);
      }
      await expect(page.getByText("1 / 2 쪽")).toBeVisible();
      await expect(page.getByText("1–20 / 25")).toBeHidden();
    });

    test("빈 목록: 진행중 탭에만 '첫 회의 올리기' 버튼, 헤더 버튼과 이름이 겹치지 않는다", async ({ page }) => {
      await start(page, { list: { items: [], total: 0 } });
      await page.goto("/v2/meetings");
      await expect(page.getByRole("status").filter({ hasText: "등록된 회의록이 없습니다." })).toBeVisible();
      await expect(page.getByRole("link", { name: "첫 회의 올리기" })).toBeVisible();
      await expect(page.getByRole("link", { name: "새 회의 업로드" })).toBeVisible();
      await page.getByRole("tab", { name: "종료" }).click();
      await expect(page.getByRole("status").filter({ hasText: "종료된 회의록이 없습니다." })).toBeVisible();
      await expect(page.getByRole("link", { name: "첫 회의 올리기" })).toHaveCount(0);
    });
  });

  test.describe("1280px 표", () => {
    test.use({ viewport: { width: 1280, height: 800 } });

    test("표 모양 그대로: 행 높이 48, 머리글 보임, 라벨 ::before 없음, 가로 스크롤 없음", async ({ page }) => {
      await start(page);
      await page.goto("/v2/meetings");
      const table = page.getByRole("table", { name: "회의록 목록" });
      expect(await table.evaluate((el) => getComputedStyle(el).display)).toBe("table");
      await expect(table.getByRole("columnheader", { name: "회의명" })).toBeVisible();
      expect((await table.locator("tbody tr").nth(0).boundingBox())!.height).toBeGreaterThanOrEqual(48);
      expect(await table.locator("tbody td").nth(0).evaluate((el) => getComputedStyle(el, "::before").content)).toMatch(/none|normal/);
      await expect(page.getByRole("link", { name: "첫 회의 올리기" })).toHaveCount(0);
    });
  });
});

/* 작업 65: 패널을 화면 오른쪽으로 이동 */
const boxOf = async (locator: ReturnType<Page["locator"]>) => (await locator.boundingBox())!;

for (const width of [1280, 768]) {
  test.describe(`패널 오른쪽 배치 ${width}px`, () => {
    test.use({ viewport: { width, height: 800 } });

    test("패널이 뷰포트 오른쪽 끝에 붙고 본문은 패널보다 왼쪽, 경계선은 패널 왼쪽 면, 메뉴 링크는 한 번만", async ({ page }) => {
      await start(page);
      await page.goto("/v2");
      const panel = await boxOf(aside(page));
      const main = await boxOf(page.locator("main"));
      expect(Math.round(panel.x + panel.width)).toBe(width);
      expect(panel.width).toBe(248);
      expect(main.x + main.width).toBeLessThanOrEqual(panel.x + 0.5);
      expect(await aside(page).evaluate((el) => { const c = getComputedStyle(el); return [c.borderLeftWidth, c.borderRightWidth]; })).toEqual(["1px", "0px"]);
      await expect(mainNav(page).getByRole("link", { name: "회의록" })).toHaveCount(1);
      await expect(page.getByRole("navigation")).toHaveCount(1);
      // 헤더의 사용자·로그아웃은 패널과 겹치지 않는다
      const logout = await boxOf(page.locator("header").getByRole("button", { name: "로그아웃" }));
      expect(logout.x + logout.width).toBeLessThanOrEqual(panel.x);
    });

    test("Tab 순서가 화면 순서(헤더 → 본문 → 패널)와 같다", async ({ page }) => {
      await start(page);
      await page.goto("/v2");
      await page.locator("body").click({ position: { x: 5, y: 5 } });
      const seen: string[] = [];
      for (let i = 0; i < 30; i += 1) {
        await page.keyboard.press("Tab");
        const where = await page.evaluate(() => {
          const el = document.activeElement as HTMLElement;
          return el.closest("header") ? "header" : el.closest("main") ? "main" : el.closest("aside") ? "aside" : "other";
        });
        if (seen[seen.length - 1] !== where) seen.push(where);
        if (where === "aside" && seen.length >= 3) break;
      }
      expect(seen.filter((v) => v !== "other")).toEqual(["header", "main", "aside"]);
    });
  });
}

for (const width of [767, 375]) {
  test.describe(`패널 오른쪽 드로어 ${width}px`, () => {
    test.use({ viewport: { width, height: 800 } });

    test("햄버거가 헤더 오른쪽 끝, 드로어는 오른쪽 가장자리에 붙어 열린다", async ({ page }) => {
      await start(page, { processing: [proc(1)] });
      await page.goto("/v2");
      const burger = await boxOf(hamburger(page));
      const upload = await boxOf(page.getByRole("link", { name: "새 회의 업로드" }));
      const badge = await boxOf(page.getByRole("button", { name: /처리 현황 요약/ }));
      expect(burger.x + burger.width).toBeGreaterThan(width - 20);
      expect(badge.x + badge.width).toBeLessThanOrEqual(upload.x + 0.5); // 뱃지 → 업로드 → 햄버거 순
      expect(upload.x + upload.width).toBeLessThanOrEqual(burger.x + 0.5);
      expect(Math.abs(badge.y - burger.y)).toBeLessThan(8); // 한 줄
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await hamburger(page).click();
      const drawer = await boxOf(aside(page));
      expect(Math.round(drawer.x + drawer.width)).toBe(width);
      expect(await aside(page).evaluate((el) => getComputedStyle(el).borderLeftWidth)).toBe("1px");
    });

    test("뱃지를 누르면 드로어가 오른쪽에서 열리고 Esc 로 닫히며 햄버거로 포커스가 돌아온다", async ({ page }) => {
      await start(page, { processing: [proc(1)] });
      await page.goto("/v2");
      await page.getByRole("button", { name: /처리 현황 요약/ }).click();
      const drawer = await boxOf(aside(page));
      expect(Math.round(drawer.x + drawer.width)).toBe(width);
      await page.keyboard.press("Escape");
      await expect(aside(page)).toBeHidden();
      await expect(hamburger(page)).toBeFocused();
    });
  });
}
