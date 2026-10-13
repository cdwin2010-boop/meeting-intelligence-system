/*
 * 엔진 시험 모드 E2E(작업 91-1, 시험 전용). 백엔드 없이 page.route 로 응답한다(데이터·주소·키는 모두 가상, 실제 서버 호출 없음).
 * - 시험 모드 꺼짐: 메뉴·탭·올리기 엔진 선택·재처리 엔진 선택이 모두 숨겨진다(기존 화면 그대로)
 * - 시험 모드 켜짐: 설정 입력·저장(키 마스킹)·로딩 테스트 결과(성공/실패/409), 올리기·재처리 엔진 선택, 담당자는 숨김
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const MANAGER = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const STAFF = { id: 9, name: "김대리", rank: "staff", tenantId: 1 };
const SYSADMIN = { id: 3, name: "시스템관리", rank: "staff", tenantId: 1, isSystemAdmin: true };
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const SECRET = "sk-e2e-secret-key-9999";
const SETTINGS = {
  baseUrl: "http://127.0.0.1:9/v1", model: "Qwen3-8B", apiKeySet: true, apiKeyMasked: "●●●●●●●●", timeoutSeconds: 300, chunkMaxChars: 0,
  configured: true, defaultEngine: "default", notice: "외부 터널을 거치므로 시험용 데이터만 사용하세요",
};
const LOAD_OK = {
  ok: true, modelListed: true, modelFound: true, currentModel: "Qwen3-8B", availableModels: ["Qwen3-8B"], responseMs: 842, jsonParsed: true,
  failureStage: "", failureReason: "",
};

interface Options {
  me?: Record<string, unknown>;
  testMode?: boolean;
  defaultEngine?: "default" | "remote";
  loadTest?: { status: number; body: unknown };
  detail?: Record<string, unknown>;
}

async function mount(page: Page, path: string, options: Options = {}) {
  const calls: { method: string; path: string; body: string | null }[] = [];
  const me = options.me ?? MANAGER;
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, me) : json(route, { detail: "x" }, 401),
  );
  await page.route("**/api/system/engine", (route) =>
    json(route, { engine: "gemini", isFake: false, engineTestMode: options.testMode ?? false, engineTestDefault: options.defaultEngine ?? "default" }),
  );
  await page.route("**/api/me/todos", (route) =>
    json(route, { awaitingConfirmMeetings: { total: 0, items: [] }, needsCompletionItems: { total: 0, items: [] }, myItems: { total: 0, items: [] }, unreadAutoConfirmed: { total: 0, items: [] }, pendingChangeRequests: { total: 0, items: [] } }),
  );
  await page.route("**/api/accounts", (route) => json(route, [MANAGER, STAFF].map(({ id, name, rank }) => ({ id, name, rank }))));
  await page.route("**/api/admin/engine-test/remote", (route) => {
    const request = route.request();
    calls.push({ method: request.method(), path: "/remote", body: request.postData() });
    if (request.method() === "PUT") {
      const sent = request.postDataJSON() as Record<string, unknown>;
      return json(route, { ...SETTINGS, baseUrl: sent.baseUrl, model: sent.model, timeoutSeconds: sent.timeoutSeconds, chunkMaxChars: sent.chunkMaxChars });
    }
    return json(route, SETTINGS);
  });
  await page.route("**/api/admin/engine-test/remote/load-test", (route) => {
    calls.push({ method: "POST", path: "/load-test", body: null });
    const result = options.loadTest ?? { status: 200, body: LOAD_OK };
    return json(route, result.body, result.status);
  });
  await page.route("**/api/meetings/upload", (route) => {
    calls.push({ method: "POST", path: "/upload", body: route.request().postData() });
    return json(route, { meetingId: 77, jobId: 5 }, 202);
  });
  const detail = {
    id: 77, title: "원격 시험 회의", heldAt: "2026-10-01T04:20:00Z", summary: "", decisions: [], status: "failed", confirmKind: null, confirmedBy: null,
    confirmedAt: null, firstCreatedAt: "2026-10-01T05:00:00Z", autoConfirmAt: null, registeredBy: { id: 7, name: "한팀장" }, origin: "audio_minutes",
    participants: [], actionItems: [], recentEvents: [], phase: "active", guestParticipants: [],
    processing: { status: "failed", errorCode: "remote_unavailable", startedAt: "2026-10-01T02:00:00Z", finishedAt: "2026-10-01T02:01:00Z" },
    minutes: { purpose: "내용없음", discussion: "내용없음", decisions: "내용없음", risks: "내용없음", nextAgenda: "내용없음", engine: "gemini", updatedBy: null, updatedAt: null },
    ...options.detail,
  };
  await page.route(/\/api\/meetings\/\d+$/, (route) => json(route, withAllowed(detail as never, me as never, { lead: true })));
  await page.route(/\/api\/meetings\/\d+\/(speakers|change-requests)$/, (route) =>
    route.request().url().endsWith("speakers") ? json(route, { labels: [], speakers: [], autoAssignedItemIds: [] }) : json(route, []),
  );
  await page.route(/\/api\/meetings\/\d+\/reprocess$/, (route) => {
    calls.push({ method: "POST", path: "/reprocess", body: route.request().postData() });
    return json(route, { meetingId: 77, jobId: 6 }, 202);
  });
  await page.goto("/v2/login");
  await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto(path);
  return calls;
}

