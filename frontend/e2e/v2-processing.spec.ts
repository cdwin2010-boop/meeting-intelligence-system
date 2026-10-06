/*
 * v2 처리 상태 안내·재처리·처리 엔진 표시 E2E. 실제 백엔드 없이 page.route 로 가로챈다(데이터는 모두 가상).
 * 자동 새로고침 주기는 playwright 설정이 NEXT_PUBLIC_PROCESSING_REFRESH_SEC=1 로 줄여 둔다.
 */
import { expect, test, type Page, type Route } from "./helpers/test";

import { withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const LEAD = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const STAFF = { id: 9, name: "이서연", rank: "staff", tenantId: 1 };

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const item = (id: number, title: string) => ({
  id, title, assignee: { id: 9, name: "이서연" }, dueDate: "2026-10-09", dueUndetermined: false, status: "pending",
  confirmKind: null, evidenceStartSec: null, evidenceQuote: null, needsCompletion: false, missingFields: [], origin: "ai",
});

type Detail = Record<string, unknown> & { actionItems: ReturnType<typeof item>[] };

const detailOf = (status: string, processing: Record<string, unknown> | null, extra: Record<string, unknown> = {}): Detail => ({
  id: 41, title: "주간 생산 현안 회의", heldAt: "2026-10-01T01:00:00Z", summary: "", decisions: [],
  status, confirmKind: null, confirmedBy: null, confirmedAt: null,
  firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: null, registeredBy: { id: 7, name: "한팀장" },
  origin: "audio_minutes", participants: [], guestParticipants: [], recentEvents: [], phase: "active",
  minutes: { purpose: "내용없음", discussion: "내용없음", decisions: "내용없음", risks: "내용없음", nextAgenda: "내용없음", engine: "gemini", updatedBy: null, updatedAt: null },
  processing, actionItems: [], ...extra,
});

const failedProcessing = (code: string | null) => ({ status: "failed", errorCode: code, startedAt: "2026-10-01T02:00:00Z", finishedAt: "2026-10-01T02:01:00Z" });

interface Opts {
  engine?: { engine: string; isFake: boolean } | null;
  lead?: boolean;
}

async function login(page: Page, account: typeof LEAD, opts: Opts) {
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, account) : json(route, 401, { detail: "인증 필요" }),
  );
  await page.route("**/api/system/engine", (route) =>
    opts.engine ? json(route, 200, opts.engine) : json(route, 404, { detail: "없음" }),
  );
  await page.route("**/api/accounts", (route) => json(route, 200, []));
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
}

