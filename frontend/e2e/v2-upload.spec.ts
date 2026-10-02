/*
 * v2 회의록 올리기 E2E. 실제 백엔드 없이 page.route 로 v2 API(/api/auth/me, /api/meetings/*)를 가로채 가짜로 응답한다.
 * 올리는 파일은 메모리에서 만든 몇 바이트짜리 가짜 파일이고, 가짜 토큰은 고정 문자열이다.
 */
import { expect, test, type Page, type Route } from "@playwright/test";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const ACCOUNT = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const AUDIO = { name: "weekly_1001.m4a", mimeType: "audio/mp4", buffer: Buffer.from("fake-audio") };
const TEXT = { name: "memo.txt", mimeType: "text/plain", buffer: Buffer.from("not audio") };

const DETAIL = {
  id: 77,
  title: "설비 점검 주간 회의",
  heldAt: "2026-10-01T04:20:00Z",
  summary: "",
  decisions: [],
  status: "processing",
  confirmKind: null,
  confirmedBy: null,
  confirmedAt: null,
  firstCreatedAt: "2026-10-01T05:00:00Z",
  autoConfirmAt: null,
  registeredBy: { id: 7, name: "한팀장" },
  origin: "audio_minutes",
  participants: [],
  actionItems: [],
  recentEvents: [],
};

/** me 는 가짜 토큰일 때만 성공. 업로드는 handler 로 테스트별 응답, 상세는 고정 응답 */
async function openUpload(page: Page, upload: (route: Route) => unknown) {
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}`
      ? json(route, 200, ACCOUNT)
      : json(route, 401, { detail: "인증이 필요합니다" }),
  );
  await page.route("**/api/meetings/upload", (route) => upload(route) as Promise<void>);
  await page.route(/\/api\/meetings\/\d+$/, (route) => json(route, 200, DETAIL));
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto("/v2/upload");
  await expect(page.getByRole("heading", { level: 1, name: "회의록 올리기" })).toBeVisible();
}

async function fillForm(page: Page, file: typeof AUDIO) {
  await page.getByLabel("음성 파일 선택").setInputFiles(file);
  await page.getByLabel("회의명").fill("설비 점검 주간 회의");
  await page.getByLabel("회의 날짜").fill("2026-10-01");
  await page.getByLabel("회의 시각").fill("13:20");
}

const submitButton = (page: Page) => page.getByRole("button", { name: "올리고 분석 시작" });

test.describe("v2 회의록 올리기", () => {
  test("올리기 성공 → /v2/meetings/{id} 로 이동, 요청에 파일·회의명·오프셋 붙은 일시", async ({ page }) => {
    let body = "";
    let method = "";
    await openUpload(page, async (route) => {
      method = route.request().method();
      body = route.request().postDataBuffer()?.toString("utf-8") ?? "";
      await new Promise((resolve) => setTimeout(resolve, 300)); // 진행 표시를 확인할 시간
      return json(route, 202, { meetingId: 77, jobId: 5 });
    });
    await expect(page.getByRole("link", { name: "회의록 올리기" })).toHaveAttribute("aria-current", "page");
    await fillForm(page, AUDIO);
    await expect(page.getByText("weekly_1001.m4a")).toBeVisible();
    await submitButton(page).click();

    await expect(page.getByRole("button", { name: "올리는 중…" })).toBeDisabled();
    await expect(page).toHaveURL(/\/v2\/meetings\/77$/);
    await expect(page.getByRole("heading", { level: 1, name: "설비 점검 주간 회의" })).toBeVisible();

    expect(method).toBe("POST");
    expect(body).toContain('name="file"; filename="weekly_1001.m4a"');
    expect(body).toContain("설비 점검 주간 회의");
    expect(body).toContain("2026-10-01T13:20:00+09:00"); // 설정 timezoneId: Asia/Seoul
  });

  test("파일 없이 제출 → 안내 문구, 요청 없음", async ({ page }) => {
    let calls = 0;
    await openUpload(page, (route) => {
      calls += 1;
      return json(route, 202, { meetingId: 77, jobId: 5 });
    });
    await submitButton(page).click();
    await expect(page.locator("form").getByRole("alert")).toHaveText("음성 파일을 선택하세요.");
    expect(calls).toBe(0);
  });

  test("형식 오류(415) → 서버 문구 표시, 이동 없음", async ({ page }) => {
    await openUpload(page, (route) =>
      json(route, 415, { detail: "허용하지 않는 파일 형식입니다(m4a, mp3, mp4, ogg, wav, webm)." }),
    );
    await fillForm(page, TEXT);
    await submitButton(page).click();
    const alert = page.getByRole("alert").filter({ hasText: "올리지 못했습니다" });
    await expect(alert).toContainText("허용하지 않는 파일 형식입니다");
    await expect(page).toHaveURL(/\/v2\/upload$/);
    await expect(submitButton(page)).toBeEnabled();
  });

  test("용량 초과(413) → 서버 문구 표시", async ({ page }) => {
    await openUpload(page, (route) => json(route, 413, { detail: "파일이 너무 큽니다(최대 200MB)." }));
    await fillForm(page, AUDIO);
    await submitButton(page).click();
    await expect(page.getByRole("alert").filter({ hasText: "올리지 못했습니다" })).toContainText("파일이 너무 큽니다");
  });

  test("서버 오류(500) → 다시 시도로 성공 후 이동", async ({ page }) => {
    let calls = 0;
    await openUpload(page, (route) => {
      calls += 1;
      return calls === 1 ? json(route, 500, { detail: "서버 오류" }) : json(route, 202, { meetingId: 77, jobId: 6 });
    });
    await fillForm(page, AUDIO);
    await submitButton(page).click();
    const alert = page.getByRole("alert").filter({ hasText: "올리지 못했습니다" });
    await expect(alert).toContainText("서버 오류");
    await alert.getByRole("button", { name: "다시 시도" }).click();
    await expect(page).toHaveURL(/\/v2\/meetings\/77$/);
    expect(calls).toBe(2);
  });
});
