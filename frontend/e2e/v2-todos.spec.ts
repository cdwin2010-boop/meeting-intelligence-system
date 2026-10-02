/*
 * v2 앱 틀 + 할 일 화면 E2E. 실제 백엔드 없이 page.route 로 v2 API(/api/auth/*, /api/me/*)를 가로채 가짜로 응답한다.
 * 데이터는 모두 가상이고, 가짜 토큰은 고정 문자열이다.
 */
import { expect, test, type Page, type Route } from "@playwright/test";

const FAKE_TOKEN = "e2e-fake-token";
const ACCOUNT = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const EMPTY_LIST = { total: 0, items: [] };
const EMPTY_TODOS = {
  awaitingConfirmMeetings: EMPTY_LIST,
  needsCompletionItems: EMPTY_LIST,
  myItems: EMPTY_LIST,
  unreadAutoConfirmed: EMPTY_LIST,
};

const FULL_TODOS = {
  awaitingConfirmMeetings: { total: 1, items: [{ id: 11, title: "주간 생산 현안 회의", autoConfirmAt: "2026-10-06T01:00:00Z" }] },
  needsCompletionItems: {
    total: 1,
    items: [{ meetingId: 11, itemId: 21, title: "설비 점검 일정 수립", missingFields: ["assignee", "dueDate"] }],
  },
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
    items: [
      {
        entityType: "meeting",
        meetingId: 13,
        itemId: null,
        title: "3호기 납기 조정 회의",
        confirmKind: "period_elapsed",
        confirmedAt: "2026-09-30T03:00:00Z",
      },
    ],
  },
};

const NOTICES = [
  {
    id: 1,
    kind: "confirmed_notice",
    entityType: "meeting",
    entityId: 12,
    meetingId: 12,
    payload: { confirmKind: "manager", title: "월간 생산 계획" },
    createdAt: "2026-10-01T02:00:00Z",
  },
];

interface MockOptions {
  todos?: unknown;
  notices?: unknown;
}