/** next 가 상세를 줄 때마다 detailFor(호출 순번)를 쓴다 */
async function openDetail(page: Page, account: typeof LEAD, detailFor: (call: number) => Detail, opts: Opts = {}) {
  const state = { gets: 0, reprocess: [] as number[] };
  await login(page, account, opts);
  await page.route(/\/api\/meetings\/41$/, (route) => {
    state.gets += 1;
    return json(route, 200, withAllowed(detailFor(state.gets), account, { lead: opts.lead }));
  });
  await page.route(/\/api\/meetings\/41\/transcript$/, (route) => json(route, 404, { detail: "전사문이 없습니다" }));
  await page.route(/\/api\/meetings\/41\/speakers$/, (route) => json(route, 200, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route(/\/api\/meetings\/41\/change-requests$/, (route) => json(route, 200, []));
  await page.route(/\/api\/meetings\/41\/audio-url$/, (route) => json(route, 404, { detail: "음성 파일이 없습니다" }));
  await page.goto("/v2/meetings/41");
  await expect(page.getByRole("heading", { level: 1, name: "주간 생산 현안 회의" })).toBeVisible();
  return state;
}

const banner = (page: Page) => page.getByLabel("처리 상태", { exact: true });

test.describe("처리 중 안내와 자동 새로고침", () => {
  test("처리 중 배너 → 자동 새로고침 → 완료되면 업무가 보이고 새로고침이 멈춘다", async ({ page }) => {
    const done = { ...detailOf("confirmed", { status: "completed", errorCode: null, startedAt: null, finishedAt: null }), actionItems: [item(101, "견적서 송부")] };
    const state = await openDetail(page, LEAD, (n) => (n < 3 ? detailOf("processing", { status: "running", errorCode: null, startedAt: null, finishedAt: null }) : done));
    await expect(banner(page)).toContainText("음성을 처리하는 중입니다. 완료되면 업무가 표시됩니다.");
    await expect(page.getByRole("table", { name: "업무 원장" }).locator("tbody tr").filter({ hasText: "견적서 송부" })).toBeVisible({ timeout: 10_000 });
    await expect(banner(page)).toHaveCount(0);
    const settled = state.gets;
    await page.waitForTimeout(3200); // 주기(1초)의 몇 배를 기다려도 더 부르지 않는다
    expect(state.gets).toBe(settled);
  });

  test("처리 중이 아니면 자동 새로고침을 하지 않는다", async ({ page }) => {
    const state = await openDetail(page, LEAD, () => detailOf("confirmed", null));
    await page.waitForTimeout(800);
    const first = state.gets; // 개발 모드(StrictMode)에서는 처음 조회가 두 번일 수 있다
    await page.waitForTimeout(2500);
    expect(state.gets).toBe(first);
  });

  test("처리 중 화면을 떠나면 더 이상 요청하지 않는다", async ({ page }) => {
    const state = await openDetail(page, LEAD, () => detailOf("processing", { status: "running", errorCode: null, startedAt: null, finishedAt: null }));
    await expect.poll(() => state.gets, { timeout: 8000 }).toBeGreaterThanOrEqual(2);
    await page.route("**/api/meetings?*", (route) => json(route, 200, { items: [], total: 0, page: 1, size: 20, availablePhases: ["active", "ended"] }));
    await page.getByRole("link", { name: /회의록 목록|목록/ }).first().click();
    await expect(page).toHaveURL(/\/v2\/meetings$/);
    const left = state.gets;
    await page.waitForTimeout(3000);
    expect(state.gets).toBe(left);
  });
});

test.describe("실패 안내", () => {
  const cases: [string, string][] = [
    ["gemini_api_503", "Gemini 서버가 일시적으로 응답하지 않았습니다. 잠시 뒤 다시 처리해 보세요."],
    ["gemini_key_missing", "Gemini 키 설정을 확인해야 합니다."],
    ["server_restarted", "서버가 재시작되어 처리가 중단되었습니다. 다시 처리해 주세요."],
    ["weird_code_77", "처리 중 오류가 발생했습니다 (weird_code_77)"],
  ];
  for (const [code, text] of cases) {
    test(`오류 코드 ${code} → 한국어 이유와 코드 글자`, async ({ page }) => {
      await openDetail(page, LEAD, () => detailOf("failed", failedProcessing(code)));
      await expect(banner(page)).toContainText("처리에 실패했습니다");
      await expect(banner(page)).toContainText(text);
      await expect(banner(page)).toContainText(code); // 코드 글자도 함께
    });
  }
});

test.describe("재처리", () => {
  test("확인 창(취소 포커스 1순위·안내) → 재처리하기 → 처리 중 전환과 자동 새로고침", async ({ page }) => {
    let reprocessed = false;
    const calls: string[] = [];
    await page.route(/\/api\/meetings\/41\/reprocess$/, (route) => {
      calls.push(route.request().method());
      reprocessed = true;
      return json(route, 202, { meetingId: 41, jobId: 9 });
    });
    const state = await openDetail(page, LEAD, () =>
      reprocessed ? detailOf("processing", { status: "queued", errorCode: null, startedAt: null, finishedAt: null }) : detailOf("failed", failedProcessing("gemini_api_503")),
    );
    const open = page.getByRole("button", { name: "다시 처리" });
    await open.click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("회의록 재처리");
    await expect(dialog).toContainText("음성이 다시 처리되며 Gemini 모드이면 외부로 전송되고 비용이 생길 수 있습니다.");
    await expect(dialog.getByRole("button", { name: "취소" })).toBeFocused();
    await dialog.getByRole("button", { name: "재처리하기" }).click();
    await expect(dialog).toBeHidden();
    expect(calls).toEqual(["POST"]);
    await expect(banner(page)).toContainText("음성을 처리하는 중입니다");
    const afterSwitch = state.gets;
    await expect.poll(() => state.gets, { timeout: 8000 }).toBeGreaterThan(afterSwitch); // 자동 새로고침이 이어진다
  });

  test("취소하면 요청 없음", async ({ page }) => {
    const calls: string[] = [];
    await page.route(/\/api\/meetings\/41\/reprocess$/, (route) => {
      calls.push("x");
      return json(route, 202, { meetingId: 41, jobId: 9 });
    });
    await openDetail(page, LEAD, () => detailOf("failed", failedProcessing("timeout")));
    await page.getByRole("button", { name: "다시 처리" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "취소" }).click();
    await expect(page.getByRole("alertdialog")).toBeHidden();
    await expect(page.getByRole("button", { name: "다시 처리" })).toBeFocused();
    expect(calls).toEqual([]);
  });

  test("서버가 거절하면(409) 서버 문구 그대로, 버튼은 숨기지 않는다", async ({ page }) => {
    await page.route(/\/api\/meetings\/41\/reprocess$/, (route) => json(route, 409, { detail: "보관된 음성 파일이 없어 다시 처리할 수 없습니다. 음성을 다시 올려 주세요" }));
    await openDetail(page, LEAD, () => detailOf("failed", failedProcessing("upload_missing")));
    await page.getByRole("button", { name: "다시 처리" }).click();
    const dialog = page.getByRole("alertdialog");
    await dialog.getByRole("button", { name: "재처리하기" }).click();
    await expect(dialog.getByRole("alert")).toContainText("보관된 음성 파일이 없어 다시 처리할 수 없습니다");
    await dialog.getByRole("button", { name: "취소" }).click();
    await expect(page.getByRole("button", { name: "다시 처리" })).toBeVisible();
  });

  test("권한이 없으면(타 관리자·담당자) 재처리 버튼이 없고 실패 이유는 보인다", async ({ page }) => {
    await openDetail(page, LEAD, () => detailOf("failed", failedProcessing("gemini_api_503")), { lead: false });
    await expect(banner(page)).toContainText("처리에 실패했습니다");
    await expect(page.getByRole("button", { name: "다시 처리" })).toHaveCount(0);
  });

  test("담당자도 재처리 버튼이 없다", async ({ page }) => {
    await openDetail(page, STAFF, () => detailOf("failed", failedProcessing("gemini_api_503")));
    await expect(page.getByRole("button", { name: "다시 처리" })).toHaveCount(0);
  });

  test("허용 동작에 없으면(allowedActions 없는 응답) 버튼이 없다", async ({ page }) => {
    await login(page, LEAD, {});
    await page.route(/\/api\/meetings\/41$/, (route) => json(route, 200, detailOf("failed", failedProcessing("gemini_api_503"))));
    await page.route(/\/api\/meetings\/41\/(transcript|audio-url)$/, (route) => json(route, 404, { detail: "없음" }));
    await page.route(/\/api\/meetings\/41\/speakers$/, (route) => json(route, 200, { labels: [], speakers: [], autoAssignedItemIds: [] }));
    await page.route(/\/api\/meetings\/41\/change-requests$/, (route) => json(route, 200, []));
    await page.goto("/v2/meetings/41");
    await expect(banner(page)).toContainText("처리에 실패했습니다");
    await expect(page.getByRole("button", { name: "다시 처리" })).toHaveCount(0);
  });
});

test.describe("처리 엔진 표시", () => {
  const FAKE = "시험용 가짜 처리 모드입니다. 업로드한 음성은 실제로 처리되지 않습니다.";

  async function openUpload(page: Page, engine: Opts["engine"]) {
    await login(page, LEAD, { engine });
    await page.goto("/v2/upload");
    await expect(page.getByRole("heading", { level: 1, name: "회의록 올리기" })).toBeVisible();
  }

  test("fake 이면 앱 틀 상단과 업로드 화면에 글자 안내", async ({ page }) => {
    await openUpload(page, { engine: "fake", isFake: true });
    await expect(page.getByLabel("처리 엔진 안내", { exact: true })).toContainText(FAKE); // 앱 틀 상단
    await expect(page.getByLabel("업로드 화면 처리 엔진 안내")).toContainText(FAKE);
  });

  test("gemini 이면 안내 없음", async ({ page }) => {
    await openUpload(page, { engine: "gemini", isFake: false });
    await expect(page.getByText(FAKE)).toHaveCount(0);
  });

  test("엔진 조회에 실패하면(알 수 없음) 안내 없음", async ({ page }) => {
    await openUpload(page, null);
    await expect(page.getByText(FAKE)).toHaveCount(0);
  });

  test("상세: 그 회의록의 처리 엔진이 fake 이면 안내, gemini 이면 없음", async ({ page }) => {
    const fakeDetail = detailOf("confirmed", null);
    (fakeDetail.minutes as Record<string, unknown>).engine = "fake";
    await openDetail(page, LEAD, () => fakeDetail, { engine: { engine: "gemini", isFake: false } });
    await expect(page.getByLabel("회의록 처리 엔진 안내")).toContainText("시험용 가짜 처리 모드로 만들어졌습니다");
    await expect(page.getByLabel("처리 엔진 안내", { exact: true })).toHaveCount(0); // 서버 엔진은 gemini 라 앱 틀 안내 없음
  });

  test("상세: gemini 로 만든 회의록에는 안내 없음", async ({ page }) => {
    await openDetail(page, LEAD, () => detailOf("confirmed", null));
    await expect(page.getByLabel("회의록 처리 엔진 안내")).toHaveCount(0);
  });
});
