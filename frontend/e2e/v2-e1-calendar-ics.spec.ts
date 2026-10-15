/*
 * 작업 E-1 E2E: 캘린더 뷰의 "캘린더 파일(.ics) 내려받기" 버튼. 백엔드 없이 page.route 로 응답한다(데이터는 모두 가상).
 */
import { expect, test, type Page, type Route } from "./helpers/test";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const MANAGER = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const json = (route: Route, body: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const EMPTY = { total: 0, items: [] };

async function openCalendar(page: Page, icsRequests: string[]) {
  await page.clock.setFixedTime(new Date("2026-10-15T09:00:00"));
  await page.route("**/api/auth/me", (route) => json(route, MANAGER));
  await page.route("**/api/accounts", (route) => json(route, [MANAGER]));
  await page.goto("/v2/login");
  await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
  await page.route("**/api/me/todos", (route) =>
    json(route, { awaitingConfirmMeetings: EMPTY, needsCompletionItems: EMPTY, myItems: EMPTY, unreadAutoConfirmed: EMPTY, pendingChangeRequests: EMPTY }),
  );
  await page.route("**/api/me/notices", (route) => json(route, []));
  await page.route("**/api/me/calendar?**", (route) => {
    const url = new URL(route.request().url());
    return json(route, { from: url.searchParams.get("from"), to: url.searchParams.get("to"), scope: "self", dated: EMPTY, undetermined: EMPTY });
  });
  await page.route("**/api/me/calendar.ics**", (route) => {
    icsRequests.push(new URL(route.request().url()).search);
    return route.fulfill({
      status: 200, contentType: "text/calendar; charset=utf-8", headers: { "Content-Disposition": 'attachment; filename="aidots-tasks.ics"' },
      body: "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n",
    });
  });
  await page.goto("/v2");
  await page.getByRole("button", { name: "캘린더", exact: true }).click();
  await expect(page.getByRole("grid", { name: /마감일 캘린더/ })).toBeVisible();
}

test("캘린더 파일(.ics) 내려받기: 안내 문구가 보이고 버튼을 누르면 파일이 내려받아진다", async ({ page }) => {
  const requests: string[] = [];
  await openCalendar(page, requests);
  await expect(page.getByText("기한이 있는 진행 중 업무만 포함됩니다.")).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "캘린더 파일(.ics) 내려받기" }).click();
  expect((await download).suggestedFilename()).toBe("aidots-tasks.ics");
  expect(requests).toEqual(["?scope=self"]);
});