/** 로그인·me 는 항상 성공, todos·notices 는 테스트별로 응답을 바꾼다 */
async function mockApi(page: Page, { todos = EMPTY_TODOS, notices = [] }: MockOptions = {}) {
  await page.route("**/api/auth/login", (route) => json(route, 200, { accessToken: FAKE_TOKEN, tokenType: "bearer" }));
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}`
      ? json(route, 200, ACCOUNT)
      : json(route, 401, { detail: "인증이 필요합니다" }),
  );
  await page.route("**/api/me/todos", (route) => json(route, 200, todos));
  await page.route("**/api/me/notices", (route) => json(route, 200, notices));
}

async function loginAndOpenHome(page: Page) {
  await page.goto("/v2/login");
  await page.getByLabel("아이디").fill("han");
  await page.getByLabel("비밀번호").fill("e2e-password");
  await page.getByLabel("비밀번호").press("Enter");
  await expect(page).toHaveURL(/\/v2$/);
}

test.describe("v2 앱 틀 + 할 일", () => {
  test("로그인 → 앱 틀(메뉴·사용자) → 할 일 목록 표시", async ({ page }) => {
    await mockApi(page, { todos: FULL_TODOS, notices: NOTICES });
    await loginAndOpenHome(page);

    // 앱 틀: 주 메뉴·올리기·로그인 사용자·로그아웃
    const nav = page.getByRole("navigation", { name: "주 메뉴" });
    await expect(nav.getByRole("link", { name: "할 일" })).toHaveAttribute("aria-current", "page");
    await expect(nav.getByRole("link", { name: "회의록" })).toBeVisible();
    await expect(page.getByRole("link", { name: "회의록 올리기" })).toBeVisible();
    await expect(page.getByRole("group", { name: "로그인 사용자" })).toContainText("한팀장");
    await expect(page.getByRole("group", { name: "로그인 사용자" })).toContainText("중간관리자");
    await expect(page.getByRole("button", { name: "로그아웃" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 1, name: "할 일" })).toBeVisible();

    // 내 업무 표: 업무명·기한·상태(글자 라벨)
    const table = page.getByRole("table", { name: "내 업무" });
    const rows = table.locator("tbody tr");
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0)).toContainText("고객사에 납기 조정 안내");
    await expect(rows.nth(0)).toContainText("2026-10-02");
    await expect(rows.nth(0)).toContainText("확정 대기");
    await expect(rows.nth(1)).toContainText("확정됨");
    await expect(rows.nth(2)).toContainText("미확정");

    // 확정 안내·자동 확정됨(미열람)·확정 대기·보완 필요
    const notice = page.getByRole("region", { name: "확정 안내" });
    await expect(notice).toContainText("월간 생산 계획");
    await expect(notice).toContainText("관리자 확정");
    const unread = page.getByRole("region", { name: "자동 확정됨 (미열람)" });
    await expect(unread).toContainText("3호기 납기 조정 회의");
    await expect(unread).toContainText("기간 경과로 자동 확정");
    await expect(page.getByRole("region", { name: "확정 대기 회의록" })).toContainText("주간 생산 현안 회의");
    const needs = page.getByRole("region", { name: "보완 필요 업무" });
    await expect(needs).toContainText("담당자 필요");
    await expect(needs).toContainText("기한 필요");

    // 빈 자리 메뉴는 이동만 된다
    await nav.getByRole("link", { name: "회의록" }).click();
    await expect(page).toHaveURL(/\/v2\/meetings$/);
    await expect(page.getByRole("heading", { level: 1, name: "회의록" })).toBeVisible();
    await page.getByRole("link", { name: "회의록 올리기" }).click();
    await expect(page).toHaveURL(/\/v2\/upload$/);
    await expect(page.getByRole("heading", { level: 1, name: "회의록 올리기" })).toBeVisible();
  });

  test("빈 목록 → 처리할 일 없음 안내", async ({ page }) => {
    await mockApi(page);
    await loginAndOpenHome(page);
    await expect(page.getByRole("status").filter({ hasText: "지금 처리할 일이 없습니다." })).toBeVisible();
    await expect(page.getByRole("table", { name: "내 업무" })).toHaveCount(0);
  });

  test("오류 → 오류 문구와 다시 시도", async ({ page }) => {
    let fail = true;
    await mockApi(page);
    // 첫 조회는 500, 다시 시도하면 빈 목록
    await page.route("**/api/me/todos", (route) =>
      fail ? json(route, 500, { detail: "서버 오류" }) : json(route, 200, EMPTY_TODOS),
    );
    await loginAndOpenHome(page);
    const alert = page.getByRole("alert").filter({ hasText: "할 일을 불러오지 못했습니다" });
    await expect(alert).toBeVisible();
    await expect(alert).toContainText("서버 오류");
    fail = false;
    await alert.getByRole("button", { name: "다시 시도" }).click();
    await expect(page.getByText("지금 처리할 일이 없습니다.")).toBeVisible();
  });

  test("토큰 없이 /v2/meetings → 로그인 화면(next 유지)", async ({ page }) => {
    await mockApi(page);
    await page.goto("/v2/meetings");
    await expect(page).toHaveURL(/\/v2\/login\?next=%2Fv2%2Fmeetings$/);
  });

  test("수정 요청 대기 묶음 표시 → 항목 누르면 회의록 상세로 이동", async ({ page }) => {
    const todos = {
      ...EMPTY_TODOS,
      pendingChangeRequests: {
        total: 2,
        items: [
          { requestId: 501, meetingId: 11, meetingTitle: "주간 생산 현안 회의", itemId: 21, itemTitle: "설비 점검 일정 수립",
            requester: { id: 9, name: "이서연" }, createdAt: "2026-10-02T03:00:00Z", commentPreview: "기한을 다음 주로 바꿔 주세요" },
          { requestId: 502, meetingId: 12, meetingTitle: "월간 생산 계획", itemId: null, itemTitle: null,
            requester: { id: 9, name: "이서연" }, createdAt: "2026-10-02T04:00:00Z", commentPreview: "요약을 보완해 주세요" },
        ],
      },
    };
    await mockApi(page, { todos });
    // 상세 화면 API 는 이 테스트 범위 밖이라 404 로만 응답(이동 확인용)
    await page.route(/\/api\/meetings\/\d+(\/.*)?$/, (route) => json(route, 404, { detail: "회의록을 찾을 수 없습니다" }));
    await loginAndOpenHome(page);

    const section = page.getByRole("region", { name: "수정 요청 대기" });
    await expect(section).toBeVisible();
    await expect(section.getByText("2", { exact: true })).toBeVisible();
    const rows = section.getByRole("link");
    await expect(rows).toHaveCount(2);
    await expect(rows.first()).toContainText("주간 생산 현안 회의");
    await expect(rows.first()).toContainText("기한을 다음 주로 바꿔 주세요");
    await expect(rows.first()).toContainText("설비 점검 일정 수립");
    await expect(rows.first()).toContainText("이서연");
    await expect(rows.first()).toContainText("해결 대기");
    await expect(rows.nth(1)).toContainText("회의록 전체");
    await expect(page.getByText("지금 처리할 일이 없습니다.")).toHaveCount(0);

    await rows.nth(1).click();
    await expect(page).toHaveURL(/\/v2\/meetings\/12$/);
  });

  test("수정 요청 대기 0건(또는 필드 없음) → 묶음 숨김", async ({ page }) => {
    await mockApi(page, { todos: { ...FULL_TODOS, pendingChangeRequests: EMPTY_LIST } });
    await loginAndOpenHome(page);
    await expect(page.getByRole("region", { name: "보완 필요 업무" })).toBeVisible();
    await expect(page.getByRole("region", { name: "수정 요청 대기" })).toHaveCount(0);

    // 필드가 없는 응답(이전 서버)도 같은 결과
    await page.unroute("**/api/me/todos");
    await page.route("**/api/me/todos", (route) => json(route, 200, FULL_TODOS));
    await page.reload();
    await expect(page.getByRole("region", { name: "보완 필요 업무" })).toBeVisible();
    await expect(page.getByRole("region", { name: "수정 요청 대기" })).toHaveCount(0);
  });
});
