/*
 * 작업 85-1 E2E: 엑셀 양식 내려받기(올리기 화면), 화자 없는 자료 파일의 전사문 표시, 할 일 화면 개선(승인 대기 링크·돌아가기·보완 필요 이동),
 * 보조창 좌우 전환(쿠키·DOM 순서·접기 버튼·모바일 고정), 375/768/1280px 가로 넘침.
 * 백엔드 없이 page.route 로 응답한다(데이터는 모두 가상).
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const ME = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
const overflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

const EMPTY = { total: 0, items: [] };
const todos = (extra: Record<string, unknown> = {}) => ({
  awaitingConfirmMeetings: EMPTY,
  needsCompletionItems: EMPTY,
  myItems: EMPTY,
  unreadAutoConfirmed: EMPTY,
  pendingChangeRequests: EMPTY,
  ...extra,
});

const NEEDS = { total: 1, items: [{ meetingId: 41, itemId: 102, title: "견적서 보완", missingFields: ["assignee"] }] };
const REQUESTS = {
  total: 1,
  items: [{ requestId: 9, meetingId: 41, meetingTitle: "주간 회의", itemId: 101, itemTitle: "견적서 송부", requester: { id: 3, name: "김대리" }, createdAt: "2026-10-01T00:00:00Z", commentPreview: "수정 부탁", kind: "edit" }],
};

const item = (overrides: Record<string, unknown>) => ({
  id: 1, title: "", assignee: { id: 7, name: "한팀장" }, dueDate: "2026-10-05", dueUndetermined: false, status: "pending", confirmKind: null,
  evidenceStartSec: null, evidenceQuote: null, needsCompletion: false, missingFields: [], origin: "ai", ...overrides,
});
const DETAIL = {
  id: 41, title: "주간 회의", heldAt: "2026-10-01T01:00:00Z", summary: "요약", decisions: [], status: "awaiting_confirmation", confirmKind: null,
  confirmedBy: null, confirmedAt: null, firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: "2026-10-06T01:00:00Z", registeredBy: { id: 3, name: "김대리" },
  origin: "transcript_xlsx", sourceKind: "transcript_xlsx", hasAudio: false, participants: [{ id: 3, name: "김대리" }, { id: 7, name: "한팀장" }], guestParticipants: [],
  actionItems: [item({ id: 101, title: "견적서 송부" }), item({ id: 102, title: "견적서 보완", assignee: null, needsCompletion: true, missingFields: ["assignee"] })],
  recentEvents: [], phase: "active", processing: null, minutes: null,
};

interface Options {
  todosBody?: unknown;
  projects?: unknown[];
  transcript?: unknown;
  cookie?: "left" | "right";
}

async function open(page: Page, path: string, options: Options = {}) {
  await page.route("**/api/auth/me", (route) => (route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, ME) : json(route, { detail: "x" }, 401)));
  await page.route("**/api/me/todos", (route) => json(route, options.todosBody ?? todos()));
  await page.route("**/api/me/notices", (route) => json(route, []));
  await page.route("**/api/me/processing", (route) => json(route, []));
  await page.route("**/api/projects", (route) => json(route, options.projects ?? []));
  await page.route("**/api/meetings?*", (route) => json(route, { items: [], total: 0, page: 1, size: 20, availablePhases: ["active"] }));
  await page.route(/\/api\/meetings\/\d+$/, (route) => json(route, withAllowed(DETAIL as never, ME)));
  await page.route(/\/api\/meetings\/\d+\/transcript$/, (route) =>
    json(route, options.transcript ?? { fullText: "x", segments: null, sttProvider: "자료 파일(STT 없음)", speakers: [] }),
  );
  await page.route(/\/api\/meetings\/\d+\/speakers$/, (route) => json(route, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route(/\/api\/meetings\/\d+\/change-requests$/, (route) => json(route, []));
  await page.route("**/api/accounts", (route) => json(route, [{ id: 7, name: "한팀장", rank: "manager" }]));
  if (options.cookie) {
    await page.context().addCookies([{ name: "mi_side_position", value: options.cookie, url: `http://localhost:${process.env.E2E_PORT ?? 3100}` }]);
  }
  await page.goto("/v2/login");
  await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto(path);
}

// ================= 파트 A: 엑셀 양식 =================
test.describe("엑셀 양식 내려받기", () => {
  test("엑셀 종류에서만 버튼이 보이고 누르면 양식 파일을 받는다", async ({ page }) => {
    let calls = 0;
    await page.route("**/api/templates/transcript-xlsx", (route) => {
      calls += 1;
      return route.fulfill({
        status: 200,
        contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        body: Buffer.from("PK-fake-template"),
      });
    });
    await open(page, "/v2/upload");
    const button = page.getByRole("button", { name: "엑셀 양식 내려받기" });
    await expect(button).toHaveCount(0); // 음성 종류에서는 보이지 않는다
    const group = page.getByRole("group", { name: "자료 종류" });
    await group.locator("label").filter({ hasText: "자료 파일(txt)" }).click();
    await expect(button).toHaveCount(0);
    await group.locator("label").filter({ hasText: "자료 파일(엑셀)" }).click();
    await expect(button).toBeVisible();
    const [download] = await Promise.all([page.waitForEvent("download"), button.click()]);
    expect(download.suggestedFilename()).toBe("회의록_자료_양식.xlsx");
    expect(calls).toBe(1);
  });

  test("양식을 받지 못하면 실패 문구를 보이고 입력은 그대로다", async ({ page }) => {
    await page.route("**/api/templates/transcript-xlsx", (route) => json(route, { detail: "양식을 만들지 못했습니다" }, 500));
    await open(page, "/v2/upload");
    await page.getByLabel("회의명").fill("입력 유지");
    await page.getByRole("group", { name: "자료 종류" }).locator("label").filter({ hasText: "자료 파일(엑셀)" }).click();
    await page.getByRole("button", { name: "엑셀 양식 내려받기" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "양식을 내려받지 못했습니다" })).toBeVisible();
    await expect(page.getByLabel("회의명")).toHaveValue("입력 유지");
  });
});

