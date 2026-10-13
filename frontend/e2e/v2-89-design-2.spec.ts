/*
 * 디자인 2단계 E2E: 메뉴 기본 위치(새 사용자 왼쪽, 저장된 설정 유지), 할 일 "내 업무" 표의 담당자 열 제거,
 * 사람 이름 아바타·행 높이·상태 칩 통합. 백엔드 없이 page.route 로 응답한다(데이터는 모두 가상).
 */
import { expect, test, type Page, type Route } from "./helpers/test";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const ME = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const EMPTY = { total: 0, items: [] };
const TODOS = {
  awaitingConfirmMeetings: EMPTY,
  needsCompletionItems: EMPTY,
  myItems: {
    total: 1,
    items: [{ meetingId: 11, itemId: 31, title: "고객사에 납기 조정 안내", dueDate: "2026-10-02", dueUndetermined: false, status: "pending" }],
  },
  unreadAutoConfirmed: EMPTY,
  pendingChangeRequests: EMPTY,
};

async function open(page: Page, path: string, cookie?: "left" | "right") {
  await page.route("**/api/**", (route) => json(route, []));
  await page.route("**/api/auth/me", (route) => json(route, ME));
  await page.route("**/api/me/todos", (route) => json(route, TODOS));
  await page.route("**/api/me/notices", (route) => json(route, []));
  await page.route("**/api/me/processing", (route) => json(route, []));
  await page.route("**/api/projects", (route) =>
    json(route, [
      {
        id: 1, name: "3호기 설비 개선", description: "", status: "pending_approval", departmentId: 1, departmentName: "개발팀", registeredBy: { id: 7, name: "한팀장" },
        lead: { id: 7, name: "한팀장" }, approver: null, myRole: "lead", memberCount: 2, createdAt: "2026-10-01T01:00:00Z", decidedAt: null, members: [],
      },
    ]),
  );
  if (cookie) await page.context().addCookies([{ name: "mi_side_position", value: cookie, url: `http://localhost:${process.env.E2E_PORT ?? 3100}` }]);
  await page.goto("/v2/login");
  await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto(path);
}

test.describe("메뉴 기본 위치", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("저장된 설정이 없는 새 사용자는 메뉴가 왼쪽이다", async ({ page }) => {
    await open(page, "/v2");
    await expect(page.getByRole("navigation", { name: "주 메뉴" })).toBeVisible();
    expect(((await page.locator("aside").first().boundingBox())!).x).toBeLessThan(10);
  });

  test("이미 오른쪽으로 저장한 사용자의 설정은 그대로 유지된다", async ({ page }) => {
    await open(page, "/v2", "right");
    await expect(page.getByRole("navigation", { name: "주 메뉴" })).toBeVisible();
    expect(((await page.locator("aside").first().boundingBox())!).x).toBeGreaterThan(640);
  });
});

test.describe("할 일 내 업무 표", () => {
  test("담당자 열이 없고 업무명·완료 기한·상태만 있다", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await open(page, "/v2");
    const table = page.getByRole("table", { name: "내 업무" });
    await expect(table).toBeVisible();
    await expect(table.getByRole("columnheader")).toHaveText(["업무명", "완료 기한", "상태"]);
    await expect(table.getByRole("columnheader", { name: "담당자" })).toHaveCount(0);
    await expect(table.locator("tbody tr").first().locator("td")).toHaveCount(3);
  });
});

test.describe("프로젝트 목록 칩·행 높이", () => {
  test("상태는 공용 칩(승인 대기 색)이고 행 높이는 64px 이상, 총괄은 아바타와 함께 이름이 보인다", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await open(page, "/v2/projects");
    const row = page.getByRole("row").filter({ hasText: "3호기 설비 개선" });
    await expect(row).toBeVisible();
    await expect(row.locator(".mn-chip-warning")).toHaveText("승인 대기");
    expect((await row.boundingBox())!.height).toBeGreaterThanOrEqual(64);
    // 아바타 글자는 가상 요소라 셀 텍스트에 중복되지 않는다
    await expect(row.locator('[data-label="총괄"]')).toHaveText("한팀장");
  });
});
