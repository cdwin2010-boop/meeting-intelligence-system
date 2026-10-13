/*
 * 프로젝트 반려 되돌리기 E2E(작업 A-1 e). 백엔드 없이 page.route 로 응답한다(데이터는 모두 가상).
 * 버튼은 서버가 준 허용 동작 revert_rejection 으로만 보이고, 사유가 필수이며, 되돌리면 승인 대기 상태로 바뀐다.
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withProjectActions } from "./helpers/project-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const HEAD = { id: 7, name: "박개발장", rank: "manager", tenantId: 1 };
const KIM = { id: 8, name: "김담당", rank: "staff", tenantId: 1 };
const base = (status: string) => ({
  id: 5, name: "설비 개선 과제", description: "", status, departmentId: 1, departmentName: "개발팀", registeredBy: { id: 8, name: "김담당" },
  lead: null, approver: { id: 7, name: "박개발장" }, myRole: null, memberCount: 1, createdAt: "2026-10-01T01:00:00Z", decidedAt: null,
  members: [{ accountId: 8, name: "김담당", rank: "staff", role: "member" }],
});

async function open(page: Page, me: typeof HEAD, state: { status: string; reverts: unknown[]; failWith?: { status: number; detail: string } }) {
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, me) : json(route, { detail: "x" }, 401),
  );
  await page.route(/\/api\/projects\/5$/, (route) => json(route, withProjectActions(base(state.status), me, 7)));
  await page.route(/\/api\/projects\/5\/revert-rejection$/, (route) => {
    state.reverts.push(route.request().postDataJSON());
    if (state.failWith) return json(route, { detail: state.failWith.detail }, state.failWith.status);
    state.status = "pending_approval";
    return json(route, withProjectActions(base("pending_approval"), me, 7));
  });
  await page.goto("/v2/login");
  await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto("/v2/projects/5");
  await expect(page.getByRole("heading", { level: 1, name: "설비 개선 과제" })).toBeVisible();
}

test.describe("반려 되돌리기", () => {
  test("부서장: 반려된 프로젝트에 버튼이 보이고, 사유 없이는 확인할 수 없으며, 되돌리면 승인 대기로 바뀐다", async ({ page }) => {
    const state = { status: "rejected", reverts: [] as unknown[] };
    await open(page, HEAD, state);
    await expect(page.getByText("반려", { exact: true }).first()).toBeVisible();
    await page.getByRole("button", { name: "반려 되돌리기" }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("승인 대기로 돌아갑니다");
    const confirm = dialog.getByRole("button", { name: "반려 되돌리기" });
    await expect(confirm).toBeDisabled(); // 사유 필수
    await dialog.getByLabel(/사유/).fill("예산이 확보되어 다시 검토합니다");
    await confirm.click();
    await expect(dialog).toBeHidden();
    expect(state.reverts).toEqual([{ reason: "예산이 확보되어 다시 검토합니다" }]);
    await expect(page.getByText("승인 대기", { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "반려 되돌리기" })).toHaveCount(0);
  });

  test("서버 거절 문구를 팝업 안에 그대로 보여 준다", async ({ page }) => {
    const state = { status: "rejected", reverts: [] as unknown[], failWith: { status: 409, detail: "반려된 프로젝트만 되돌릴 수 있습니다" } };
    await open(page, HEAD, state);
    await page.getByRole("button", { name: "반려 되돌리기" }).click();
    const dialog = page.getByRole("alertdialog");
    await dialog.getByLabel(/사유/).fill("사유");
    await dialog.getByRole("button", { name: "반려 되돌리기" }).click();
    await expect(dialog).toContainText("반려된 프로젝트만 되돌릴 수 있습니다");
  });

  test("승인자가 아니면 버튼이 없다(허용 동작 기준)", async ({ page }) => {
    await open(page, KIM as never, { status: "rejected", reverts: [] });
    await expect(page.getByRole("button", { name: "반려 되돌리기" })).toHaveCount(0);
  });

  test("승인 대기·진행 중 프로젝트에는 버튼이 없다", async ({ page }) => {
    await open(page, HEAD, { status: "pending_approval", reverts: [] });
    await expect(page.getByRole("button", { name: "반려 되돌리기" })).toHaveCount(0);
  });
});
