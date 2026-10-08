/*
 * v2 회의록 올리기 E2E. 실제 백엔드 없이 page.route 로 v2 API(/api/auth/me, /api/meetings/*)를 가로채 가짜로 응답한다.
 * 올리는 파일은 메모리에서 만든 몇 바이트짜리 가짜 파일이고, 가짜 토큰은 고정 문자열이다.
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const ACCOUNT = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const AUDIO = { name: "weekly_1001.m4a", mimeType: "audio/mp4", buffer: Buffer.from("fake-audio") };
const TEXT = { name: "memo.txt", mimeType: "text/plain", buffer: Buffer.from("not audio") };

// 참석자 선택 목록(같은 고객사 계정)
const ACCOUNTS = [
  { id: 2, name: "권부장", rank: "executive" },
  { id: 7, name: "한팀장", rank: "manager" },
  { id: 9, name: "김대리", rank: "staff" },
];

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

/** me 는 가짜 토큰일 때만 성공. 업로드는 handler 로 테스트별 응답, 참석자 목록·상세는 고정 응답(테스트에서 다시 가로채 바꿀 수 있음) */
async function openUpload(page: Page, upload: (route: Route) => unknown) {
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}`
      ? json(route, 200, ACCOUNT)
      : json(route, 401, { detail: "인증이 필요합니다" }),
  );
  await page.route("**/api/accounts", (route) => json(route, 200, ACCOUNTS));
  await page.route("**/api/meetings/upload", (route) => upload(route) as Promise<void>);
  await page.route(/\/api\/meetings\/\d+$/, (route) => json(route, 200, withAllowed(DETAIL, ACCOUNT)));
  // 이동한 상세 화면의 화자 요약용
  await page.route(/\/api\/meetings\/\d+\/speakers$/, (route) => json(route, 200, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route(/\/api\/meetings\/\d+\/change-requests$/, (route) => json(route, 200, []));
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
  await page.getByRole("radio", { name: "정기회의", exact: true }).check({ force: true }); // 회의 유형은 필수(69-2)
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
    expect(body).not.toContain('name="participantIds"'); // 참석자를 고르지 않으면 보내지 않는다
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

  test("파일 선택 표시: 고르기 전 '선택한 파일 없음', 고른 뒤 이름·크기와 '파일 다시 선택'", async ({ page }) => {
    await openUpload(page, (route) => json(route, 202, { meetingId: 77, jobId: 5 }));
    const form = page.locator("form");
    await expect(form.locator("button", { hasText: "음성 파일 선택" })).toBeVisible(); // 숨긴 입력(file 입력도 button 역할)과 구분해 버튼 요소만
    await expect(form.getByText("선택한 파일 없음")).toBeVisible();
    // 숨긴 입력은 접근성 이름 "음성 파일 선택"으로 찾을 수 있고, 거기에 파일을 넣어도 동작한다
    await expect(page.getByLabel("음성 파일 선택")).toHaveClass(/sr-only/);
    await page.getByLabel("음성 파일 선택").setInputFiles(AUDIO);
    await expect(form.getByText("선택한 파일 없음")).toHaveCount(0);
    await expect(form.getByText("weekly_1001.m4a")).toBeVisible();
    await expect(form.getByText("10 B")).toBeVisible();
    await expect(form.locator("button", { hasText: "파일 다시 선택" })).toBeVisible();
    await expect(form.locator("button", { hasText: "음성 파일 선택" })).toHaveCount(0);
  });

  test("끌어다 놓은 뒤에도 같은 표시(파일 이름·크기, 파일 다시 선택)", async ({ page }) => {
    await openUpload(page, (route) => json(route, 202, { meetingId: 77, jobId: 5 }));
    const form = page.locator("form");
    const zone = form.locator("div", { has: page.getByText("음성 파일을 끌어다 놓거나 선택하세요") }).first();
    await zone.evaluate((node) => {
      const transfer = new DataTransfer();
      transfer.items.add(new File([new Uint8Array(2048)], "dropped_0930.m4a", { type: "audio/mp4" }));
      node.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: transfer }));
    });
    await expect(form.getByText("dropped_0930.m4a")).toBeVisible();
    await expect(form.getByText("2.0 KB")).toBeVisible();
    await expect(form.locator("button", { hasText: "파일 다시 선택" })).toBeVisible();
    await expect(form.getByText("선택한 파일 없음")).toHaveCount(0);
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

  test("참석자 목록 표시 → 두 명 선택 후 올리기 → participantIds 로 함께 전송", async ({ page }) => {
    let body = "";
    await openUpload(page, (route) => {
      body = route.request().postDataBuffer()?.toString("utf-8") ?? "";
      return json(route, 202, { meetingId: 77, jobId: 7 });
    });
    const picker = page.getByRole("group", { name: /참석자/ });
    await expect(picker.getByRole("checkbox")).toHaveCount(3);
    await expect(picker).toContainText("권부장 · 지시자");
    await expect(picker).toContainText("김대리 · 담당자");
    await fillForm(page, AUDIO);
    // 체크박스는 칩(라벨) 안에 숨겨져 있으므로 사용자처럼 칩을 눌러 고른다
    const chip = (name: string) => picker.locator("label").filter({ hasText: name });
    await chip("권부장 · 지시자").click();
    await chip("김대리 · 담당자").click();
    await chip("권부장 · 지시자").click(); // 해제도 반영
    await chip("한팀장 · 중간관리자").click();
    await expect(picker.getByLabel("권부장 · 지시자")).not.toBeChecked();
    await expect(picker.getByLabel("김대리 · 담당자")).toBeChecked();
    await expect(picker.getByLabel("한팀장 · 중간관리자")).toBeChecked();
    await submitButton(page).click();
    await expect(page).toHaveURL(/\/v2\/meetings\/77$/);
    const sent = [...body.matchAll(/name="participantIds"\r\n\r\n(\d+)\r\n/g)].map((m) => Number(m[1])).sort((a, b) => a - b);
    expect(sent).toEqual([7, 9]);
  });

  test("참석자 목록 오류 → 오류 표시, 올리기는 그대로 가능", async ({ page }) => {
    let body = "";
    await openUpload(page, (route) => {
      body = route.request().postDataBuffer()?.toString("utf-8") ?? "";
      return json(route, 202, { meetingId: 77, jobId: 8 });
    });
    // openUpload 이후 다시 가로채 오류로 바꾸고, 목록 다시 불러오기 대신 화면을 새로 연다
    await page.route("**/api/accounts", (route) => json(route, 500, { detail: "서버 오류" }));
    await page.reload();
    const alert = page.getByRole("alert").filter({ hasText: "참석자 목록을 불러오지 못했습니다" });
    await expect(alert).toContainText("서버 오류");
    await expect(alert.getByRole("button", { name: "목록 다시 불러오기" })).toBeVisible();
    await fillForm(page, AUDIO);
    await submitButton(page).click();
    await expect(page).toHaveURL(/\/v2\/meetings\/77$/);
    expect(body).toContain('name="file"');
    expect(body).not.toContain('name="participantIds"');
  });
});
