/*
 * 작업 85-2 E2E: 올리기 화면 자료 종류 자동 선택(확장자)과 종류 불일치 안내. 백엔드 없이 page.route 로 응답한다(데이터는 모두 가상).
 */
import { expect, test, type Page, type Route } from "./helpers/test";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const ME = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const json = (route: Route, status: number, body: unknown) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const XLSX = { name: "회의_자료.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("PK-fake") };
const TXT = { name: "전사문.txt", mimeType: "text/plain", buffer: Buffer.from("[00:03] 화자1: 안녕하세요", "utf-8") };
const WAV = { name: "회의.wav", mimeType: "audio/wav", buffer: Buffer.from("RIFF-fake") };

async function openUpload(page: Page, uploads: string[]) {
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

const kindGroup = (page: Page) => page.getByRole("group", { name: "자료 종류" });
const checked = (page: Page, label: string) => kindGroup(page).getByRole("radio", { name: label, exact: true });
const choose = (page: Page, label: string) => kindGroup(page).locator("label").filter({ hasText: label }).click();
const typeLabel = (page: Page, label: string) =>
  page.getByRole("group", { name: "회의 유형" }).locator("label").filter({ has: page.getByRole("radio", { name: label, exact: true }) });

test.describe("자료 종류 자동 선택", () => {
  test("엑셀·txt·음성 파일을 고르면 확장자에 맞게 종류가 바뀌고 안내는 없다", async ({ page }) => {
    await openUpload(page, []);
    const input = page.locator('input[type="file"]');
    await input.setInputFiles(XLSX);
    await expect(checked(page, "자료 파일(엑셀)")).toBeChecked();
    await expect(page.getByText("종류를 엑셀로 바꿔 주세요")).toHaveCount(0);
    await input.setInputFiles(TXT);
    await expect(checked(page, "자료 파일(txt)")).toBeChecked();
    await input.setInputFiles(WAV);
    await expect(checked(page, "음성 파일")).toBeChecked();
  });

  test("자동 선택된 종류로 제출하면 sourceKind 가 엑셀이다", async ({ page }) => {
    const uploads: string[] = [];
    await openUpload(page, uploads);
    await page.getByLabel("회의명").fill("엑셀 회의");
    await typeLabel(page, "정기회의").click();
    await page.locator('input[type="file"]').setInputFiles(XLSX);
    await page.getByRole("button", { name: "올리고 분석 시작" }).click();
    await expect.poll(() => uploads.length).toBe(1);
    expect(uploads[0]).toContain("transcript_xlsx");
  });

  test("직접 바꾼 선택은 파일을 골라도 유지되고 어긋남 안내가 보인다", async ({ page }) => {
    const uploads: string[] = [];
    await openUpload(page, uploads);
    await choose(page, "자료 파일(엑셀)");
    await page.locator('input[type="file"]').setInputFiles(TXT);
    await expect(checked(page, "자료 파일(엑셀)")).toBeChecked();
    await expect(page.getByText("텍스트 파일입니다. 자료 종류를 텍스트로 바꿔 주세요.")).toBeVisible();
    await page.getByLabel("회의명").fill("불일치");
    await typeLabel(page, "정기회의").click();
    await page.getByRole("button", { name: "올리고 분석 시작" }).click();
    await expect(page.getByText("자료 파일은 .xlsx 파일만 올릴 수 있습니다.")).toBeVisible();
    expect(uploads).toHaveLength(0);
  });

  test("음성 종류를 직접 고른 상태에서 엑셀 파일이면 안내하고 서버를 부르지 않는다", async ({ page }) => {
    const uploads: string[] = [];
    await openUpload(page, uploads);
    await choose(page, "자료 파일(txt)");
    await choose(page, "음성 파일");
    await page.locator('input[type="file"]').setInputFiles(XLSX);
    await expect(checked(page, "음성 파일")).toBeChecked();
    await expect(page.getByText("엑셀 파일입니다. 자료 종류를 엑셀로 바꿔 주세요.")).toBeVisible();
    await page.getByLabel("회의명").fill("안내");
    await typeLabel(page, "정기회의").click();
    await page.getByRole("button", { name: "올리고 분석 시작" }).click();
    await expect(page.getByText("엑셀 파일입니다. 자료 종류를 엑셀로 바꿔 주세요.").first()).toBeVisible();
    expect(uploads).toHaveLength(0);
    // 종류를 바꾸면 안내가 사라진다
    await choose(page, "자료 파일(엑셀)");
    await page.locator('input[type="file"]').setInputFiles(XLSX);
    await expect(page.getByText("자료 종류를 엑셀로 바꿔 주세요")).toHaveCount(0);
  });

  test("375/768/1280px 에서 안내가 있어도 가로 넘침이 없다", async ({ page }) => {
    await openUpload(page, []);
    await choose(page, "자료 파일(txt)");
    await choose(page, "음성 파일");
    await page.locator('input[type="file"]').setInputFiles({ ...XLSX, name: "아주_긴_파일_이름_".repeat(8) + ".xlsx" });
    await expect(page.getByText("엑셀 파일입니다. 자료 종류를 엑셀로 바꿔 주세요.")).toBeVisible();
    for (const width of [1280, 768, 375]) {
      await page.setViewportSize({ width, height: 800 });
      // 너비를 바꾼 직후 한 프레임은 모바일 전환(matchMedia 이벤트)이 반영되기 전이라 안정될 때까지 기다린다
      await expect
        .poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), { message: `${width}px` })
        .toBeLessThanOrEqual(0);
    }
  });
});