// ================= 파트 B: 화자 없는 전사문 표시 =================
test.describe("자료 파일 전사문 표시", () => {
  test("화자가 없으면 라벨 없이 발언만, 있으면 적은 그대로(공백·문장부호 유지)", async ({ page }) => {
    const segments = [
      { speaker: "", start_sec: 5, end_sec: 20, text: "화자 열이 없는 발언" },
      { speaker: "김 대리(팀)", start_sec: 20, end_sec: null, text: "적은 그대로 보이는 화자" },
      { speaker: "", start_sec: null, end_sec: null, text: "시각도 없는 발언" },
    ];
    await open(page, "/v2/meetings/41", {
      transcript: { fullText: "[00:00:05] 화자 열이 없는 발언\n[00:00:20] 김 대리(팀): 적은 그대로 보이는 화자\n시각도 없는 발언", segments, sttProvider: "자료 파일(STT 없음)", speakers: [] },
    });
    await page.getByRole("button", { name: /전사문/ }).first().click();
    const panel = page.getByRole("region", { name: "전사문" });
    await expect(panel).toContainText("화자 열이 없는 발언");
    await expect(panel).toContainText("김 대리(팀)");
    await expect(panel).toContainText("시각도 없는 발언");
    await expect(panel).not.toContainText("화자1");
  });
});