const AUDIO = { name: "weekly.m4a", mimeType: "audio/mp4", buffer: Buffer.from("fake-audio") };
const engineGroup = (page: Page) => page.getByRole("group", { name: "처리 엔진" });

test.describe("시험 모드 꺼짐(납품 설정): 화면에 아무것도 없다", () => {
  test.use({ viewport: { width: 1280, height: 900 } });

  test("메뉴에 엔진 시험이 없고 올리기에 처리 엔진 선택이 없다", async ({ page }) => {
    const calls = await mount(page, "/v2/upload");
    await expect(page.getByRole("heading", { level: 1, name: "회의록 올리기" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "주 메뉴" }).getByRole("link")).toHaveText(["할 일", "업무 현황", "회의록", "프로젝트"]);
    await expect(engineGroup(page)).toHaveCount(0);
    await page.locator('input[type="file"]:not([capture])').setInputFiles(AUDIO);
    await page.getByRole("group", { name: "회의 유형" }).locator("label").filter({ has: page.getByRole("radio", { name: "정기회의", exact: true }) }).click();
    await page.getByRole("button", { name: "올리고 분석 시작" }).click();
    await expect(page).toHaveURL(/\/v2\/meetings\/77$/);
    expect(calls.find((c) => c.path === "/upload")!.body).not.toContain('name="engine"'); // 현행 요청 그대로
  });

  test("/v2/engine-test 로 직접 들어가도 아무것도 그려지지 않고 설정을 조회하지 않는다", async ({ page }) => {
    const calls = await mount(page, "/v2/engine-test");
    await page.waitForTimeout(500);
    await expect(page.getByRole("form", { name: "원격 엔진 설정" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "엔진 시험" })).toHaveCount(0);
    expect(calls.filter((c) => c.path === "/remote")).toHaveLength(0);
  });

  test("재처리 팝업에 처리 엔진 선택이 없다", async ({ page }) => {
    await mount(page, "/v2/meetings/77");
    await page.getByRole("button", { name: "다시 처리" }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("group", { name: /처리 엔진/ })).toHaveCount(0);
  });

  test("시스템 관리자 화면에 엔진 시험 탭이 없다", async ({ page }) => {
    await mount(page, "/v2/admin/engine-test", { me: SYSADMIN });
    await expect(page.getByRole("navigation", { name: "시스템 관리 메뉴" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "시스템 관리 메뉴" }).getByRole("link", { name: "엔진 시험" })).toHaveCount(0);
  });
});

