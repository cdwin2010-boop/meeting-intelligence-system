/*
 * v2 로그인·인증 보호 E2E. 실제 백엔드 없이 page.route 로 v2 API(/api/auth/*) 요청을 가로채 가짜로 응답한다.
 * 가짜 토큰은 고정 문자열이고 실제 비밀번호·토큰은 쓰지 않는다.
 */
import { expect, test, type Page, type Route } from "@playwright/test";

const FAKE_TOKEN = "e2e-fake-token";
const GOOD = { loginId: "kim", password: "correct-password" };
const ACCOUNT = { id: 7, name: "김담당", rank: "manager", tenantId: 1 };
const TOKEN_KEY = "mi.v2.accessToken";

// v2 API 주소(기본 8001)가 환경에 따라 달라도 경로로 가로챈다(프론트 dev 서버에는 /api/auth 가 없다)
const LOGIN_URL = "**/api/auth/login";
const ME_URL = "**/api/auth/me";

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

/** 로그인: 정해 둔 아이디·비밀번호만 성공. me: 가짜 토큰이 붙어 있을 때만 성공 */
async function mockAuthApi(page: Page, options: { meStatus?: number } = {}) {
  await page.route(LOGIN_URL, async (route) => {
    const body = route.request().postDataJSON() as { loginId?: string; password?: string };
    if (body.loginId === GOOD.loginId && body.password === GOOD.password) {
      return json(route, 200, { accessToken: FAKE_TOKEN, tokenType: "bearer" });
    }
    return json(route, 401, { detail: "ID 또는 비밀번호가 올바르지 않습니다" });
  });
  await page.route(ME_URL, async (route) => {
    const authorized = route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}`;
    if (options.meStatus && options.meStatus !== 200) return json(route, options.meStatus, { detail: "인증이 필요합니다" });
    return authorized ? json(route, 200, ACCOUNT) : json(route, 401, { detail: "인증이 필요합니다" });
  });
}

const storedToken = (page: Page) => page.evaluate((key) => window.sessionStorage.getItem(key), TOKEN_KEY);

async function fillAndSubmit(page: Page, loginId: string, password: string) {
  await page.getByLabel("아이디").fill(loginId);
  await page.getByLabel("비밀번호").fill(password);
  await page.getByLabel("비밀번호").press("Enter"); // Enter 로 제출
}

test.describe("v2 로그인", () => {
  test("로그인 성공 → /v2 에 이름 표시", async ({ page }) => {
    await mockAuthApi(page);
    await page.goto("/v2/login");
    await expect(page.getByLabel("아이디")).toBeFocused(); // 첫 입력란 자동 포커스
    await fillAndSubmit(page, GOOD.loginId, GOOD.password);
    await expect(page).toHaveURL(/\/v2$/);
    await expect(page.getByRole("heading", { name: "로그인됨" })).toBeVisible();
    await expect(page.getByText("김담당")).toBeVisible();
    await expect(page.getByText("중간관리자")).toBeVisible();
    expect(await storedToken(page)).toBe(FAKE_TOKEN);
  });

  test("잘못된 비밀번호 → 오류 문구, 이동 없음", async ({ page }) => {
    await mockAuthApi(page);
    await page.goto("/v2/login");
    await fillAndSubmit(page, GOOD.loginId, "wrong-password");
    await expect(page.locator("form").getByRole("alert")).toHaveText("아이디 또는 비밀번호가 올바르지 않습니다");
    await expect(page).toHaveURL(/\/v2\/login$/);
    expect(await storedToken(page)).toBeNull();
    await expect(page.getByRole("button", { name: "로그인" })).toBeEnabled();
  });

  test("토큰 없이 /v2 → /v2/login?next=%2Fv2", async ({ page }) => {
    await mockAuthApi(page);
    await page.goto("/v2");
    await expect(page).toHaveURL(/\/v2\/login\?next=%2Fv2$/);
  });

  test("/v2 에서 me 가 401 → 토큰 삭제 후 로그인 화면", async ({ page }) => {
    await mockAuthApi(page, { meStatus: 401 });
    await page.goto("/v2/login");
    await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
    await page.goto("/v2");
    await expect(page).toHaveURL(/\/v2\/login\?next=%2Fv2$/);
    expect(await storedToken(page)).toBeNull();
  });

  test("로그아웃 → 토큰 삭제 후 로그인 화면", async ({ page }) => {
    await mockAuthApi(page);
    await page.goto("/v2/login");
    await fillAndSubmit(page, GOOD.loginId, GOOD.password);
    await expect(page.getByRole("heading", { name: "로그인됨" })).toBeVisible();
    await page.getByRole("button", { name: "로그아웃" }).click();
    await expect(page).toHaveURL(/\/v2\/login(\?.*)?$/);
    await expect(page.getByLabel("아이디")).toBeVisible();
    expect(await storedToken(page)).toBeNull();
  });

  for (const next of ["https://evil.example", "//evil.example", "/v2evil", "javascript:alert(1)"]) {
    test(`next=${next} 는 외부·다른 경로로 보내지 않고 /v2 로`, async ({ page }) => {
      await mockAuthApi(page);
      await page.goto(`/v2/login?next=${encodeURIComponent(next)}`);
      await fillAndSubmit(page, GOOD.loginId, GOOD.password);
      await expect(page).toHaveURL(/^http:\/\/localhost:\d+\/v2$/);
      await expect(page.getByRole("heading", { name: "로그인됨" })).toBeVisible();
    });
  }

  test("next 가 /v2 아래 경로면 그곳으로 이동", async ({ page }) => {
    await mockAuthApi(page);
    await page.goto(`/v2/login?next=${encodeURIComponent("/v2?tab=todo")}`);
    await fillAndSubmit(page, GOOD.loginId, GOOD.password);
    await expect(page).toHaveURL(/\/v2\?tab=todo$/);
  });

  test("이미 로그인 상태로 로그인 화면에 오면 /v2 로 이동", async ({ page }) => {
    await mockAuthApi(page);
    await page.goto("/v2/login");
    await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
    await page.goto("/v2/login");
    await expect(page).toHaveURL(/\/v2$/);
  });

  test("네트워크 실패 → 연결 오류 문구", async ({ page }) => {
    await page.route(LOGIN_URL, (route) => route.abort("connectionrefused"));
    await page.goto("/v2/login");
    await fillAndSubmit(page, GOOD.loginId, GOOD.password);
    await expect(page.locator("form").getByRole("alert")).toHaveText("서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요");
    await expect(page).toHaveURL(/\/v2\/login$/);
  });

  test("제출 중에는 버튼이 비활성이고 요청은 한 번만 간다", async ({ page }) => {
    let calls = 0;
    await page.route(LOGIN_URL, async (route) => {
      calls += 1;
      await new Promise((resolve) => setTimeout(resolve, 500));
      return json(route, 401, { detail: "ID 또는 비밀번호가 올바르지 않습니다" });
    });
    await page.goto("/v2/login");
    await page.getByLabel("아이디").fill(GOOD.loginId);
    await page.getByLabel("비밀번호").fill("x");
    await page.getByLabel("비밀번호").press("Enter");
    await expect(page.getByRole("button", { name: "로그인 중…" })).toBeDisabled();
    await page.keyboard.press("Enter");
    await expect(page.locator("form").getByRole("alert")).toBeVisible();
    expect(calls).toBe(1);
  });
});