// ================= 파트 C: 할 일 화면 =================
test.describe("할 일 화면 개선", () => {
  test("보완 필요 업무 줄이 링크이고 상세에서 그 업무 줄로 이동·돌아가기 링크가 보인다", async ({ page }) => {
    await open(page, "/v2", { todosBody: todos({ needsCompletionItems: NEEDS }) });
    const link = page.getByRole("link", { name: "보완 필요 업무로 이동: 견적서 보완" });
    await expect(link).toHaveAttribute("href", "/v2/meetings/41?item=102&returnTo=%2Fv2");
    await link.click();
    await expect(page).toHaveURL(/\/v2\/meetings\/41\?item=102/);
    await expect(page.getByRole("heading", { level: 1, name: "주간 회의" })).toBeVisible();
    const row = page.locator("#ledger-item-102");
    await expect(row).toBeVisible();
    await expect(row).toBeFocused();
    await expect(row).toHaveAttribute("aria-current", "true");
    await page.getByRole("link", { name: "← 할 일로 돌아가기" }).click();
    await expect(page).toHaveURL(/\/v2$/);
  });

  test("수정 요청 대기에서 회의록으로 갔다가 돌아온다", async ({ page }) => {
    await open(page, "/v2", { todosBody: todos({ pendingChangeRequests: REQUESTS }) });
    const section = page.getByRole("region", { name: "수정 요청 대기" });
    const link = section.getByRole("link").first();
    await expect(link).toHaveAttribute("href", "/v2/meetings/41?item=101&returnTo=%2Fv2");
    await link.click();
    await expect(page.getByRole("link", { name: "← 할 일로 돌아가기" })).toHaveAttribute("href", "/v2");
  });

  test("returnTo 가 외부 주소·잘못된 값이면 돌아가기 링크는 회의록 목록으로 간다", async ({ page }) => {
    await open(page, "/v2/meetings/41?returnTo=https%3A%2F%2Fevil.example%2Fx", {});
    await expect(page.getByRole("heading", { level: 1, name: "주간 회의" })).toBeVisible();
    await expect(page.getByRole("link", { name: "← 회의록 목록으로" })).toHaveAttribute("href", "/v2/meetings");
    await page.goto("/v2/meetings/41?returnTo=%2F%2Fevil.example");
    await expect(page.getByRole("link", { name: "← 회의록 목록으로" })).toHaveAttribute("href", "/v2/meetings");
    await page.goto("/v2/meetings/41?returnTo=%2Fv2%2Fprojects");
    await expect(page.getByRole("link", { name: "← 이전 화면으로 돌아가기" })).toHaveAttribute("href", "/v2/projects");
  });

  test("승인 대기 건수 제목이 링크이다(한 건=그 항목, 프로젝트만=목록, 섞이면 할 일)", async ({ page }) => {
    const project = (id: number, name: string) => ({
      id, name, description: "", status: "pending_approval", departmentId: 1, departmentName: "개발팀", registeredBy: { id: 3, name: "김대리" },
      lead: null, approver: { id: 7, name: "한팀장" }, myRole: null, memberCount: 1, createdAt: "2026-10-01T00:00:00Z", decidedAt: null,
    });
    await open(page, "/v2", { projects: [project(5, "신제품 도입")] });
    const panel = page.getByRole("region", { name: "승인 대기" });
    await expect(panel.getByRole("link", { name: /승인 대기 1건/ })).toHaveAttribute("href", "/v2/projects/5");
    await page.unroute("**/api/projects");
    await page.route("**/api/projects", (route) => json(route, [project(5, "신제품 도입"), project(6, "공정 개선")]));
    await page.reload();
    await expect(panel.getByRole("link", { name: /승인 대기 2건/ })).toHaveAttribute("href", "/v2/projects");
    await page.unroute("**/api/me/todos");
    await page.route("**/api/me/todos", (route) => json(route, todos({ pendingChangeRequests: REQUESTS })));
    await page.reload();
    await expect(panel.getByRole("link", { name: /승인 대기 3건/ })).toHaveAttribute("href", "/v2");
  });
});

// ================= 파트 D: 보조창 좌우 전환 =================
const aside = (page: Page) => page.locator("aside").first();
const box = async (page: Page, selector: string) => (await page.locator(selector).first().boundingBox())!;