test.describe("시험 모드 켜짐", () => {
  test.use({ viewport: { width: 1280, height: 900 } });

  test("관리자: 메뉴에서 엔진 시험을 열어 저장하고(키 마스킹) 로딩 테스트 결과를 본다", async ({ page }) => {
    const calls = await mount(page, "/v2", { testMode: true });
    const nav = page.getByRole("navigation", { name: "주 메뉴" });
    await nav.getByRole("link", { name: "엔진 시험" }).click();
    await expect(page).toHaveURL(/\/v2\/engine-test$/);
    await expect(page.getByRole("status", { name: "시험 전용 안내" })).toContainText("외부 터널을 거치므로 시험용 데이터만 사용하세요");
    const form = page.getByRole("form", { name: "원격 엔진 설정" });
    await expect(form.getByLabel("서버 주소")).toHaveValue("http://127.0.0.1:9/v1");
    await expect(form.getByLabel("모델명")).toHaveValue("Qwen3-8B");
    // 키는 화면에 평문으로 나오지 않는다(저장 여부·마스킹 표시만)
    await expect(form.getByLabel("API 키")).toHaveValue("");
    await expect(form).toContainText("저장된 키가 있습니다");

    await form.getByLabel("서버 주소").fill("https://tunnel.example.test/v1");
    await form.getByLabel("API 키").fill(SECRET);
    await form.getByRole("button", { name: "저장" }).click();
    await expect(page.getByRole("status").filter({ hasText: "저장했습니다." })).toBeVisible();
    const put = calls.find((c) => c.method === "PUT")!;
    expect(JSON.parse(put.body!)).toMatchObject({ baseUrl: "https://tunnel.example.test/v1", model: "Qwen3-8B", apiKey: SECRET });
    await expect(form.getByLabel("API 키")).toHaveValue(""); // 저장 뒤 입력칸을 비운다
    await expect(page.locator("body")).not.toContainText(SECRET);

    await page.getByRole("button", { name: "로딩 테스트" }).click();
    const result = page.getByRole("region", { name: "로딩 테스트 결과" });
    await expect(result).toContainText("로딩 테스트 성공");
    await expect(result).toContainText("842 ms");
    await expect(result).toContainText("Qwen3-8B");
    await expect(result).toContainText("JSON 파싱");
    await expect(page.locator("body")).not.toContainText(SECRET);
  });

  test("로딩 테스트 실패 사유와 진행 중 작업 409 안내", async ({ page }) => {
    await mount(page, "/v2/engine-test", {
      testMode: true,
      loadTest: { status: 200, body: { ...LOAD_OK, ok: false, modelListed: false, modelFound: false, responseMs: null, jsonParsed: false, failureStage: "models", failureReason: "원격 엔진에 연결할 수 없습니다" } },
    });
    await page.getByRole("button", { name: "로딩 테스트" }).click();
    const result = page.getByRole("region", { name: "로딩 테스트 결과" });
    await expect(result).toContainText("로딩 테스트 실패");
    await expect(result).toContainText("원격 엔진에 연결할 수 없습니다");
  });

  test("진행 중인 처리가 있으면 서버 문구(409)를 그대로 보여 준다", async ({ page }) => {
    await mount(page, "/v2/engine-test", {
      testMode: true,
      loadTest: { status: 409, body: { detail: "음성 인식·업무 추출 작업이 진행 중이라 로딩 테스트를 할 수 없습니다. 작업이 끝난 뒤 다시 시도하세요" } },
    });
    await page.getByRole("button", { name: "로딩 테스트" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "작업이 진행 중이라 로딩 테스트를 할 수 없습니다" })).toBeVisible();
  });

  test("올리기: 처리 엔진 선택이 보이고 고른 값이 요청에 실린다(기본값은 설정값)", async ({ page }) => {
    const calls = await mount(page, "/v2/upload", { testMode: true, defaultEngine: "remote" });
    const group = engineGroup(page);
    await expect(group).toBeVisible();
    await expect(group).toContainText("시험용 데이터만 사용하세요");
    await expect(group.getByRole("radio", { name: "원격(시험용)" })).toBeChecked();
    await group.getByRole("radio", { name: "기본 엔진" }).check();
    await group.getByRole("radio", { name: "원격(시험용)" }).check();
    await page.locator('input[type="file"]:not([capture])').setInputFiles(AUDIO);
    await page.getByRole("group", { name: "회의 유형" }).locator("label").filter({ has: page.getByRole("radio", { name: "정기회의", exact: true }) }).click();
    await page.getByRole("button", { name: "올리고 분석 시작" }).click();
    await expect(page).toHaveURL(/\/v2\/meetings\/77$/);
    const body = calls.find((c) => c.path === "/upload")!.body!;
    expect(body).toMatch(/name="engine"\r?\n\r?\nremote/);
  });

  test("재처리 팝업에서 엔진을 고르면 요청 본문에 engine 이 실린다", async ({ page }) => {
    const calls = await mount(page, "/v2/meetings/77", { testMode: true, defaultEngine: "remote" });
    await page.getByRole("button", { name: "다시 처리" }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog.getByRole("radio", { name: "원격(시험용)" })).toBeChecked();
    await dialog.locator("label").filter({ hasText: "기본 엔진" }).click(); // 라디오는 시각적으로 숨겨져 있어 라벨을 누른다
    await dialog.getByRole("button", { name: "재처리하기" }).click();
    await expect.poll(() => calls.find((c) => c.path === "/reprocess")?.body ?? null).not.toBeNull();
    expect(JSON.parse(calls.find((c) => c.path === "/reprocess")!.body!)).toEqual({ engine: "default" });
  });

  test("처리된 회의록에 사용 엔진이 '원격(시험용) · 모델명' 으로 표시된다", async ({ page }) => {
    await mount(page, "/v2/meetings/77", {
      testMode: true,
      detail: {
        status: "awaiting_confirmation",
        minutes: { purpose: "목적", discussion: "논의", decisions: "결정", risks: "내용없음", nextAgenda: "내용없음", engine: "원격(시험용) · Qwen3-8B", updatedBy: null, updatedAt: null },
      },
    });
    await expect(page.getByText("생성 엔진 원격(시험용) · Qwen3-8B")).toBeVisible();
  });

  test("담당자는 메뉴·올리기·재처리·설정 화면 어디서도 엔진 선택을 볼 수 없다", async ({ page }) => {
    await mount(page, "/v2/upload", { me: STAFF, testMode: true, defaultEngine: "remote" });
    await expect(page.getByRole("heading", { level: 1, name: "회의록 올리기" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "주 메뉴" }).getByRole("link", { name: "엔진 시험" })).toHaveCount(0);
    await expect(engineGroup(page)).toHaveCount(0);
    await page.goto("/v2/engine-test");
    await expect(page).toHaveURL(/\/v2$/);
  });

  test("시스템 관리자: 시스템 관리 탭에 엔진 시험이 있다", async ({ page }) => {
    await mount(page, "/v2/admin/engine-test", { me: SYSADMIN, testMode: true });
    const tab = page.getByRole("navigation", { name: "시스템 관리 메뉴" }).getByRole("link", { name: "엔진 시험" });
    await expect(tab).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("form", { name: "원격 엔진 설정" })).toBeVisible();
  });
});

test("375px 에서 엔진 시험 화면 가로 넘침이 없다", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await mount(page, "/v2/engine-test", { testMode: true });
  await expect(page.getByRole("form", { name: "원격 엔진 설정" })).toBeVisible();
  await page.getByRole("button", { name: "로딩 테스트" }).click();
  await expect(page.getByRole("region", { name: "로딩 테스트 결과" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
});
