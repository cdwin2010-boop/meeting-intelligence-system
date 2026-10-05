/*
 * v2 회의록 상세 · 재개 뒤 업무 기한 재입력 E2E.
 * 실제 백엔드 없이 page.route 로 v2 API 를 가로챈다(PATCH 에 따라 바뀌는 가짜 상태). 데이터는 모두 가상이다.
 */
import { expect, test, type Page, type Route } from "@playwright/test";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const MANAGER = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

type Item = {
  id: number; title: string; assignee: { id: number; name: string }; dueDate: string | null; dueUndetermined: boolean; status: string;
  confirmKind: string | null; evidenceStartSec: null; evidenceQuote: null; needsCompletion: boolean; missingFields: string[];
};

/** 기한이 비워진(재개 직후) 업무. 날짜도 '미확정'도 아니면 보완 필요 */
const emptyDue = (id: number, title: string): Item => ({
  id, title, assignee: { id: 9, name: "이서연" }, dueDate: null, dueUndetermined: false, status: "confirmed",
  confirmKind: "manager", evidenceStartSec: null, evidenceQuote: null, needsCompletion: true, missingFields: ["dueDate"],
});

const detailOf = (actionItems: Item[], phase = "active") => ({
  id: 41, title: "주간 생산 현안 회의", heldAt: "2026-10-01T01:00:00Z", summary: "", decisions: [],
  status: "confirmed", confirmKind: "manager", confirmedBy: null, confirmedAt: null,
  firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: null, registeredBy: { id: 7, name: "한팀장" },
  origin: "audio_minutes", participants: [], recentEvents: [], phase,
  onHold: phase === "on_hold", onHoldBy: phase === "on_hold" ? MANAGER : null, onHoldAt: phase === "on_hold" ? "2026-10-02T01:00:00Z" : null,
  resumedAt: phase === "active" ? "2026-10-02T02:00:00Z" : null, resumedBy: phase === "active" ? MANAGER : null,
  actionItems,
});