test.describe("보조창 좌우 전환", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("저장된 오른쪽 설정에서 전환하면 왼쪽으로 가고 쿠키에 저장되며 새로고침해도 유지된다", async ({ page }) => {
    await open(page, "/v2", { cookie: "right" }); // 기본 위치가 왼쪽으로 바뀌어(디자인 2단계) 저장된 오른쪽 설정으로 시작
    await expect(page.getByRole("navigation", { name: "주 메뉴" })).toBeVisible();
    const rightBox = await box(page, "aside");
    expect(rightBox.x).toBeGreaterThan(640);
    await page.getByRole("button", { name: "보조창을 왼쪽으로 옮기기" }).click();
    await expect.poll(async () => (await box(page, "aside")).x).toBeLessThan(10);
    const cookies = await page.context().cookies();
    expect(cookies.find((c) => c.name === "mi_side_position")?.value).toBe("left");
    // DOM 순서: 왼쪽이면 보조창이 본문보다 먼저(키보드 Tab 순서 = 시각 순서)
    expect(await page.evaluate(() => {
      const a = document.querySelector("aside");
      const m = document.querySelector("main");
      return !!(a && m && (a.compareDocumentPosition(m) & Node.DOCUMENT_POSITION_FOLLOWING));
    })).toBe(true);
    // 전환 버튼이 포커스를 유지하고 반대 방향 이름으로 바뀐다
    await expect(page.getByRole("button", { name: "보조창을 오른쪽으로 옮기기" })).toBeFocused();
    await page.reload();
    await expect.poll(async () => (await box(page, "aside")).x).toBeLessThan(10);
    // 접기 버튼은 패널과 가까운 왼쪽 끝, 경계선은 오른쪽
    const collapse = page.getByRole("button", { name: "회의록 추적 패널 접기" });
    expect((await collapse.boundingBox())!.x).toBeLessThan(400);
    expect(await aside(page).evaluate((el) => getComputedStyle(el).borderRightWidth)).toBe("1px");
    expect(await aside(page).evaluate((el) => getComputedStyle(el).borderLeftWidth)).toBe("0px");
    await collapse.click();
    await expect(page.getByRole("button", { name: "회의록 추적 패널 펼치기" })).toBeVisible();
    expect((await aside(page).boundingBox())!.width).toBeLessThan(60);
  });

  test("쿠키 값이 left 이면 서버 렌더부터 왼쪽이고, 알 수 없는 값은 기본값(왼쪽)이다", async ({ page, context }) => {
    await open(page, "/v2", { cookie: "left" });
    await expect(page.getByRole("navigation", { name: "주 메뉴" })).toBeVisible();
    expect((await box(page, "aside")).x).toBeLessThan(10);
    await context.clearCookies();
    await context.addCookies([{ name: "mi_side_position", value: "evil", url: `http://localhost:${process.env.E2E_PORT ?? 3100}` }]);
    await page.goto("/v2");
    await expect(page.getByRole("navigation", { name: "주 메뉴" })).toBeVisible();
    expect((await box(page, "aside")).x).toBeLessThan(10);
  });

  test("좌우 어느 쪽이든 375/768/1280px 에서 가로 넘침이 없다", async ({ page, context }) => {
    for (const side of ["left", "right"] as const) {
      await context.clearCookies();
      await open(page, "/v2", { cookie: side, todosBody: todos({ needsCompletionItems: NEEDS, pendingChangeRequests: REQUESTS }) });
      for (const width of [1280, 768, 375]) {
        await page.setViewportSize({ width, height: 800 });
        await expect(page.getByRole("heading", { level: 1, name: "할 일" })).toBeVisible();
        // 너비를 바꾼 직후에는 모바일 전환(matchMedia 이벤트)이 반영되기 전 한 프레임이 있어 안정될 때까지 기다린다
        await expect.poll(() => overflow(page), { message: `${side} ${width}px` }).toBeLessThanOrEqual(0);
      }
    }
  });

  test("모바일(768 미만)은 오른쪽 드로어 고정이고 전환 버튼이 없다", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await open(page, "/v2", { cookie: "left" });
    await page.getByRole("button", { name: "메뉴 열기" }).click();
    const drawer = page.getByRole("complementary", { name: "메뉴 드로어" });
    await expect(drawer).toBeVisible();
    const b = (await drawer.boundingBox())!;
    expect(b.x + b.width).toBeGreaterThan(370); // 오른쪽 가장자리에 붙는다
    await expect(page.getByRole("button", { name: /보조창을 .*쪽으로 옮기기/ })).toHaveCount(0);
  });
});
