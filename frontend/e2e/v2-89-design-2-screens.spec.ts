/*
 * 디자인 2단계 화면 스크린샷·가로 넘침 검사. 375px / 1280px × 라이트(기본)·다크.
 * 스크린샷은 test-results/design-2/ 에 저장한다(.gitignore 대상, 커밋하지 않음). 데이터는 모두 가상이다.
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withAllowed } from "./helpers/allowed-actions";
import { withProjectActions } from "./helpers/project-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const THEME_KEY = "mi.v2.theme";
const DIR = "test-results/design-2";
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const BASE_ME = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
let ME: Record<string, unknown> = BASE_ME;
const ACCOUNTS = [
  { id: 7, name: "한팀장", rank: "manager" }, { id: 8, name: "김대리", rank: "staff" }, { id: 9, name: "권부장", rank: "executive" },
];
const ref = (id: number) => ({ id, name: ACCOUNTS.find((a) => a.id === id)!.name });
const memberOf = (id: number, role: string) => { const a = ACCOUNTS.find((x) => x.id === id)!; return { accountId: id, name: a.name, rank: a.rank, role }; };

const EMPTY = { total: 0, items: [] };
const TODOS = { awaitingConfirmMeetings: EMPTY, needsCompletionItems: EMPTY, myItems: EMPTY, unreadAutoConfirmed: EMPTY, pendingChangeRequests: EMPTY };

const aiItem = (o: Record<string, unknown>) => ({
  id: 1, title: "", assignee: { id: 8, name: "김대리" }, dueDate: "2026-10-15", dueUndetermined: false, status: "pending", confirmKind: null,
  evidenceStartSec: null, evidenceQuote: null, needsCompletion: false, missingFields: [], origin: "ai", ...o,
});
const DETAIL = {
  id: 41, title: "주간 생산 현안 회의", heldAt: "2026-10-01T01:00:00Z", registeredAt: "2026-10-01T02:00:00Z", summary: "3호기 납기 조정과 설비 점검 일정을 논의했다.",
  decisions: ["3호기 납기를 10월 15일로 조정한다"], status: "awaiting_confirmation", confirmKind: null, confirmedBy: null, confirmedAt: null,
  firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: "2026-10-06T01:00:00Z", registeredBy: { id: 8, name: "김대리" }, origin: "transcript_xlsx",
  sourceKind: "transcript_xlsx", hasAudio: false, participants: [{ id: 7, name: "한팀장" }, { id: 8, name: "김대리" }], guestParticipants: [],
  actionItems: [
    aiItem({ id: 101, title: "고객사에 납기 조정 안내" }),
    aiItem({ id: 102, title: "설비 점검 일정 수립", assignee: null, needsCompletion: true, missingFields: ["assignee"] }),
  ],
  recentEvents: [], phase: "active", processing: null, minutes: null,
};
const meeting = (id: number, title: string, heldAt: string, status: string, kind: string | null, n: number) => ({
  id, title, heldAt, registeredAt: "2026-10-02T01:00:00Z", registeredBy: { id: 8, name: "김대리" }, origin: "audio", status, confirmKind: kind,
  itemCount: n, needsCompletionCount: 0, autoConfirmAt: null,
});
const MEETINGS = {
  items: [
    meeting(41, "주간 생산 현안 회의", "2026-10-01T01:00:00Z", "awaiting_confirmation", null, 4),
    meeting(42, "3호기 납기 조정 회의", "2026-09-30T00:30:00Z", "confirmed", "period_elapsed", 3),
    meeting(44, "설비 점검 주간 회의", "2026-09-28T04:20:00Z", "processing", null, 0),
  ],
  total: 3, page: 1, size: 20, availablePhases: ["active"],
};
const member = (accountId: number, name: string, completed: number, inProgress: number, overdue: number, overloaded = false) => ({
  accountId, name, completed, inProgress, overdue, open: inProgress + overdue, overloaded,
});
const WORKLOAD = {
  snapshotDate: "2026-10-06", generatedAt: "2026-10-07T00:10:00Z",
  scope: { kind: "company", departments: [{ id: 1, name: "개발팀" }], department: null },
  kpis: { assigned: 20, completionRate: 35, overdue: 7, avgOpenPerPerson: 2.7, memberCount: 3 },
  members: [member(1, "한과다", 1, 2, 4, true), member(2, "김담당", 3, 2, 1), member(3, "이완료", 3, 0, 0)],
  urgentItems: [{ itemId: 11, title: "견적서 송부", dueDate: "2026-10-01", kind: "overdue", days: 5, meetingId: 41, meetingTitle: "회의 41", assignee: { id: 9, name: "김담당" } }],
};
const PROJECT = (id: number, name: string, status: string) => ({
  id, name, description: "설비 개선 과제", status, departmentId: 1, departmentName: "개발팀", registeredBy: ref(7), lead: ref(7), approver: null, myRole: "lead",
  memberCount: 2, createdAt: "2026-10-01T01:00:00Z", decidedAt: null, members: [memberOf(7, "lead"), memberOf(8, "member")],
});
const PROJECTS = [PROJECT(1, "3호기 설비 개선", "active"), PROJECT(2, "품질 지표 정비", "pending_approval"), PROJECT(3, "구형 라인 폐기", "rejected")];
const acct = (id: number, over: Record<string, unknown> = {}) => ({
  id, loginId: `user${id}`, name: `사용자${id}`, email: `u${id}@example.com`, rank: "staff", isActive: true, isSystemAdmin: false,
  departments: [{ id: 1, name: "개발팀", role: "member" }], createdAt: "2026-10-01T00:00:00Z", ...over,
});

async function mount(page: Page, theme: "light" | "dark", systemAdmin = false) {
  ME = systemAdmin ? { ...BASE_ME, isSystemAdmin: true } : BASE_ME; // 시스템 관리자는 관리 화면만 쓴다
  await page.route("**/api/**", (route) => json(route, []));
  await page.route("**/api/auth/me", (route) => json(route, ME));
  await page.route("**/api/me/todos", (route) => json(route, TODOS));
  await page.route("**/api/me/notices", (route) => json(route, []));
  await page.route("**/api/me/processing", (route) => json(route, []));
  await page.route("**/api/me/org", (route) => json(route, [{ departmentId: 1, name: "개발팀", kind: "normal", role: "head" }]));
  await page.route("**/api/accounts", (route) => json(route, ACCOUNTS));
  await page.route(/\/api\/workload(\?.*)?$/, (route) => json(route, WORKLOAD));
  await page.route("**/api/meetings?*", (route) => json(route, MEETINGS));
  await page.route(/\/api\/meetings\/\d+$/, (route) => json(route, withAllowed(DETAIL as never, ME as never)));
  await page.route(/\/api\/meetings\/\d+\/transcript$/, (route) =>
    json(route, { fullText: "x", segments: null, sttProvider: "자료 파일(STT 없음)", speakers: [] }));
  await page.route(/\/api\/meetings\/\d+\/speakers$/, (route) => json(route, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route(/\/api\/meetings\/\d+\/change-requests$/, (route) => json(route, []));
  await page.route("**/api/projects/candidates?*", (route) =>
    json(route, {
      ownDepartment: { departmentId: 1, departmentName: "개발팀", members: [{ accountId: 7, name: "한팀장", rank: "manager" }, { accountId: 8, name: "김대리", rank: "staff" }] },
      otherDepartments: [], executiveGroup: [],
    }));
  await page.route("**/api/projects", (route) => json(route, PROJECTS));
  await page.route(/\/api\/projects\/\d+$/, (route) => json(route, withProjectActions(PROJECT(1, "3호기 설비 개선", "active") as never, ME as never, null)));
  await page.route(/\/api\/projects\/\d+\/meetings(\?.*)?$/, (route) => json(route, { items: [], total: 0, page: 1, size: 20 }));
  await page.route(/\/api\/admin\/accounts(\?.*)?$/, (route) =>
    json(route, { items: [acct(5, { name: "박담당", rank: "manager" }), acct(6, { name: "이사원" }), acct(7, { name: "한팀장", isSystemAdmin: true })], total: 3, page: 1, size: 20 }));
  await page.route(/\/api\/admin\/departments(\/.*)?$/, (route) => json(route, [{ id: 1, name: "개발팀", kind: "normal", memberCount: 3 }]));
  await page.addInitScript(([themeKey, t]) => window.localStorage.setItem(themeKey, t), [THEME_KEY, theme]);
  await page.goto("/v2/login");
  await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
}

const SCREENS: { name: string; path: string; ready: string }[] = [
  { name: "workload", path: "/v2/workload", ready: "업무 현황" },
  { name: "meetings", path: "/v2/meetings", ready: "회의록" },
  { name: "meeting-detail", path: "/v2/meetings/41", ready: "주간 생산 현안 회의" },
  { name: "upload", path: "/v2/upload", ready: "올리기" },
  { name: "projects", path: "/v2/projects", ready: "프로젝트" },
  { name: "project-new", path: "/v2/projects/new", ready: "프로젝트 등록" },
  { name: "project-detail", path: "/v2/projects/1", ready: "3호기 설비 개선" },
  { name: "admin", path: "/v2/admin", ready: "시스템 관리" },
  { name: "admin-accounts", path: "/v2/admin/accounts", ready: "시스템 관리" },
];

for (const theme of ["light", "dark"] as const) {
  for (const width of [375, 1280]) {
    for (const s of SCREENS) {
      test(`${s.name} ${theme} ${width}px: 가로 넘침 없음, 스크린샷 저장`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await mount(page, theme, s.path.startsWith("/v2/admin"));
        await page.goto(s.path);
        await expect(page.getByRole("heading", { name: new RegExp(s.ready) }).first()).toBeVisible();
        await page.evaluate(() => document.fonts.ready);
        await expect(page.locator("html")).toHaveAttribute("data-v2-theme", theme);
        expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
        await page.screenshot({ path: `${DIR}/${s.name}-${theme}-${width}.png`, fullPage: true });
      });
    }
  }
}

test("로그인 화면 라이트 375/1280px", async ({ page }) => {
  for (const width of [375, 1280]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/v2/login");
    await expect(page.getByRole("heading").first()).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: `${DIR}/login-light-${width}.png`, fullPage: true });
  }
});
