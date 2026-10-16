/*
 * 벤치마크 변수 입력·프리셋·맥락 스윕·이력 E2E(작업 L-5, 시험 전용). 백엔드 없이 page.route 로 응답한다(주소·키·데이터는 모두 가상).
 * - 입력란 옆 "허용 a~b, 기본 c"와 가이드 상시 표시, 범위 밖 값이면 문제 입력란 오류·시작 차단(입력값 유지)
 * - 프리셋 선택 → "프리셋 X 적용됨", 수정 → "프리셋 X 기반, 수정됨(항목: 값)", 기본값으로 되돌리기
 * - OpenAI 호환 방식: 맥락 입력란 비활성 사유, 맥락 스윕 비활성 사유 / Ollama: 스윕이 sweep:true 로 전송
 * - 서버 422 오류의 문제 항목 표시, 이전 측정 비교 표
 */
import { expect, test, type Page, type Route } from "./helpers/test";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const MANAGER = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const SETTINGS = {
  baseUrl: "http://127.0.0.1:9", model: "Qwen3-8B", apiKeySet: true, apiKeyMasked: "●●●●●●●●", timeoutSeconds: 300, chunkMaxChars: 0,
  configured: true, defaultEngine: "default", notice: "외부 터널을 거치므로 시험용 데이터만 사용하세요", connectionType: "ollama",
};
const IDLE = {
  status: "idle", mode: "", runId: "", currentLabel: "", doneUnits: 0, totalUnits: 0, elapsedSeconds: 0, message: "", canResume: false,
  canDownload: false, retries: 0,
};

function paramsBody(connection: "ollama" | "openai") {
  const sendable = connection === "ollama";
  const def = (key: string, label: string, unit: string, d: number, min: number, max: number, guide: string, extra: Record<string, unknown> = {}) => ({
    key, label, unit, type: "int", default: d, min, max, guide, autoValue: null, disabledReason: "", ...extra,
  });
  return {
    params: [
      def("contextTokens", "맥락 길이", "토큰", 8192, 2048, 32768, "모델이 한 번에 읽는 길이", {
        disabledReason: sendable ? "" : "이 방식은 맥락을 서버에 보낼 수 없음",
      }),
      def("chunkChars", "구간 크기", "글자", 0, 200, 12000, "0 이면 자동", { autoValue: 0 }),
      def("outputReserveTokens", "출력 예약 토큰", "토큰", 2048, 256, 4096, "답변용 예약"),
      def("repeat", "반복 횟수", "회", 5, 1, 20, "항목별 반복"),
      def("driftRuns", "드리프트 횟수", "회", 5, 1, 20, "연속 실행"),
    ],
    presets: [
      { id: "A", name: "빠른 확인", values: { contextTokens: 8192, chunkChars: 0, repeat: 1, driftRuns: 1 } },
      { id: "B", name: "표준", values: { contextTokens: 8192, chunkChars: 0, repeat: 5, driftRuns: 5 } },
      { id: "C", name: "중간 맥락", values: { contextTokens: 16384, chunkChars: 0, repeat: 5, driftRuns: 5 } },
      { id: "D", name: "큰 맥락", values: { contextTokens: 24576, chunkChars: 0, repeat: 3, driftRuns: 3 } },
    ],
    defaults: { contextTokens: 8192, chunkChars: 0, outputReserveTokens: 2048, repeat: 5, driftRuns: 5 },
    connectionType: connection, contextSendable: sendable, contextDisabledReason: sendable ? "" : "이 방식은 맥락을 서버에 보낼 수 없음",
    sweepOrder: ["B", "C", "D"], sweepEnabled: sendable,
    sweepDisabledReason: sendable ? "" : "OpenAI 호환 방식은 맥락을 서버에 보낼 수 없어 맥락 스윕을 쓸 수 없음",
  };
}

