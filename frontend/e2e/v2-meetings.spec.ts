/*
 * v2 회의록 목록 E2E. 실제 백엔드 없이 page.route 로 v2 API(/api/auth/me, /api/meetings)를 가로채 가짜로 응답한다.
 * 데이터는 모두 가상이고, 가짜 토큰은 고정 문자열이다.
 */
import { expect, test, type Page, type Route } from "@playwright/test";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const ACCOUNT = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const meeting = (id: number, title: string, heldAt: string, status: string, confirmKind: string | null, itemCount: number) => ({
  id,
  title,
  heldAt,
  registeredBy: { id: 3, name: "김대리" },
  origin: "audio",
  status,
  confirmKind,
  itemCount,
  needsCompletionCount: 0,
  autoConfirmAt: null,
});

const PAGE = {
  items: [
    meeting(41, "주간 생산 현안 회의", "2026-10-01T01:00:00Z", "awaiting_confirmation", null, 4),
    meeting(42, "3호기 납기 조정 회의", "2026-09-30T00:30:00Z", "confirmed", "period_elapsed", 3),
    meeting(43, "입고 검사 기준 개정", "2026-09-29T05:00:00Z", "confirmed", "manager", 6),
    meeting(44, "설비 점검 주간 회의", "2026-09-28T04:20:00Z", "processing", null, 0),
    meeting(45, "고객 클레임 대응", "2026-09-27T02:00:00Z", "failed", null, 0),
  ],
  total: 5,
  page: 1,
  size: 20,
};

const EMPTY_PAGE = { items: [], total: 0, page: 1, size: 20 };

/** me 는 가짜 토큰일 때만 성공, 목록은 handler 로 테스트별 응답 */
async function openMeetings(page: Page, handler: (route: Route) => Promise<void> | void) {
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}`
      ? json(route, 200, ACCOUNT)
      : json(route, 401, { detail: "인증이 필요합니다" }),
  );
  await page.route("**/api/meetings?*", handler);
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto("/v2/meetings");
}

test.describe("v2 회의록 목록", () => {
  test("목록 표시: 일시·회의명·상태 라벨·업무 건수", async ({ page }) => {
    let requested = "";
    await openMeetings(page, (route) => {
      requested = route.request().url();
      return json(route, 200, PAGE);
    });

    await expect(page.getByRole("navigation", { name: "주 메뉴" }).getByRole("link", { name: "회의록" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    const rows = page.getByRole("table", { name: "회의록 목록" }).locator("tbody tr");
    await expect(rows).toHaveCount(5);
    // 시각은 브라우저 현지 시각(설정: Asia/Seoul)으로
    await expect(rows.nth(0)).toContainText("2026-10-01 10:00");
    await expect(rows.nth(0)).toContainText("주간 생산 현안 회의");
    await expect(rows.nth(0)).toContainText("확정 대기");
    await expect(rows.nth(0).locator("td").nth(3)).toHaveText("4");
    await expect(rows.nth(1)).toContainText("자동 확정됨");
    await expect(rows.nth(2)).toContainText("확정 완료");
    await expect(rows.nth(3)).toContainText("전사·추출 중");
    await expect(rows.nth(3).locator("td").nth(3)).toHaveText("—");
    await expect(rows.nth(4)).toContainText("처리 실패");
    await expect(page.getByText("1–5 / 5")).toBeVisible();
    await expect(page.getByRole("button", { name: "다음" })).toBeDisabled();
    expect(requested).toContain("page=1");
  });

  test("빈 목록 → 등록된 회의록 없음 안내", async ({ page }) => {
    await openMeetings(page, (route) => json(route, 200, EMPTY_PAGE));
    await expect(page.getByRole("status").filter({ hasText: "등록된 회의록이 없습니다." })).toBeVisible();
    await expect(page.getByRole("table", { name: "회의록 목록" })).toHaveCount(0);
  });

  test("오류 → 오류 문구와 다시 시도", async ({ page }) => {
    let fail = true;
    await openMeetings(page, (route) => (fail ? json(route, 500, { detail: "서버 오류" }) : json(route, 200, PAGE)));
    const alert = page.getByRole("alert").filter({ hasText: "회의록을 불러오지 못했습니다" });
    await expect(alert).toBeVisible();
    await expect(alert).toContainText("서버 오류");
    fail = false;
    await alert.getByRole("button", { name: "다시 시도" }).click();
    await expect(page.getByRole("table", { name: "회의록 목록" }).locator("tbody tr")).toHaveCount(5);
  });

  test("행 클릭 → /v2/meetings/{id} 로 이동", async ({ page }) => {
    await openMeetings(page, (route) => json(route, 200, PAGE));
    // 상세 화면이 부르는 API 는 최소 응답으로
    await page.route("**/api/meetings/42", (route) =>
      json(route, 200, {
        ...PAGE.items[1], summary: "", decisions: [], confirmedBy: null, confirmedAt: null,
        firstCreatedAt: "2026-09-30T00:30:00Z", participants: [], actionItems: [], recentEvents: [],
      }),
    );
    await page.route("**/api/meetings/42/speakers", (route) => json(route, 200, { labels: [], speakers: [], autoAssignedItemIds: [] }));
    await page.route("**/api/accounts", (route) => json(route, 200, []));
    const row = page.getByRole("table", { name: "회의록 목록" }).locator("tbody tr").nth(1);
    await row.locator("td").first().click(); // 링크가 아닌 칸을 눌러도 이동
    await expect(page).toHaveURL(/\/v2\/meetings\/42$/);
    await expect(page.getByRole("heading", { level: 1, name: "3호기 납기 조정 회의" })).toBeVisible();
  });
});
