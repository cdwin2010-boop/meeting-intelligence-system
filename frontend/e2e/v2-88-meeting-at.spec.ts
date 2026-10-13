/*
 * 작업 88-4 E2E: 회의 일시 필수 입력·제안값 미리 채움·미래 거부·목록/상세의 회의 일시와 등록 일시 구분·등록 후 변경.
 * 백엔드 없이 page.route 로 응답한다(데이터는 모두 가상).
 */
import { expect, test, type Page } from "./helpers/test";
import { ME_MANAGER, ME_STAFF, json, loginWithMocks, queuedDetail } from "./helpers/queue-mock";

const FILE = { name: "녹음.m4a", mimeType: "audio/mp4", buffer: Buffer.from("fake-audio-bytes") };
const pad = (n: number) => String(n).padStart(2, "0");

async function openUpload(page: Page, uploads: string[] = [], width = 1280) {
  await page.setViewportSize({ width, height: 900 });
  await loginWithMocks(page, { detail: queuedDetail() });
  await page.route("**/api/meetings/upload", (route) => {
    uploads.push(route.request().postDataBuffer()?.toString("utf-8") ?? "");
    return json(route, 202, { meetingId: 41, jobId: 77 });
  });
  await page.goto("/v2/upload");
  await expect(page.getByRole("heading", { level: 1, name: "회의록 올리기" })).toBeVisible();
}

/** lastModified 를 정해 파일을 입력에 넣는다(Playwright 의 setInputFiles 는 시각을 정할 수 없다) */
async function pickWithModified(page: Page, selector: string, lastModified: number, name = FILE.name) {
  await page.evaluate(
    ([sel, modified, fileName]) => {
      const input = document.querySelector(sel as string) as HTMLInputElement;
      const transfer = new DataTransfer();
      transfer.items.add(new File(["fake-audio-bytes"], fileName as string, { type: "audio/mp4", lastModified: modified as number }));
      input.files = transfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    },
    [selector, lastModified, name] as const,
  );
}

const dateInput = (page: Page) => page.getByLabel("회의 날짜");
const timeInput = (page: Page) => page.getByLabel("회의 시각");
const typeRegular = (page: Page) =>
  page.getByRole("group", { name: "회의 유형" }).locator("label").filter({ has: page.getByRole("radio", { name: "정기회의", exact: true }) }).click();
const FILE_INPUT = 'input[type="file"]:not([capture])';