const HISTORY = {
  items: [
    {
      runId: "aaaaaaaaaaaa", startedAt: "2026-10-16T01:02:03+00:00", status: "completed", preset: "C", presetSummary: "프리셋 C 적용됨",
      connectionType: "ollama", model: "Qwen3-8B", contextTokens: 16384, completedUnits: 30, totalUnits: 30, tokensPerSecond: 41.5,
      vramBytes: 6442450944, verifiedRate: 0.95, warningCount: 1, sweepId: "",
    },
  ],
};

interface Mounted {
  starts: string[];
}

async function mount(page: Page, options: { connection?: "ollama" | "openai"; startError?: { status: number; body: unknown } } = {}): Promise<Mounted> {
  const starts: string[] = [];
  const connection = options.connection ?? "ollama";
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, MANAGER) : json(route, { detail: "x" }, 401),
  );
  await page.route("**/api/system/engine", (route) =>
    json(route, { engine: "gemini", isFake: false, engineTestMode: true, engineTestDefault: "default" }),
  );
  await page.route("**/api/accounts", (route) => json(route, [MANAGER]));
  await page.route("**/api/admin/engine-test/remote", (route) => json(route, { ...SETTINGS, connectionType: connection }));
  await page.route("**/api/admin/engine-test/bench/status", (route) => json(route, IDLE));
  await page.route("**/api/admin/engine-test/bench/params", (route) => json(route, paramsBody(connection)));
  await page.route("**/api/admin/engine-test/bench/models", (route) => json(route, { models: ["Qwen3-8B", "gemma3:12b"], error: "" }));
  await page.route("**/api/admin/engine-test/bench/history", (route) => json(route, HISTORY));
  await page.route("**/api/admin/engine-test/bench/start", (route) => {
    starts.push(route.request().postData() ?? "");
    if (options.startError) return json(route, options.startError.body, options.startError.status);
    return json(route, { ...IDLE, status: "running", mode: "full", runId: "r1", totalUnits: 30, settingsSummary: "프리셋 B 적용됨" }, 202);
  });
  await page.goto("/v2/login");
  await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto("/v2/engine-test");
  return { starts };
}

const region = (page: Page) => page.getByRole("region", { name: "연결 점검 및 벤치마크 측정" });

