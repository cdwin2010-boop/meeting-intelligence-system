/*
 * 연결 점검·벤치마크 측정 E2E(작업 L-3a, 시험 전용). 백엔드 없이 page.route 로 응답한다(데이터·주소·키는 모두 가상, 실제 서버 호출 없음).
 * - 빠른 점검 시작 → 진행(현재 항목·완료 n/전체·경과 시간) → 완료 → 결과 파일 내려받기
 * - 연결 끊김 안내 문구 → 이어하기, 중복 시작 거부(409) 안내, 새 입력칸 없음, 시험 모드 꺼짐이면 영역 없음
 */
import { expect, test, type Page, type Route } from "./helpers/test";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const MANAGER = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const SETTINGS = {
  baseUrl: "http://127.0.0.1:9/v1", model: "Qwen3-8B", apiKeySet: true, apiKeyMasked: "●●●●●●●●", timeoutSeconds: 300, chunkMaxChars: 0,
  configured: true, defaultEngine: "default", notice: "외부 터널을 거치므로 시험용 데이터만 사용하세요",
};
const IDLE = {
  status: "idle", mode: "", runId: "", currentLabel: "", doneUnits: 0, totalUnits: 0, elapsedSeconds: 0, message: "", canResume: false,
  canDownload: false, retries: 0,
};
const DISCONNECT = "연결이 끊겼습니다. 새 주소를 입력하고 이어하기를 눌러 주세요";

interface Mounted {
  calls: { method: string; path: string; body: string | null }[];
  setStatus: (next: Record<string, unknown>) => void;
}

async function mount(page: Page, options: { testMode?: boolean; start?: { status: number; body: unknown } } = {}): Promise<Mounted> {
  const calls: Mounted["calls"] = [];
  let current: Record<string, unknown> = { ...IDLE };
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, MANAGER) : json(route, { detail: "x" }, 401),
  );
  await page.route("**/api/system/engine", (route) =>
    json(route, { engine: "gemini", isFake: false, engineTestMode: options.testMode ?? true, engineTestDefault: "default" }),
  );
  await page.route("**/api/me/todos", (route) =>
    json(route, { awaitingConfirmMeetings: { total: 0, items: [] }, needsCompletionItems: { total: 0, items: [] }, myItems: { total: 0, items: [] }, unreadAutoConfirmed: { total: 0, items: [] }, pendingChangeRequests: { total: 0, items: [] } }),
  );
  await page.route("**/api/accounts", (route) => json(route, [MANAGER]));
  await page.route("**/api/admin/engine-test/remote", (route) => json(route, SETTINGS));
  await page.route("**/api/admin/engine-test/bench/status", (route) => {
    calls.push({ method: "GET", path: "/status", body: null });
    return json(route, current);
  });
  await page.route("**/api/admin/engine-test/bench/start", (route) => {
    calls.push({ method: "POST", path: "/start", body: route.request().postData() });
    if (options.start) return json(route, options.start.body, options.start.status);
    current = { ...IDLE, status: "running", mode: "quick", runId: "r1", currentLabel: "A 연결·모델 상태 · 1회차", doneUnits: 0, totalUnits: 4 };
    return json(route, current, 202);
  });
  await page.route("**/api/admin/engine-test/bench/resume", (route) => {
    calls.push({ method: "POST", path: "/resume", body: null });
    current = { ...current, status: "running", message: "", canResume: false, currentLabel: "C 컨텍스트 경계(단일·분할) · 1회차" };
    return json(route, current, 202);
  });
  await page.route("**/api/admin/engine-test/bench/stop", (route) => {
    calls.push({ method: "POST", path: "/stop", body: null });
    current = { ...current, status: "stopped", canResume: true, message: "사용자가 중단했습니다. 이어하기로 남은 항목을 실행할 수 있습니다" };
    return json(route, current);
  });
  await page.route("**/api/admin/engine-test/bench/result", (route) => {
    calls.push({ method: "GET", path: "/result", body: null });
    return route.fulfill({
      status: 200, contentType: "application/json", headers: { "Content-Disposition": 'attachment; filename="l3-bench-r1.json"' },
      body: JSON.stringify({ schema: "dots-bench-v1", runId: "r1" }),
    });
  });
  await page.goto("/v2/login");
  await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto("/v2/engine-test");
  return { calls, setStatus: (next) => (current = { ...IDLE, ...next }) };
}

const region = (page: Page) => page.getByRole("region", { name: "연결 점검 및 벤치마크 측정" });

