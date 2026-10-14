/*
 * 작업 B-1c-1 E2E: 회의록 목록 검색창·정렬, 근거 위치 재계산 팝업·근사 위치 표시. 백엔드 없이 page.route 로 /api/* 를 가로챈다(데이터는 모두 가상).
 */
import { expect, test, type Page, type Route } from "./helpers/test";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const json = (route: Route, status: number, body: unknown) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
const MANAGER = { id: 7, name: "한팀장", rank: "manager", tenantId: 1, isSystemAdmin: false };

async function login(page: Page, target: string) {
  await page.route("**/api/auth/me", (route) => json(route, 200, MANAGER));
  await page.goto("/v2/login");
  await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto(target);
}

const listItem = (id: number, title: string) => ({
  id, title, heldAt: "2026-10-01T01:00:00Z", registeredAt: "2026-10-01T02:00:00Z", registeredBy: { id: 3, name: "김대리" }, origin: "audio",
  status: "awaiting_confirmation", confirmKind: null, itemCount: 2, needsCompletionCount: 0, autoConfirmAt: null, phase: "active",
});

test.describe("회의록 목록 검색·정렬", () => {
  test("검색어는 디바운스 뒤 서버 질의(q)로 보내고, 결과가 없으면 안내를 보인다", async ({ page }) => {
    const queries: string[] = [];
    await page.route(/\/api\/meetings\?.*/, (route) => {
      const q = new URL(route.request().url()).searchParams.get("q") ?? "";
      queries.push(q);
      const items = q === "" ? [listItem(1, "주간 회의")] : q === "예산" ? [listItem(2, "예산 검토")] : [];
      return json(route, 200, { items, total: items.length, page: 1, size: 20, availablePhases: ["active", "ended"] });
    });
    await login(page, "/v2/meetings");
    await expect(page.getByRole("link", { name: "주간 회의" })).toBeVisible();

    const search = page.getByRole("searchbox", { name: "회의록 검색" });
    await search.fill("예");
    await search.fill("예산");
    await expect(page.getByRole("link", { name: "예산 검토" })).toBeVisible();
    expect(queries.filter((q) => q === "예")).toHaveLength(0); // 입력 중간값은 보내지 않는다(디바운스)
    expect(queries).toContain("예산");

    await search.fill("없는검색어");
    await expect(page.getByText("검색 결과가 없습니다")).toBeVisible();
    await expect(page.getByRole("link", { name: "첫 회의 올리기" })).toHaveCount(0);
  });

  test("정렬을 등록 일시 최신순으로 바꾸면 sort=registered 로 조회한다", async ({ page }) => {
    const sorts: (string | null)[] = [];
    await page.route(/\/api\/meetings\?.*/, (route) => {
      sorts.push(new URL(route.request().url()).searchParams.get("sort"));
      return json(route, 200, { items: [listItem(1, "주간 회의")], total: 1, page: 1, size: 20, availablePhases: ["active"] });
    });
    await login(page, "/v2/meetings");
    await expect(page.getByRole("link", { name: "주간 회의" })).toBeVisible();
    expect(sorts[0]).toBeNull(); // 기본은 sort 를 보내지 않는다(회의 일시 최신순)
    await page.getByLabel("정렬").selectOption("registered");
    await expect.poll(() => sorts.includes("registered")).toBe(true);
  });
});

test.describe("근거 위치 재계산", () => {
  const item = (over: Record<string, unknown>) => ({
    id: 1, title: "예산안 제출", assignee: { id: 3, name: "김대리" }, dueDate: "2026-10-10", dueUndetermined: false, status: "pending",
    confirmKind: null, evidenceStartSec: 150, evidenceQuote: null, needsCompletion: false, missingFields: [], origin: "ai", allowedActions: [], ...over,
  });
  const detail = (over: Record<string, unknown>) => ({
    id: 41, title: "주간 회의", heldAt: "2026-10-01T01:00:00Z", summary: "", decisions: [], status: "awaiting_confirmation",
    confirmKind: null, confirmedBy: null, confirmedAt: null, firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: null,
    registeredBy: { id: 3, name: "김대리" }, origin: "audio", participants: [], actionItems: [item({ evidenceApprox: true })], recentEvents: [],
    allowedActions: ["edit_held_at"], ...over,
  });

  async function open(page: Page, meeting: Record<string, unknown>, calls: unknown[] = []) {
    await page.route(/\/api\/meetings\/41\/recompute-evidence$/, (route) => {
      const body = route.request().postDataJSON() as { dryRun?: boolean; reason?: string };
      calls.push(body);
      return json(
        route, 200,
        body.dryRun
          ? { targets: 4, changed: 1, approx: 1, unchanged: 2, excluded: 1, applied: false }
          : { targets: 4, changed: 1, approx: 1, unchanged: 2, excluded: 1, applied: true },
      );
    });
    await page.route(/\/api\/meetings\/41$/, (route) => json(route, 200, meeting));
    await login(page, "/v2/meetings/41");
    await expect(page.getByRole("heading", { level: 1, name: "주간 회의" })).toBeVisible();
  }

  test("관리자 이상(허용 동작 있음)에게만 버튼이 보이고, 근사 위치 표시가 나온다", async ({ page }) => {
    await open(page, detail({}));
    await expect(page.getByRole("button", { name: "근거 위치 재계산" })).toBeVisible();
    await expect(page.getByTestId("evidence-approx").first()).toContainText("근사 위치");
  });

  test("허용 동작이 없으면 버튼이 없다", async ({ page }) => {
    await open(page, detail({ allowedActions: [], actionItems: [item({})] }));
    await expect(page.getByRole("button", { name: "근거 위치 재계산" })).toHaveCount(0);
    await expect(page.getByTestId("evidence-approx")).toHaveCount(0);
  });

  test("확인 창에 대상 건수·확정 제외 건수를 보이고 사유 없이는 보내지 않으며, 결과 건수를 알린다", async ({ page }) => {
    const calls: { dryRun?: boolean; reason?: string }[] = [];
    await open(page, detail({}), calls);
    await page.getByRole("button", { name: "근거 위치 재계산" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByTestId("recompute-preview")).toContainText("재계산 대상 4건 · 확정 제외 1건");
    await dialog.getByRole("button", { name: "근거 위치 재계산" }).click();
    await expect(dialog.getByRole("alert")).toContainText("사유");
    expect(calls.filter((c) => c.dryRun !== true)).toHaveLength(0); // 사유가 없으면 실행 요청을 보내지 않는다
    await dialog.getByLabel("재계산 사유(필수)").fill("근거 위치 오류 보정");
    await dialog.getByRole("button", { name: "근거 위치 재계산" }).click();
    await expect(page.getByText("변경 1건 · 근사 1건 · 미변경 2건 · 확정 제외 1건")).toBeVisible();
    expect(calls.at(-1)).toMatchObject({ dryRun: false, reason: "근거 위치 오류 보정" });
  });
});