test.describe("벤치마크 변수·프리셋·스윕·이력", () => {
  test.use({ viewport: { width: 1280, height: 1000 } });

  test("입력란 옆에 허용 범위·기본값·가이드가 항상 보인다", async ({ page }) => {
    await mount(page);
    const box = region(page);
    await expect(box.getByText("허용 2048~32768, 기본 8192")).toBeVisible();
    await expect(box.getByText("허용 200~12000, 기본 0")).toBeVisible();
    await expect(box.getByText("모델이 한 번에 읽는 길이", { exact: false })).toBeVisible();
    await expect(box.getByLabel(/^반복 횟수/)).toHaveValue("5");
  });

  test("프리셋 적용 표시 → 수정 표시 → 기본값으로 되돌리기", async ({ page }) => {
    await mount(page);
    const box = region(page);
    await box.getByRole("button", { name: "프리셋 C 중간 맥락" }).click();
    await expect(box.getByLabel(/^맥락 길이/)).toHaveValue("16384");
    await expect(box.getByRole("status", { name: "프리셋 상태" })).toHaveText("프리셋 C 적용됨");
    await box.getByLabel(/^맥락 길이/).fill("12288");
    await expect(box.getByRole("status", { name: "프리셋 상태" })).toHaveText("프리셋 C 기반, 수정됨(맥락 길이: 12288)");
    await box.getByRole("button", { name: "기본값으로 되돌리기" }).click();
    await expect(box.getByLabel(/^맥락 길이/)).toHaveValue("8192");
    await expect(box.getByRole("status", { name: "프리셋 상태" })).toHaveText("프리셋 없음(기본값)");
  });

  test("범위 밖 값은 오류 문장과 함께 시작을 막고 입력값은 유지된다", async ({ page }) => {
    const m = await mount(page);
    const box = region(page);
    await box.getByLabel(/^반복 횟수/).fill("99");
    await expect(box.getByRole("alert").filter({ hasText: "반복 횟수은(는) 1~20 범위여야 합니다" })).toBeVisible();
    await expect(box.getByRole("button", { name: "전체 측정" })).toBeDisabled();
    await expect(box.getByLabel(/^반복 횟수/)).toHaveValue("99");
    expect(m.starts).toHaveLength(0);
  });

  test("전체 측정은 프리셋·값·모델을 함께 보낸다", async ({ page }) => {
    const m = await mount(page);
    const box = region(page);
    await box.getByRole("button", { name: "프리셋 B 표준" }).click();
    await box.getByLabel("측정 모델").selectOption("gemma3:12b");
    await box.getByRole("button", { name: "전체 측정" }).click();
    await expect.poll(() => m.starts.length).toBe(1);
    const body = JSON.parse(m.starts[0]);
    expect(body).toMatchObject({ mode: "full", preset: "B", model: "gemma3:12b", values: { contextTokens: 8192, repeat: 5, driftRuns: 5 } });
    expect(body.sweep).toBeUndefined();
  });

  test("맥락 스윕은 sweep:true 로 보낸다(Ollama)", async ({ page }) => {
    const m = await mount(page, { connection: "ollama" });
    await region(page).getByRole("button", { name: "맥락 스윕" }).click();
    await expect.poll(() => m.starts.length).toBe(1);
    expect(JSON.parse(m.starts[0]).sweep).toBe(true);
  });

  test("OpenAI 호환 방식은 맥락 입력란과 스윕을 사유와 함께 비활성으로 둔다", async ({ page }) => {
    await mount(page, { connection: "openai" });
    const box = region(page);
    await expect(box.getByLabel(/^맥락 길이/)).toBeDisabled();
    await expect(box.getByText("이 방식은 맥락을 서버에 보낼 수 없음").first()).toBeVisible();
    await expect(box.getByRole("button", { name: "맥락 스윕" })).toBeDisabled();
    await expect(box.getByText("맥락 스윕 비활성:", { exact: false })).toBeVisible();
    // 프리셋 C 를 눌러도 맥락은 바뀌지 않고 수정 항목으로 표시된다
    await box.getByRole("button", { name: "프리셋 C 중간 맥락" }).click();
    await expect(box.getByLabel(/^맥락 길이/)).toHaveValue("8192");
    await expect(box.getByRole("status", { name: "프리셋 상태" })).toHaveText("프리셋 C 기반, 수정됨(맥락 길이: 8192)");
  });

  test("서버가 돌려준 오류 항목을 해당 입력란에 표시한다", async ({ page }) => {
    await mount(page, {
      startError: {
        status: 422,
        body: { detail: { message: "구간 크기 12000자는 상한 777자를 넘습니다. 777자 이하로 바꾸세요", errors: [{ key: "chunkChars", message: "구간 크기 12000자는 상한 777자를 넘습니다. 777자 이하로 바꾸세요" }] } },
      },
    });
    const box = region(page);
    await box.getByLabel(/^구간 크기/).fill("5000");
    await box.getByRole("button", { name: "전체 측정" }).click();
    await expect(box.getByLabel(/^구간 크기/)).toHaveAttribute("aria-invalid", "true");
    await expect(box.getByLabel(/^구간 크기/)).toHaveValue("5000");
    await expect(box.getByRole("alert").filter({ hasText: "상한 777자" }).first()).toBeVisible();
  });

  test("이전 측정을 설정 요약과 핵심 결과로 나란히 보여 준다", async ({ page }) => {
    await mount(page);
    const table = region(page).getByRole("table", { name: "이전 측정 비교" });
    await expect(table.getByText("프리셋 C 적용됨")).toBeVisible();
    await expect(table.getByText("16384")).toBeVisible();
    await expect(table.getByText("41.5")).toBeVisible();
    await expect(table.getByText("95.0%")).toBeVisible();
    await expect(table.getByText("1건")).toBeVisible();
  });
});