async function open(page: Page, detail: ReturnType<typeof detailOf>, fail: [number, string] | null = null) {
  const bodies: unknown[] = [];
  let failure = fail;
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, MANAGER) : json(route, 401, { detail: "인증 필요" }),
  );
  await page.route(/\/api\/meetings\/41$/, (route) => json(route, 200, detail));
  await page.route(/\/api\/meetings\/41\/transcript$/, (route) => json(route, 404, { detail: "전사문이 없습니다" }));
  await page.route(/\/api\/meetings\/41\/speakers$/, (route) => json(route, 200, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route(/\/api\/meetings\/41\/change-requests$/, (route) => json(route, 200, []));
  await page.route("**/api/accounts", (route) => json(route, 200, []));
  await page.route(/\/api\/meetings\/41\/resume$/, (route) => {
    Object.assign(detail, { phase: "active", onHold: false, resumedAt: "2026-10-02T02:00:00Z", resumedBy: MANAGER });
    detail.actionItems = detail.actionItems.map((it) => ({ ...it, dueDate: null, dueUndetermined: false, needsCompletion: true, missingFields: ["dueDate"] }));
    return json(route, 200, { id: 41, phase: "active" });
  });
  await page.route(/\/api\/action-items\/\d+$/, (route) => {
    if (route.request().method() !== "PATCH") return route.fallback();
    const body = JSON.parse(route.request().postData() ?? "{}");
    bodies.push(body);
    if (failure) {
      const [status, message] = failure;
      failure = null;
      return json(route, status, { detail: message });
    }
    const id = Number(/\/action-items\/(\d+)$/.exec(route.request().url())![1]);
    const target = detail.actionItems.find((it) => it.id === id)!;
    if (body.dueDate) Object.assign(target, { dueDate: body.dueDate, dueUndetermined: false });
    if (body.dueUndetermined) Object.assign(target, { dueDate: null, dueUndetermined: true });
    Object.assign(target, { needsCompletion: false, missingFields: [] });
    return json(route, 200, target);
  });
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto("/v2/meetings/41");
  await expect(page.getByRole("heading", { level: 1, name: "주간 생산 현안 회의" })).toBeVisible();
  return bodies;
}

const row = (page: Page, title: string) => page.getByRole("table", { name: "업무 원장" }).locator("tbody tr").filter({ hasText: title });
const notice = (page: Page, n: number) => page.getByText(`기한을 다시 설정해야 하는 업무 ${n}건`);

test.describe("v2 재개 뒤 기한 재입력", () => {
  test("재개 직후 안내 → 날짜 입력·미확정 선택 → 모두 입력하면 안내 사라짐", async ({ page }) => {
    const detail = detailOf([emptyDue(101, "견적서 송부"), emptyDue(102, "설비 점검")], "on_hold");
    detail.actionItems = detail.actionItems.map((it) => ({ ...it, dueDate: "2026-10-09", needsCompletion: false, missingFields: [] }));
    const bodies = await open(page, detail);
    await expect(page.getByText(/기한을 다시 설정해야 하는 업무/)).toHaveCount(0);
    await page.getByRole("button", { name: "재개", exact: true }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "재개하기" }).click();
    await expect(notice(page, 2)).toBeVisible();
    await expect(page.getByText("보완 필요 2건")).toBeVisible();

    await page.getByRole("button", { name: "견적서 송부 기한 설정" }).click();
    await page.getByLabel("견적서 송부 완료 기한").fill("2026-11-05");
    await page.getByRole("button", { name: "견적서 송부 기한 저장" }).click();
    await expect(row(page, "견적서 송부")).toContainText("2026-11-05");
    await expect(notice(page, 1)).toBeVisible();

    await page.getByRole("button", { name: "설비 점검 기한 설정" }).click();
    await page.getByRole("button", { name: "설비 점검 기한 미확정" }).click();
    await expect(row(page, "설비 점검").getByText("미확정", { exact: true })).toBeVisible();
    await expect(page.getByText(/기한을 다시 설정해야 하는 업무/)).toHaveCount(0);
    await expect(page.getByText(/보완 필요 \d+건/)).toHaveCount(0);
    expect(bodies).toEqual([{ dueDate: "2026-11-05" }, { dueUndetermined: true }]);
  });

  test("빈 값이면 요청 없이 안내하고 보완 필요 경고 유지, 취소하면 그대로", async ({ page }) => {
    const bodies = await open(page, detailOf([emptyDue(101, "견적서 송부")]));
    await page.getByRole("button", { name: "견적서 송부 기한 설정" }).click();
    await page.getByRole("button", { name: "견적서 송부 기한 저장" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "날짜를 선택하거나 '미확정'을 누르세요" })).toBeVisible();
    await expect(notice(page, 1)).toBeVisible();
    await expect(row(page, "견적서 송부")).toContainText("기한 필요");
    await page.getByRole("button", { name: "견적서 송부 기한 입력 취소" }).click();
    await expect(page.getByLabel("견적서 송부 완료 기한")).toHaveCount(0);
    await expect(row(page, "견적서 송부")).toContainText("기한 필요");
    expect(bodies).toEqual([]);
  });

  test("서버 거절 문구를 그대로 표시하고 입력을 유지, 다시 저장하면 성공", async ({ page }) => {
    const msg = "업무를 보완할 권한이 없습니다";
    const bodies = await open(page, detailOf([emptyDue(101, "견적서 송부")]), [403, msg]);
    await page.getByRole("button", { name: "견적서 송부 기한 설정" }).click();
    await page.getByLabel("견적서 송부 완료 기한").fill("2026-11-05");
    await page.getByRole("button", { name: "견적서 송부 기한 저장" }).click();
    await expect(page.getByRole("alert").filter({ hasText: msg })).toBeVisible();
    await expect(page.getByLabel("견적서 송부 완료 기한")).toHaveValue("2026-11-05");
    await expect(notice(page, 1)).toBeVisible();
    await page.getByRole("button", { name: "견적서 송부 기한 저장" }).click();
    await expect(row(page, "견적서 송부")).toContainText("2026-11-05");
    await expect(page.getByText(/기한을 다시 설정해야 하는 업무/)).toHaveCount(0);
    expect(bodies).toHaveLength(2);
  });

  test("재개하지 않은 회의록은 기한이 비어도 재개 안내를 띄우지 않는다", async ({ page }) => {
    const detail = detailOf([emptyDue(101, "견적서 송부")]);
    Object.assign(detail, { resumedAt: null, resumedBy: null });
    await open(page, detail);
    await expect(page.getByText(/기한을 다시 설정해야 하는 업무/)).toHaveCount(0);
    await expect(row(page, "견적서 송부")).toContainText("기한 필요");
  });
});
