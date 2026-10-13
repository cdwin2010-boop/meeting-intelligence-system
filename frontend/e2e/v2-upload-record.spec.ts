/*
 * 작업 86-1a E2E: 모바일 올리기 "녹음하기"(capture=microphone) 입력. 음성 종류이고 768px 미만(또는 터치)에서만 보인다.
 * 백엔드 없이 page.route 로 응답한다(데이터는 모두 가상).
 */
import { expect, test, type Page, type Route } from "./helpers/test";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const ME = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const json = (route: Route, status: number, body: unknown) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
const REC = { name: "녹음.m4a", mimeType: "audio/mp4", buffer: Buffer.from("fake-audio") };

async function openUpload(page: Page, width: number, uploads: string[] = []) {
  await page.setViewportSize({ width, height: 800 });
  await page.route("**/api/auth/me", (route) => (route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, ME) : json(route, 401, { detail: "x" })));
  await page.route("**/api/accounts", (route) => json(route, 200, [{ id: 7, name: "한팀장", rank: "manager" }]));
  await page.route("**/api/meetings/upload", (route) => {
    uploads.push(route.request().postDataBuffer()?.toString("utf-8") ?? "");
    return json(route, 202, { meetingId: 77, jobId: 5 });
  });
  await page.goto("/v2/login");
  await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto("/v2/upload");
  await expect(page.getByRole("heading", { level: 1, name: "회의록 올리기" })).toBeVisible();
}

const overflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

for (const width of [375, 412]) {
  test(`${width}px: 녹음하기와 파일 선택 버튼이 보이고 가로 넘침이 없다`, async ({ page }) => {
    await openUpload(page, width);
    await expect(page.locator("button", { hasText: "녹음하기" })).toBeVisible();
    await expect(page.locator("button", { hasText: "음성 파일 선택" })).toBeVisible();
    const record = page.locator('input[type="file"][capture]');
    await expect(record).toHaveCount(1);
    await expect(record).toHaveAttribute("capture", "microphone");
    await expect(record).toHaveAttribute("accept", "audio/*");
    // 일반 파일 선택 입력에는 capture 가 없다
    await expect(page.locator('input[type="file"]:not([capture])')).toHaveCount(1);
    await expect.poll(() => overflow(page)).toBeLessThanOrEqual(0);
  });
}

test("녹음하기로 고른 파일이 같은 선택 상태에 들어가 그대로 제출된다", async ({ page }) => {
  const uploads: string[] = [];
  // 작업 88-2: 모바일 올리기는 Wi-Fi 여부를 판정한다. 이 시험은 녹음 파일 제출 흐름이 목적이라 Wi-Fi 로 주입해 안내 팝업을 건너뛴다
  await page.addInitScript(() => Object.defineProperty(navigator, "connection", { configurable: true, get: () => ({ type: "wifi" }) }));
  await openUpload(page, 375, uploads);
  await page.locator('input[type="file"][capture]').setInputFiles(REC);
  await expect(page.getByText("녹음.m4a")).toBeVisible();
  await page.getByLabel("회의명").fill("녹음 회의");
  await page.getByRole("group", { name: "회의 유형" }).locator("label").filter({ has: page.getByRole("radio", { name: "정기회의", exact: true }) }).click();
  await page.getByRole("button", { name: "올리고 분석 시작" }).click();
  await expect.poll(() => uploads.length).toBe(1);
  expect(uploads[0]).toContain("녹음.m4a");
});

test("자료 종류가 음성이 아니면 모바일에서도 녹음하기가 없다", async ({ page }) => {
  await openUpload(page, 375);
  await page.getByRole("group", { name: "자료 종류" }).locator("label").filter({ hasText: "자료 파일(txt)" }).click();
  await expect(page.locator("button", { hasText: "녹음하기" })).toHaveCount(0);
  await expect(page.locator('input[type="file"][capture]')).toHaveCount(0);
});

test("768px 이상(PC)에서는 녹음하기가 없고 화면이 기존과 같다", async ({ page }) => {
  await openUpload(page, 768);
  await expect(page.locator("button", { hasText: "음성 파일 선택" })).toBeVisible();
  await expect(page.locator("button", { hasText: "녹음하기" })).toHaveCount(0);
  await expect(page.locator('input[type="file"][capture]')).toHaveCount(0);
  await expect.poll(() => overflow(page)).toBeLessThanOrEqual(0);
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(page.locator("button", { hasText: "녹음하기" })).toHaveCount(0);
});
