/*
 * 작업 L-4a E2E: 연결 진단 버튼(분류 표시), 정상이 아니면 측정 시작 차단, 모델 후보 선택.
 * 백엔드 없이 page.route 로 응답한다(데이터·주소·키는 모두 가상, 실제 서버 호출 없음).
 */
import { expect, test, type Page, type Route } from "./helpers/test";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const MANAGER = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const SETTINGS = {
  baseUrl: "http://127.0.0.1:9/v1", model: "qwen2.5:14b", apiKeySet: true, apiKeyMasked: "●●●●●●●●", timeoutSeconds: 300, chunkMaxChars: 0,
  configured: true, defaultEngine: "default", notice: "외부 터널을 거치므로 시험용 데이터만 사용하세요",
};
const IDLE = {
  status: "idle", mode: "", runId: "", currentLabel: "", doneUnits: 0, totalUnits: 0, elapsedSeconds: 0, message: "", canResume: false,
  canDownload: false, retries: 0,
};

interface Diag {
  category: string;
  httpStatus: number | null;
  elapsedMs: number;
  hint: string;
  checkedAt: string;
  candidates: string[];
}
const diag = (category: string, extra: Partial<Diag> = {}): Diag => ({
  category, httpStatus: 200, elapsedMs: 42, hint: "힌트 문구", checkedAt: "2026-10-15T00:00:00+00:00", candidates: [], ...extra,
});

async function mount(page: Page, diagnosis: Diag, startFailure?: { message: string; candidates: string[] }) {
  const calls: string[] = [];
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, MANAGER) : json(route, { detail: "x" }, 401),
  );
  await page.route("**/api/system/engine", (route) => json(route, { engine: "gemini", isFake: false, engineTestMode: true, engineTestDefault: "default" }));
  await page.route("**/api/me/todos", (route) =>
    json(route, { awaitingConfirmMeetings: { total: 0, items: [] }, needsCompletionItems: { total: 0, items: [] }, myItems: { total: 0, items: [] }, unreadAutoConfirmed: { total: 0, items: [] }, pendingChangeRequests: { total: 0, items: [] } }),
  );
  await page.route("**/api/accounts", (route) => json(route, [MANAGER]));
  await page.route("**/api/admin/engine-test/remote", (route) => json(route, SETTINGS));
  await page.route("**/api/admin/engine-test/remote/diagnose", (route) => {
    calls.push("diagnose");
    return json(route, diagnosis);
  });
  await page.route("**/api/admin/engine-test/bench/status", (route) => json(route, IDLE));
  await page.route("**/api/admin/engine-test/bench/start", (route) => {
    calls.push("start");
    if (startFailure) {
      return json(route, { detail: { message: startFailure.message, category: "MODEL_MISSING", candidates: startFailure.candidates } }, 409);
    }
    return json(route, { ...IDLE, status: "running", mode: "quick", runId: "r1", totalUnits: 4 }, 202);
  });
  await page.goto("/v2/login");
  await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto("/v2/engine-test");
  return calls;
}

test.use({ viewport: { width: 1280, height: 900 } });

test("연결 진단 버튼: 분류와 힌트가 한 줄로 표시된다", async ({ page }) => {
  await mount(page, diag("AUTH_FAILED", { httpStatus: 401, hint: "서버가 인증을 거절했습니다. API 키를 확인하세요" }));
  await page.getByRole("button", { name: "연결 진단" }).click();
  const box = page.getByRole("region", { name: "연결 진단 결과" });
  await expect(box.getByRole("status")).toContainText("인증 실패");
  await expect(box.getByRole("status")).toContainText("API 키를 확인하세요");
  await expect(box).toContainText("HTTP 401");
});

test("정상이 아니면 측정 시작이 차단되고 분류 안내가 즉시 보인다", async ({ page }) => {
  await mount(page, diag("SERVER_ERROR"), { message: "측정을 시작하지 않았습니다. 연결 진단 결과: 서버 내부 오류이거나 응답이 없습니다", candidates: [] });
  const panel = page.getByRole("region", { name: "연결 점검 및 벤치마크 측정" });
  await panel.getByRole("button", { name: "빠른 점검" }).click();
  await expect(panel.getByRole("alert")).toContainText("측정을 시작하지 않았습니다");
  await expect(panel.getByRole("status", { name: "측정 진행 상태" })).toContainText("대기 중");
});

test("모델 없음: 가까운 후보를 보여 주고 선택하면 모델명 칸에 채워진다(저장은 따로)", async ({ page }) => {
  await mount(page, diag("MODEL_MISSING", { hint: "서버에 설정한 모델이 없습니다", candidates: ["qwen2.5:7b", "Qwen2.5-7B-Instruct-Q4_K_M.gguf:latest"] }));
  const modelInput = page.getByRole("form", { name: "원격 엔진 설정" }).getByLabel("모델명");
  await expect(modelInput).toHaveValue("qwen2.5:14b");
  await page.getByRole("button", { name: "연결 진단" }).click();
  const group = page.getByRole("group", { name: "모델명 후보" });
  await expect(group.getByRole("button", { name: "후보 qwen2.5:7b" })).toBeVisible();
  await group.getByRole("button", { name: "후보 qwen2.5:7b" }).click();
  await expect(modelInput).toHaveValue("qwen2.5:7b");
});
