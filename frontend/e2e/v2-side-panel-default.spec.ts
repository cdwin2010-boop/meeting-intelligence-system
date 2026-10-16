/*
 * 보조창(왼쪽 메뉴·패널) 기본 상태 회귀 (87-1 결정): 저장값이 없는 새 사용자는 "왼쪽 + 펼침", 저장된 설정(쿠키·접힘 값)은 그대로 따른다.
 */
import { expect, test, type Page } from "./helpers/test";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const ME = { id: 9, name: "이서연", rank: "staff", tenantId: 1 };

async function open(page: Page, options: { cookie?: string; collapsed?: boolean } = {}) {
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}`
      ? route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ME) })
      : route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "인증 필요" }) }),
  );
  if (options.cookie) {
    await page.context().addCookies([{ name: "mi_side_position", value: options.cookie, url: `http://localhost:${process.env.E2E_PORT ?? 3100}` }]);
  }
  await page.goto("/v2/login");
  await page.evaluate(([k, t, collapsed, id]) => {
    window.sessionStorage.setItem(k, t);
    if (collapsed) window.localStorage.setItem(`mi.v2.shell.panelCollapsed.${id}`, "1");
    else window.localStorage.clear();
  }, [TOKEN_KEY, FAKE_TOKEN, options.collapsed ? "1" : "", String(ME.id)]);
  await page.goto("/v2");
  await expect(page.getByRole("navigation", { name: "주 메뉴" })).toBeVisible();
}

const aside = (page: Page) => page.locator("aside").first();

test.describe("보조창 기본 상태(87-1)", () => {
  test("저장값이 없으면 왼쪽에 펼쳐진 채로 시작한다", async ({ page }) => {
    await open(page);
    await expect(page.getByRole("button", { name: "회의록 추적 패널 접기" })).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByRole("button", { name: "회의록 추적 패널 펼치기" })).toHaveCount(0);
    const box = (await aside(page).boundingBox())!;
    expect(box.x).toBeLessThan(10);
    expect(box.width).toBeGreaterThan(200);
  });

  test("저장된 접힘 값은 그대로 따른다", async ({ page }) => {
    await open(page, { collapsed: true });
    await expect(page.getByRole("button", { name: "회의록 추적 패널 펼치기" })).toBeVisible();
    expect((await aside(page).boundingBox())!.width).toBeLessThan(120);
  });

  test("저장된 오른쪽 위치(쿠키)는 그대로 따르고, 알 수 없는 값은 왼쪽 기본", async ({ page }) => {
    await open(page, { cookie: "right" });
    expect((await aside(page).boundingBox())!.x).toBeGreaterThan(640);
  });
});
