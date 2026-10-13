/*
 * 할 일 화면 스크린샷·가로 넘침 검사(작업 87-1). 375/768/1280px × 라이트/다크.
 * 스크린샷은 test-results/design-87-1/ 에 저장한다(.gitignore 대상, 커밋하지 않음). 데이터는 모두 가상이다.
 */
import { expect, test, type Route } from "./helpers/test";

const FAKE_TOKEN = "e2e-fake-token";
const json = (route: Route, body: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

const TODOS = {
  awaitingConfirmMeetings: { total: 1, items: [{ id: 11, title: "주간 생산 현안 회의", autoConfirmAt: "2026-10-06T01:00:00Z" }] },
  needsCompletionItems: { total: 1, items: [{ meetingId: 11, itemId: 21, title: "설비 점검 일정 수립", missingFields: ["assignee", "dueDate"] }] },
  myItems: {
    total: 3,
    items: [
      { meetingId: 11, itemId: 31, title: "고객사에 납기 조정 안내", dueDate: "2026-10-02", dueUndetermined: false, status: "pending" },
      { meetingId: 12, itemId: 32, title: "월간 불량 현황 보고", dueDate: "2026-10-07", dueUndetermined: false, status: "confirmed" },
      { meetingId: 13, itemId: 33, title: "협력사 단가 재협의", dueDate: null, dueUndetermined: true, status: "confirmed" },
    ],
  },
  unreadAutoConfirmed: {
    total: 1,
    items: [{ entityType: "meeting", meetingId: 13, itemId: null, title: "3호기 납기 조정 회의", confirmKind: "period_elapsed", confirmedAt: "2026-09-30T03:00:00Z" }],
  },
  pendingChangeRequests: {
    total: 1,
    items: [{ requestId: 5, kind: "change", meetingId: 12, meetingTitle: "월간 생산 계획", itemId: null, itemTitle: "", commentPreview: "결정 사항 수정 요청", supersedesItemId: null, requester: { id: 9, name: "김대리" }, createdAt: "2026-10-01T02:00:00Z" }],
  },
};

for (const theme of ["light", "dark"] as const) {
  for (const width of [375, 768, 1280]) {
    test(`할 일 화면 ${theme} ${width}px: 가로 넘침 없음, 스크린샷 저장`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.route("**/api/auth/me", (route) => json(route, { id: 7, name: "한팀장", rank: "manager", tenantId: 1 }));
      await page.route("**/api/me/todos", (route) => json(route, TODOS));
      await page.route("**/api/me/notices", (route) => json(route, []));
      await page.addInitScript(([themeKey, tokenKey, t, token]) => {
        window.localStorage.setItem(themeKey, t);
        window.sessionStorage.setItem(tokenKey, token);
      }, ["mi.v2.theme", "mi.v2.accessToken", theme, FAKE_TOKEN]);
      await page.goto("/v2");
      await expect(page.getByRole("table", { name: "내 업무" })).toBeVisible();
      await expect(page.locator("html")).toHaveAttribute("data-v2-theme", theme);
      // 글꼴 로딩이 끝난 뒤 측정·촬영한다
      await page.evaluate(() => document.fonts.ready);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.screenshot({ path: `test-results/design-87-1/todos-${theme}-${width}.png`, fullPage: true });
    });
  }
}