test.describe("연결 점검·벤치마크 측정(시험 모드 켜짐)", () => {
  test.use({ viewport: { width: 1280, height: 900 } });

  test("빠른 점검 시작 → 진행 상태 → 완료 → 결과 파일 내려받기", async ({ page }) => {
    const m = await mount(page);
    const panel = region(page);
    await expect(panel.getByRole("button", { name: "빠른 점검" })).toBeEnabled();
    await expect(panel.getByRole("button", { name: "이어하기" })).toBeDisabled();
    await expect(panel.getByRole("button", { name: "결과 파일 내려받기" })).toBeDisabled();
    await panel.getByRole("button", { name: "빠른 점검" }).click();
    expect(JSON.parse(m.calls.find((c) => c.path === "/start")!.body!)).toEqual({ mode: "quick" });
    const progress = panel.getByRole("status", { name: "측정 진행 상태" });
    await expect(progress).toContainText("측정 중");
    await expect(progress).toContainText("A 연결·모델 상태 · 1회차");
    await expect(progress).toContainText("0/4");
    await expect(panel.getByRole("button", { name: "중단" })).toBeEnabled();
    await expect(panel.getByRole("button", { name: "빠른 점검" })).toBeDisabled();

    // 다음 조회(2초 주기)에서 진행이 갱신되고 끝나면 내려받기가 열린다
    m.setStatus({ status: "running", mode: "quick", runId: "r1", currentLabel: "B 단건 추출 · 1회차", doneUnits: 1, totalUnits: 4, elapsedSeconds: 75, canDownload: true });
    await expect(progress).toContainText("1/4", { timeout: 8000 });
    await expect(progress).toContainText("1분 15초");
    m.setStatus({ status: "completed", mode: "quick", runId: "r1", doneUnits: 4, totalUnits: 4, elapsedSeconds: 190, canDownload: true });
    await expect(progress).toContainText("측정 완료", { timeout: 8000 });
    await expect(progress).toContainText("4/4");
    const download = page.waitForEvent("download");
    await panel.getByRole("button", { name: "결과 파일 내려받기" }).click();
    expect((await download).suggestedFilename()).toBe("l3-bench-result.json");
    expect(m.calls.some((c) => c.path === "/result")).toBe(true);
  });

  test("연결 끊김: 안내 문구가 보이고 이어하기로 남은 항목을 다시 시작한다", async ({ page }) => {
    const m = await mount(page);
    m.setStatus({ status: "interrupted", mode: "full", runId: "r2", doneUnits: 7, totalUnits: 13, elapsedSeconds: 600, canResume: true, canDownload: true, message: DISCONNECT });
    await page.reload();
    const panel = region(page);
    await expect(panel.getByRole("alert").filter({ hasText: DISCONNECT })).toBeVisible();
    await expect(panel.getByRole("status", { name: "측정 진행 상태" })).toContainText("중단됨(연결 끊김)");
    await expect(panel.getByRole("status", { name: "측정 진행 상태" })).toContainText("7/13");
    await panel.getByRole("button", { name: "이어하기" }).click();
    expect(m.calls.some((c) => c.method === "POST" && c.path === "/resume")).toBe(true);
    await expect(panel.getByRole("status", { name: "측정 진행 상태" })).toContainText("측정 중");
    await expect(panel.getByRole("alert").filter({ hasText: DISCONNECT })).toHaveCount(0);
  });

  test("중단 버튼: 중단 후 이어하기가 열린다", async ({ page }) => {
    const m = await mount(page);
    await region(page).getByRole("button", { name: "전체 측정" }).click();
    await region(page).getByRole("button", { name: "중단" }).click();
    expect(m.calls.some((c) => c.path === "/stop")).toBe(true);
    await expect(region(page).getByRole("status", { name: "측정 진행 상태" })).toContainText("중단됨");
    await expect(region(page).getByRole("button", { name: "이어하기" })).toBeEnabled();
  });

  test("이미 측정 중이면(409) 서버 안내를 보여 준다", async ({ page }) => {
    await mount(page, { start: { status: 409, body: { detail: "이미 측정이 진행 중입니다" } } });
    await region(page).getByRole("button", { name: "빠른 점검" }).click();
    await expect(region(page).getByRole("alert")).toContainText("이미 측정이 진행 중입니다");
  });

  test("주소·키 입력칸을 새로 만들지 않는다(위 원격 엔진 설정을 그대로 쓴다)", async ({ page }) => {
    await mount(page);
    await expect(region(page).getByRole("button", { name: "빠른 점검" })).toBeVisible();
    await expect(region(page).getByRole("textbox")).toHaveCount(0);
    await expect(page.getByRole("form", { name: "원격 엔진 설정" }).getByLabel("서버 주소")).toBeVisible();
  });
});

test.describe("시험 모드 꺼짐", () => {
  test("측정 영역이 그려지지 않고 상태를 조회하지 않는다", async ({ page }) => {
    const m = await mount(page, { testMode: false });
    await page.waitForTimeout(500);
    await expect(region(page)).toHaveCount(0);
    expect(m.calls.filter((c) => c.path === "/status")).toHaveLength(0);
  });
});