function localParts(ms: number) {
  const d = new Date(ms);
  return { date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`, time: `${pad(d.getHours())}:${pad(d.getMinutes())}` };
}

test("처음에는 회의 일시가 빈칸이고 필수 입력 안내가 보인다", async ({ page }) => {
  await openUpload(page);
  await expect(dateInput(page)).toHaveValue("");
  await expect(timeInput(page)).toHaveValue("");
  await expect(page.getByText("필수 입력입니다. 회의가 열린 날짜와 시각을 입력해 주세요.")).toBeVisible();
});

test("파일을 고르면 파일의 수정 시각이 제안값으로 미리 채워지고 확인 안내가 보인다", async ({ page }) => {
  await openUpload(page);
  const modified = new Date("2026-10-12T08:30:00").getTime();
  await pickWithModified(page, FILE_INPUT, modified);
  const expected = localParts(modified);
  await expect(dateInput(page)).toHaveValue(expected.date);
  await expect(timeInput(page)).toHaveValue(expected.time);
  await expect(page.getByText("파일의 시각을 제안값으로 채웠습니다.")).toBeVisible();
});

test("제안값이 없으면(시각 정보 없음) 빈칸으로 두고, 비운 채 올리면 필수 안내만 나오고 서버를 부르지 않는다", async ({ page }) => {
  const uploads: string[] = [];
  await openUpload(page, uploads);
  await pickWithModified(page, FILE_INPUT, 0);
  await expect(dateInput(page)).toHaveValue("");
  await typeRegular(page);
  await page.getByRole("button", { name: "올리고 분석 시작" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "회의 일시를 입력하세요." })).toBeVisible();
  expect(uploads).toHaveLength(0);
});

test("사용자가 직접 입력한 일시는 다른 파일을 골라도 제안값으로 덮어쓰지 않는다", async ({ page }) => {
  await openUpload(page);
  await dateInput(page).fill("2026-09-01");
  await timeInput(page).fill("09:00");
  await pickWithModified(page, FILE_INPUT, new Date("2026-10-12T08:30:00").getTime());
  await expect(dateInput(page)).toHaveValue("2026-09-01");
  await expect(timeInput(page)).toHaveValue("09:00");
});

test("미래 일시는 서버를 부르지 않고 거부한다", async ({ page }) => {
  const uploads: string[] = [];
  await openUpload(page, uploads);
  await page.locator(FILE_INPUT).setInputFiles(FILE);
  const future = new Date(Date.now() + 3 * 24 * 3600 * 1000);
  await dateInput(page).fill(`${future.getFullYear()}-${pad(future.getMonth() + 1)}-${pad(future.getDate())}`);
  await timeInput(page).fill("10:00");
  await typeRegular(page);
  await page.getByRole("button", { name: "올리고 분석 시작" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "현재 시각보다 미래일 수 없습니다" })).toBeVisible();
  expect(uploads).toHaveLength(0);
});

test("올릴 때 회의 일시가 오프셋 붙은 ISO 로 전송된다", async ({ page }) => {
  const uploads: string[] = [];
  await openUpload(page, uploads);
  await page.locator(FILE_INPUT).setInputFiles(FILE);
  await dateInput(page).fill("2026-10-01");
  await timeInput(page).fill("13:20");
  await typeRegular(page);
  await page.getByRole("button", { name: "올리고 분석 시작" }).click();
  await expect.poll(() => uploads.length).toBe(1);
  expect(uploads[0]).toMatch(/name="heldAt"\r?\n\r?\n2026-10-01T13:20:00[+-]\d{2}:\d{2}/);
});

test("모바일 녹음하기: 녹음을 시작한 시각이 제안값(파일 시각보다 우선)", async ({ page }) => {
  await openUpload(page, [], 375);
  const before = Date.now();
  await page.locator("button", { hasText: "녹음하기" }).click();
  const old = new Date("2026-09-01T07:00:00").getTime();
  await pickWithModified(page, 'input[type="file"][capture]', old);
  const parts = localParts(before);
  await expect(dateInput(page)).toHaveValue(parts.date);
  const minutes = Number((await timeInput(page).inputValue()).slice(3));
  expect(Math.abs(minutes - Number(parts.time.slice(3)))).toBeLessThanOrEqual(2); // 클릭 시각 근처(분 단위 경계 허용)
  await expect(page.getByText("파일의 시각을 제안값으로 채웠습니다.")).toBeVisible();
});

// ---------------- 목록·상세: 회의 일시와 등록 일시 구분 ----------------
test("목록: 회의 일시와 등록 일시가 구분해 보인다(같은 셀의 별도 줄)", async ({ page }) => {
  await loginWithMocks(page);
  await page.route(/\/api\/meetings\?.*/, (route) =>
    json(route, 200, {
      items: [{
        id: 41, title: "주간 생산 현안 회의", heldAt: "2026-10-01T01:00:00Z", registeredAt: "2026-10-05T02:30:00Z",
        registeredBy: { id: 7, name: "한팀장" }, origin: "audio", status: "confirmed", confirmKind: "manager", itemCount: 2,
        needsCompletionCount: 0, autoConfirmAt: null, phase: "active",
      }],
      total: 1, page: 1, size: 20, availablePhases: ["active"],
    }),
  );
  await page.goto("/v2/meetings");
  const table = page.getByRole("table", { name: "회의록 목록" });
  await expect(table.getByRole("columnheader", { name: "회의 일시" })).toBeVisible();
  const first = table.locator("tbody tr").first().locator("td").nth(0);
  await expect(first).toContainText("2026-10-01");
  await expect(first).toContainText("등록 2026-10-05");
});

test("상세: 회의 일시와 등록 일시를 따로 보이고, 값이 없는 옛 회의록은 '회의 일시 미입력'", async ({ page }) => {
  await loginWithMocks(page, { detail: queuedDetail({ heldAt: "2026-10-01T01:00:00Z", registeredAt: "2026-10-05T02:30:00Z", firstCreatedAt: "2026-10-05T02:30:00Z" }, {}, []) });
  await page.goto("/v2/meetings/41");
  const overview = page.getByRole("region", { name: "회의 개요" });
  await expect(overview.getByText("회의 일시", { exact: true })).toBeVisible();
  await expect(overview.getByText("등록 일시", { exact: true })).toBeVisible();
  await expect(overview).toContainText("2026-10-01");
  await expect(overview).toContainText("2026-10-05");

  const old = await page.context().newPage();
  await loginWithMocks(old, { detail: queuedDetail({ heldAt: null, registeredAt: undefined, firstCreatedAt: "2026-10-05T02:30:00Z" }, {}, []) });
  await old.goto("/v2/meetings/41");
  await expect(old.getByRole("region", { name: "회의 개요" })).toContainText("회의 일시 미입력");
});

// ---------------- 등록 후 변경 ----------------
test("회의 일시 변경: 서버가 허용한 사용자에게만 버튼, 사유 필수, 요청 본문과 서버 거부 사유 표시", async ({ page }) => {
  const bodies: Array<Record<string, unknown>> = [];
  await loginWithMocks(page, { detail: queuedDetail({}, {}, ["edit_held_at"]) });
  await page.goto("/v2/meetings/41");
  await page.getByRole("button", { name: "회의 일시 변경" }).first().click();
  const dialog = page.getByRole("dialog", { name: "회의 일시 변경" });
  await expect(dialog).toContainText("이미 계산된 업무 기한과 등록 일시는 바뀌지 않으며");
  await page.route("**/api/meetings/41/held-at", (route) => {
    bodies.push(route.request().postDataJSON());
    if (bodies.length === 1) return json(route, 422, { detail: "회의 일시는 현재 시각보다 미래일 수 없습니다. 날짜와 시각을 다시 확인해 주세요." });
    return json(route, 200, { id: 41, heldAt: "2026-09-30T01:00:00Z", registeredAt: "2026-10-05T02:30:00Z", changed: true });
  });
  await dialog.getByLabel("새 회의 날짜").fill("2026-09-30");
  await dialog.getByLabel("새 회의 시각").fill("10:00");
  await dialog.getByRole("button", { name: "회의 일시 변경" }).click();
  await expect(dialog.getByRole("alert")).toContainText("변경 사유를 입력해 주세요."); // 사유 없이는 서버를 부르지 않는다
  expect(bodies).toHaveLength(0);
  await dialog.getByLabel("변경 사유 (필수)").fill("실제 회의 날짜 정정");
  await dialog.getByRole("button", { name: "회의 일시 변경" }).click();
  await expect(dialog.getByRole("alert")).toContainText("현재 시각보다 미래일 수 없습니다"); // 서버 사유 그대로
  await dialog.getByRole("button", { name: "회의 일시 변경" }).click();
  await expect(dialog).toHaveCount(0);
  expect(bodies).toHaveLength(2);
  expect(bodies[1]).toMatchObject({ reason: "실제 회의 날짜 정정" });
  expect(String(bodies[1].heldAt)).toMatch(/^2026-09-30T10:00:00[+-]\d{2}:\d{2}$/);
});

test("담당자(허용 동작 없음)에게는 회의 일시 변경 버튼이 없다", async ({ page }) => {
  await loginWithMocks(page, { me: ME_STAFF, detail: queuedDetail({}, {}, []) });
  await page.goto("/v2/meetings/41");
  await expect(page.getByRole("region", { name: "회의 개요" })).toBeVisible();
  await expect(page.getByRole("button", { name: "회의 일시 변경" })).toHaveCount(0);
});

test("관리자 화면 375px: 개요·변경 팝업에서 가로 넘침이 없다", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await loginWithMocks(page, { me: ME_MANAGER, detail: queuedDetail({}, {}, ["edit_held_at"]) });
  await page.goto("/v2/meetings/41");
  await page.getByRole("button", { name: "회의 일시 변경" }).first().click();
  await expect(page.getByRole("dialog", { name: "회의 일시 변경" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
});
