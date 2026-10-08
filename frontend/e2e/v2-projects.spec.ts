/*
 * v2 프로젝트 화면 E2E(작업 69-1): 메뉴, 목록, 등록, 상세(승인·반려·참여자·총괄 변경), 프로젝트 회의록, 접근성, 두 테마.
 * 실제 백엔드 없이 상태를 가진 가짜 서버(page.route)로 응답한다(데이터는 모두 가상).
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withProjectActions } from "./helpers/project-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const THEME_KEY = "mi.v2.theme";
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

type Rank = "staff" | "manager" | "executive";
interface Me { id: number; name: string; rank: Rank; tenantId: number }
const LEAD: Me = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const STAFF: Me = { id: 8, name: "김대리", rank: "staff", tenantId: 1 };
const EXEC: Me = { id: 9, name: "권부장", rank: "executive", tenantId: 1 };
const ACCOUNTS = [
  { id: 7, name: "한팀장", rank: "manager" }, { id: 8, name: "김대리", rank: "staff" }, { id: 9, name: "권부장", rank: "executive" }, { id: 10, name: "박관리", rank: "manager" },
];
const ref = (id: number) => ({ id, name: ACCOUNTS.find((a) => a.id === id)!.name });
const memberOf = (id: number, role: string) => { const a = ACCOUNTS.find((x) => x.id === id)!; return { accountId: id, name: a.name, rank: a.rank, role }; };

interface P {
  id: number; name: string; description: string; status: string; departmentId: number; departmentName: string; registeredBy: { id: number; name: string };
  lead: { id: number; name: string } | null; approver: { id: number; name: string } | null; myRole: string | null; memberCount: number; createdAt: string; decidedAt: string | null;
  members: ReturnType<typeof memberOf>[];
}
const mk = (over: Partial<P> & { id: number; name: string; status: string }): P => ({
  description: "", departmentId: 1, departmentName: "개발팀", registeredBy: ref(7), lead: ref(7), approver: null, myRole: "lead", memberCount: 2,
  createdAt: "2026-10-01T01:00:00Z", decidedAt: null, members: [memberOf(7, "lead"), memberOf(8, "member")], ...over,
});

interface Server {
  me: Me;
  projects: P[];
  org: { departmentId: number; name: string; kind: string; role: string }[];
  calls: { method: string; path: string; body: unknown }[];
  listStatus: number;
  createError: { status: number; detail: string } | null;
  createDelayMs: number;
  meetings: unknown[];
  meetingsTotal: number;
  detailStatus: number;
}
const CANDIDATES = {
  ownDepartment: { departmentId: 1, departmentName: "개발팀", members: [{ accountId: 7, name: "한팀장", rank: "manager" }, { accountId: 8, name: "김대리", rank: "staff" }] },
  otherDepartments: [{ departmentId: 2, departmentName: "영업팀", members: [{ accountId: 10, name: "박관리", rank: "manager" }] }],
  executiveGroup: [{ departmentId: 3, departmentName: "임원", members: [{ accountId: 9, name: "권부장", rank: "executive" }] }],
};

async function start(page: Page, opts: Partial<Server> = {}): Promise<Server> {
  const server: Server = {
    me: LEAD, projects: [], org: [{ departmentId: 1, name: "개발팀", kind: "normal", role: "head" }], calls: [], listStatus: 200, createError: null, createDelayMs: 0,
    meetings: [], meetingsTotal: 0, detailStatus: 200, ...opts,
  };
  const record = (route: Route) => {
    const req = route.request();
    server.calls.push({ method: req.method(), path: new URL(req.url()).pathname.replace(/^\/api/, ""), body: req.postDataJSON?.() ?? null });
  };
  const byId = (route: Route) => server.projects.find((p) => p.id === Number(/projects\/(\d+)/.exec(route.request().url())![1]));
  const full = (p: P) => withProjectActions({ ...p, memberCount: p.members.length }, server.me, p.status === "pending_approval" ? p.approver?.id ?? null : null);
  const myRoleOf = (p: P) => p.members.find((m) => m.accountId === server.me.id)?.role ?? null;

  await page.route("**/api/**", (route) => json(route, []));
  await page.route("**/api/auth/me", (route) => (route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, server.me) : json(route, { detail: "x" }, 401)));
  await page.route("**/api/me/todos", (route) => json(route, { awaitingConfirmMeetings: { total: 0, items: [] }, needsCompletionItems: { total: 0, items: [] }, myItems: { total: 0, items: [] }, unreadAutoConfirmed: { total: 0, items: [] }, pendingChangeRequests: { total: 0, items: [] } }));
  await page.route("**/api/accounts", (route) => json(route, ACCOUNTS));
  await page.route("**/api/me/org", (route) => json(route, server.org));
  await page.route("**/api/projects/candidates?*", (route) => json(route, CANDIDATES));
  await page.route("**/api/projects", async (route) => {
    if (route.request().method() === "GET") {
      if (server.listStatus !== 200) return json(route, { detail: "목록 오류" }, server.listStatus);
      return json(route, server.projects.map((p) => ({ ...p, memberCount: p.members.length, myRole: myRoleOf(p) })));
    }
    record(route);
    if (server.createDelayMs) await new Promise((r) => setTimeout(r, server.createDelayMs));
    if (server.createError) return json(route, { detail: server.createError.detail }, server.createError.status);
    const body = route.request().postDataJSON();
    const dept = server.org.find((d) => d.departmentId === body.departmentId)!;
    const head = dept.role === "head";
    const created = mk({
      id: 100 + server.projects.length, name: body.name, description: body.description, status: head ? "active" : "pending_approval", departmentId: dept.departmentId,
      departmentName: dept.name, registeredBy: ref(server.me.id), lead: head ? ref(server.me.id) : null, approver: head ? null : ref(7),
      members: [memberOf(server.me.id, head ? "lead" : "member"), ...body.memberIds.map((id: number) => memberOf(id, "member"))],
    });
    server.projects.push(created);
    return json(route, full(created), 201);
  });
  await page.route(/\/api\/projects\/\d+$/, (route) => {
    const p = byId(route);
    return server.detailStatus !== 200 || !p ? json(route, { detail: "프로젝트를 찾을 수 없습니다" }, 404) : json(route, full(p));
  });
  await page.route(/\/api\/projects\/\d+\/approve$/, (route) => {
    record(route);
    const p = byId(route)!;
    p.status = "active"; p.lead = ref(server.me.id); p.decidedAt = "2026-10-02T01:00:00Z";
    p.members = [...p.members, memberOf(server.me.id, "lead")];
    return json(route, full(p));
  });
  await page.route(/\/api\/projects\/\d+\/reject$/, (route) => {
    record(route);
    const p = byId(route)!;
    p.status = "rejected";
    return json(route, full(p));
  });
  await page.route(/\/api\/projects\/\d+\/members$/, (route) => {
    record(route);
    const p = byId(route)!;
    const body = route.request().postDataJSON();
    p.members = [...p.members, memberOf(body.accountId, body.role)];
    return json(route, full(p), 201);
  });
  await page.route(/\/api\/projects\/\d+\/members\/\d+\/remove$/, (route) => {
    record(route);
    const p = byId(route)!;
    const id = Number(/members\/(\d+)\/remove/.exec(route.request().url())![1]);
    p.members = p.members.filter((m) => m.accountId !== id);
    return json(route, full(p));
  });
  await page.route(/\/api\/projects\/\d+\/change-lead$/, (route) => {
    record(route);
    const p = byId(route)!;
    const body = route.request().postDataJSON();
    p.members = p.members.map((m) => (m.role === "lead" ? { ...m, role: "manager" } : m));
    p.members = p.members.some((m) => m.accountId === body.newLeadId) ? p.members.map((m) => (m.accountId === body.newLeadId ? { ...m, role: "lead" } : m)) : [...p.members, memberOf(body.newLeadId, "lead")];
    p.lead = ref(body.newLeadId);
    return json(route, full(p));
  });
  await page.route(/\/api\/projects\/\d+\/meetings(\?.*)?$/, (route) => {
    const pageNo = Number(new URL(route.request().url()).searchParams.get("page") ?? "1");
    return json(route, { items: pageNo === 1 ? server.meetings : [], total: server.meetingsTotal, page: pageNo, size: 20, availablePhases: ["active"] });
  });
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
  return server;
}

const mainNav = (page: Page) => page.getByRole("navigation", { name: "주 메뉴" });
const meetingRow = (id: number, title: string) => ({
  id, title, heldAt: "2026-10-01T01:00:00Z", registeredBy: { id: 7, name: "한팀장" }, origin: "audio_minutes", status: "confirmed", confirmKind: "manager",
  itemCount: 2, needsCompletionCount: 0, autoConfirmAt: null, phase: "active",
});
const three = () => [mk({ id: 1, name: "신제품 개발", status: "active" }), mk({ id: 2, name: "대기 건", status: "pending_approval", lead: null, approver: ref(7), registeredBy: ref(8), myRole: "member" }), mk({ id: 3, name: "반려 건", status: "rejected", lead: null })];

// ================= 메뉴 =================
test.describe("메뉴", () => {
  test("프로젝트 항목이 회의록 아래에 보이고 이동하며 현재 위치가 표시된다", async ({ page }) => {
    await start(page, { projects: three() });
    await page.goto("/v2");
    await expect(mainNav(page).getByRole("link", { name: "프로젝트" })).toBeVisible();
    const names = await mainNav(page).getByRole("link").allTextContents();
    expect(names).toEqual(["할 일", "업무 현황", "회의록", "프로젝트"]); // 69-3: 할 일 아래에 업무 현황 추가
    await mainNav(page).getByRole("link", { name: "프로젝트" }).click();
    await expect(page).toHaveURL(/\/v2\/projects$/);
    await expect(mainNav(page).getByRole("link", { name: "프로젝트" })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("heading", { level: 1, name: "프로젝트" })).toBeVisible();
  });

  test("모바일 드로어에서도 보이고 이동하면 닫힌다", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await start(page, { projects: three() });
    await page.goto("/v2");
    await page.getByRole("button", { name: "메뉴 열기" }).click();
    await mainNav(page).getByRole("link", { name: "프로젝트" }).click();
    await expect(page).toHaveURL(/\/v2\/projects$/);
    await expect(page.locator("aside")).toBeHidden();
    await page.getByRole("button", { name: "메뉴 열기" }).click();
    await expect(mainNav(page).getByRole("link", { name: "프로젝트" })).toHaveAttribute("aria-current", "page");
  });
});

// ================= 목록 =================
test.describe("목록", () => {
  test("탭·개수·항목 내용·승인 필요 표시·클릭 이동", async ({ page }) => {
    await start(page, { projects: three() });
    await page.goto("/v2/projects");
    const table = page.getByRole("table", { name: "프로젝트 목록" });
    await expect(table.getByRole("row")).toHaveCount(4); // 머리 + 3
    await expect(page.getByRole("tab", { name: /^전체/ })).toContainText("(3)");
    await expect(page.getByRole("tab", { name: /^진행 중/ })).toContainText("(1)");
    await expect(page.getByRole("tab", { name: /^승인 대기/ })).toContainText("(1)");
    await expect(page.getByRole("tab", { name: /^반려/ })).toContainText("(1)");
    const first = table.getByRole("row").filter({ hasText: "신제품 개발" });
    await expect(first).toContainText("진행 중");
    await expect(first).toContainText("개발팀");
    await expect(first).toContainText("한팀장");
    await expect(first).toContainText("총괄"); // 내 역할
    await expect(table.getByRole("row").filter({ hasText: "대기 건" })).toContainText("승인 필요"); // 내가 승인자(한팀장)
    await expect(first).not.toContainText("승인 필요");
    await page.getByRole("tab", { name: /^승인 대기/ }).click();
    await expect(table.getByRole("row")).toHaveCount(2);
    await expect(page.getByRole("tab", { name: /^승인 대기/ })).toHaveAttribute("aria-selected", "true");
    await table.getByRole("link", { name: "대기 건" }).click();
    await expect(page).toHaveURL(/\/v2\/projects\/2$/);
  });

  test("다른 사람이 승인자면 승인 필요 표시가 없다", async ({ page }) => {
    await start(page, { me: EXEC, projects: three() });
    await page.goto("/v2/projects");
    await expect(page.getByText("승인 필요")).toHaveCount(0);
  });

  test("빈 상태·오류(다시 시도)", async ({ page }) => {
    const server = await start(page, { projects: [] });
    await page.goto("/v2/projects");
    await expect(page.getByRole("status").filter({ hasText: "프로젝트가 없습니다." })).toBeVisible();
    await expect(page.getByRole("link", { name: "프로젝트 등록" })).toBeVisible();
    server.listStatus = 500;
    await page.reload();
    await expect(page.getByRole("alert").filter({ hasText: "프로젝트를 불러오지 못했습니다" })).toBeVisible();
    server.listStatus = 200;
    server.projects = three();
    await page.getByRole("button", { name: "다시 시도" }).click();
    await expect(page.getByRole("table", { name: "프로젝트 목록" })).toBeVisible();
  });

  for (const width of [1280, 768, 375]) {
    test(`가로 넘침 없음, 768 미만은 카드 ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await start(page, { projects: [mk({ id: 1, name: "아주 긴 프로젝트 이름이 들어가도 칸이 겹치지 않아야 합니다 ".repeat(3), status: "active" }), ...three().slice(1)] });
      await page.goto("/v2/projects");
      await expect(page.getByRole("table", { name: "프로젝트 목록" })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      const display = await page.locator('table[aria-label="프로젝트 목록"] tbody tr').first().evaluate((el) => getComputedStyle(el).display);
      expect(display).toBe(width < 768 ? "flex" : "table-row");
    });
  }
});

// ================= 등록 =================
test.describe("등록", () => {
  test("소속 부서가 없으면 안내하고 제출 수단이 없다", async ({ page }) => {
    await start(page, { org: [] });
    await page.goto("/v2/projects/new");
    await expect(page.getByText("소속 부서가 없어 프로젝트를 등록할 수 없습니다. 관리자에게 문의하세요")).toBeVisible();
    await expect(page.getByRole("button", { name: "등록하기" })).toHaveCount(0);
  });

  test("이름 검증(공백·120자 초과), 부서별 안내 문구, 후보 3그룹과 선택", async ({ page }) => {
    await start(page, { me: STAFF, org: [{ departmentId: 1, name: "개발팀", kind: "normal", role: "member" }, { departmentId: 4, name: "기획팀", kind: "normal", role: "head" }] });
    await page.goto("/v2/projects/new");
    const submit = page.getByRole("button", { name: "등록하기" });
    await expect(submit).toBeDisabled();
    await page.getByLabel("프로젝트 이름").fill("   ");
    await expect(page.getByText("프로젝트 이름은 공백만으로 쓸 수 없습니다.")).toBeVisible();
    await expect(submit).toBeDisabled();
    await page.getByLabel("프로젝트 이름").fill("가".repeat(121));
    await expect(page.getByText("프로젝트 이름은 120자 이하여야 합니다.")).toBeVisible();
    await expect(submit).toBeDisabled();
    await page.getByLabel("프로젝트 이름").fill("정상 이름");
    await expect(submit).toBeEnabled();
    await expect(page.getByText("부서장의 승인 후 진행됩니다")).toBeVisible(); // 개발팀: 부서원
    await page.getByLabel("등록 부서").selectOption("4");
    await expect(page.getByText("등록하면 바로 진행 중이 되고 본인이 총괄이 됩니다")).toBeVisible(); // 기획팀: 부서장
    await page.getByLabel("등록 부서").selectOption("1");
    await expect(page.getByRole("group", { name: "우리 부서" })).toBeVisible();
    await expect(page.getByRole("group", { name: "다른 부서 · 영업팀" })).toBeVisible();
    await expect(page.getByRole("group", { name: "임원 그룹 · 임원" })).toBeVisible();
    await expect(page.getByRole("group", { name: "우리 부서" }).getByLabel(/김대리/)).toHaveCount(0); // 본인은 목록에서 뺀다
    await page.getByLabel(/박관리/).check();
    await page.getByLabel(/권부장/).check();
    await expect(page.getByLabel(/박관리/)).toBeChecked();
    // 부서를 바꾸면 선택이 비워지고 후보를 다시 불러온다
    await page.getByLabel("등록 부서").selectOption("4");
    await expect(page.getByLabel(/박관리/)).not.toBeChecked();
  });

  test("부서장 등록은 제출 본문대로 바로 진행 중이 되어 상세로 이동한다", async ({ page }) => {
    const server = await start(page, { me: LEAD });
    await page.goto("/v2/projects/new");
    await page.getByLabel("프로젝트 이름").fill("  새 프로젝트  ");
    await page.getByLabel(/설명/).fill("설명입니다");
    await page.getByLabel(/김대리/).check();
    await page.getByLabel(/박관리/).check();
    await page.getByRole("button", { name: "등록하기" }).click();
    await expect(page).toHaveURL(/\/v2\/projects\/100$/);
    expect(server.calls.find((c) => c.method === "POST")!.body).toEqual({ name: "새 프로젝트", description: "설명입니다", departmentId: 1, memberIds: [8, 10] });
    await expect(page.getByRole("heading", { level: 1, name: "새 프로젝트" })).toBeVisible();
    await expect(page.locator("header").getByText("진행 중")).toBeVisible();
  });

  test("부서원 등록은 승인 대기", async ({ page }) => {
    await start(page, { me: STAFF, org: [{ departmentId: 1, name: "개발팀", kind: "normal", role: "member" }] });
    await page.goto("/v2/projects/new");
    await page.getByLabel("프로젝트 이름").fill("대기될 프로젝트");
    await page.getByRole("button", { name: "등록하기" }).click();
    await expect(page).toHaveURL(/\/v2\/projects\/\d+$/);
    await expect(page.getByText("승인 대기").first()).toBeVisible();
    await expect(page.getByText("승인 대기 중, 승인자: 한팀장")).toBeVisible();
  });

  for (const [status, detail] of [[409, "부서장이 지정되지 않아 등록할 수 없습니다"], [403, "소속된 부서로만 프로젝트를 등록할 수 있습니다"], [422, "name 은 비워 둘 수 없습니다"]] as const) {
    test(`서버 거부 ${status} 메시지가 폼 위 알림으로 보인다`, async ({ page }) => {
      await start(page, { createError: { status, detail } });
      await page.goto("/v2/projects/new");
      await page.getByLabel("프로젝트 이름").fill("거부될 프로젝트");
      await page.getByRole("button", { name: "등록하기" }).click();
      await expect(page.getByRole("alert").filter({ hasText: detail })).toBeVisible();
      await expect(page).toHaveURL(/\/v2\/projects\/new$/);
      await expect(page.getByRole("button", { name: "등록하기" })).toBeEnabled();
    });
  }

  test("제출 중 중복 클릭은 한 번만 요청한다", async ({ page }) => {
    const server = await start(page, { createDelayMs: 600 });
    await page.goto("/v2/projects/new");
    await page.getByLabel("프로젝트 이름").fill("한 번만");
    const button = page.getByRole("button", { name: "등록하기" });
    await button.dblclick();
    await expect(page).toHaveURL(/\/v2\/projects\/\d+$/);
    expect(server.calls.filter((c) => c.method === "POST")).toHaveLength(1);
  });
});

// ================= 상세 =================
test.describe("상세", () => {
  test("승인자: 승인·반려 표시, 반려는 사유 필수(빈 사유 거부), 승인하면 진행 중", async ({ page }) => {
    const server = await start(page, { me: LEAD, projects: three() });
    await page.goto("/v2/projects/2");
    await expect(page.getByRole("button", { name: "승인", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "반려", exact: true })).toBeVisible();
    await expect(page.getByText("승인 대기 중, 승인자")).toHaveCount(0);
    await page.getByRole("button", { name: "반려", exact: true }).click();
    const dialog = page.getByRole("alertdialog", { name: "프로젝트 반려" });
    await expect(dialog.getByRole("button", { name: "반려하기" })).toBeDisabled();
    await dialog.getByLabel(/사유/).fill("   ");
    await expect(dialog.getByRole("button", { name: "반려하기" })).toBeDisabled();
    await dialog.getByRole("button", { name: "취소" }).click();
    await expect(page.getByRole("button", { name: "반려", exact: true })).toBeFocused(); // 모달 포커스 복원
    await page.getByRole("button", { name: "승인", exact: true }).click();
    await expect(page.getByRole("status").filter({ hasText: "프로젝트를 승인했습니다" })).toBeVisible();
    await expect(page.locator("header").getByText("진행 중")).toBeVisible();
    expect(server.calls.map((c) => c.path)).toEqual(["/projects/2/approve"]);
    await expect(page.getByRole("button", { name: "승인", exact: true })).toHaveCount(0);
  });

  test("반려: 사유를 보내고 반려 상태가 된다", async ({ page }) => {
    const server = await start(page, { me: LEAD, projects: three() });
    await page.goto("/v2/projects/2");
    await page.getByRole("button", { name: "반려", exact: true }).click();
    const dialog = page.getByRole("alertdialog", { name: "프로젝트 반려" });
    await dialog.getByLabel(/사유/).fill("예산이 없습니다");
    await dialog.getByRole("button", { name: "반려하기" }).click();
    await expect(page.getByRole("status").filter({ hasText: "프로젝트를 반려했습니다" })).toBeVisible();
    expect(server.calls[0]).toMatchObject({ path: "/projects/2/reject", body: { reason: "예산이 없습니다" } });
    await expect(page.locator("header").getByText("반려", { exact: true })).toBeVisible();
  });

  test("허용 동작이 없는 사람은 버튼 없이 '승인 대기 중' 안내만 본다", async ({ page }) => {
    await start(page, { me: STAFF, projects: three() });
    await page.goto("/v2/projects/2");
    await expect(page.getByText("승인 대기 중, 승인자: 한팀장")).toBeVisible();
    for (const name of ["승인", "반려", "총괄 변경", "참여자 추가"]) await expect(page.getByRole("button", { name, exact: true })).toHaveCount(0);
  });

  test("진행 중 총괄에게 참여자 관리·총괄 변경이 보이고 일반 참여자에게는 없다", async ({ page }) => {
    await start(page, { me: LEAD, projects: three() });
    await page.goto("/v2/projects/1");
    await expect(page.getByRole("button", { name: "참여자 추가" })).toBeVisible();
    await expect(page.getByRole("button", { name: "총괄 변경", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "한팀장 참여자 제거" })).toHaveCount(0); // 총괄 행에는 제거 버튼이 없다
    await expect(page.getByRole("button", { name: "김대리 참여자 제거" })).toBeVisible();
  });

  test("일반 참여자(담당자)는 참여자 관리·총괄 변경 버튼이 없다", async ({ page }) => {
    await start(page, { me: STAFF, projects: three() });
    await page.goto("/v2/projects/1");
    await expect(page.getByRole("table", { name: "참여자 목록" })).toBeVisible();
    for (const name of ["참여자 추가", "총괄 변경"]) await expect(page.getByRole("button", { name, exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /참여자 제거/ })).toHaveCount(0);
  });

  test("지시자는 총괄 변경만 보인다(참여자 관리는 총괄만)", async ({ page }) => {
    await start(page, { me: EXEC, projects: three() });
    await page.goto("/v2/projects/1");
    await expect(page.getByRole("button", { name: "총괄 변경", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "참여자 추가" })).toHaveCount(0);
  });

  test("참여자 추가: 후보에서 선택, 요청 본문, 표 갱신, 닫으면 버튼으로 포커스 복원", async ({ page }) => {
    const server = await start(page, { me: LEAD, projects: three() });
    await page.goto("/v2/projects/1");
    const open = page.getByRole("button", { name: "참여자 추가" });
    await open.click();
    const dialog = page.getByRole("alertdialog", { name: "참여자 추가" }).or(page.getByRole("dialog", { name: "참여자 추가" }));
    await expect(dialog.getByRole("button", { name: "참여자 추가" })).toBeDisabled();
    const select = dialog.getByLabel("추가할 사람");
    await expect(select.locator("optgroup")).toHaveCount(2); // 우리 부서는 모두 이미 참여 중이라 빠지고 다른 부서·임원 그룹만 남는다
    await expect(select.locator("option", { hasText: "김대리" })).toHaveCount(0);
    await dialog.getByRole("button", { name: "취소" }).click();
    await expect(open).toBeFocused();
    await open.click();
    await dialog.getByLabel("추가할 사람").selectOption({ label: "박관리 (중간관리자)" });
    await dialog.getByLabel("역할").selectOption("manager");
    await dialog.getByRole("button", { name: "참여자 추가" }).click();
    await expect(page.getByRole("status").filter({ hasText: "참여자를 추가했습니다" })).toBeVisible();
    expect(server.calls[0]).toMatchObject({ path: "/projects/1/members", body: { accountId: 10, role: "manager" } });
    const row = page.getByRole("table", { name: "참여자 목록" }).getByRole("row").filter({ hasText: "박관리" });
    await expect(row).toContainText("관리자");
  });

  test("참여자 제거: 확인 팝업(구체 행동 이름)과 요청, 표 갱신", async ({ page }) => {
    const server = await start(page, { me: LEAD, projects: three() });
    await page.goto("/v2/projects/1");
    await page.getByRole("button", { name: "김대리 참여자 제거" }).click();
    const dialog = page.getByRole("alertdialog", { name: "참여자 제거" });
    await expect(dialog.getByRole("button", { name: "참여자 제거" })).toBeVisible();
    await dialog.getByRole("button", { name: "참여자 제거" }).click();
    await expect(page.getByRole("status").filter({ hasText: "김대리 님을 참여자에서 제거했습니다" })).toBeVisible();
    expect(server.calls[0].path).toBe("/projects/1/members/8/remove");
    await expect(page.getByRole("table", { name: "참여자 목록" }).getByRole("row").filter({ hasText: "김대리" })).toHaveCount(0);
  });

  test("총괄 변경: 사유 필수, 관리자 이상만 선택, 요청 본문, 이전 총괄이 관리자로 남는다", async ({ page }) => {
    const server = await start(page, { me: LEAD, projects: three() });
    await page.goto("/v2/projects/1");
    const open = page.getByRole("button", { name: "총괄 변경", exact: true });
    await open.click();
    const dialog = page.getByRole("dialog", { name: "총괄 변경" }).or(page.getByRole("alertdialog", { name: "총괄 변경" }));
    const confirm = dialog.getByRole("button", { name: "총괄 변경" });
    await expect(confirm).toBeDisabled();
    const options = await dialog.getByLabel("새 총괄").locator("option").allTextContents();
    expect(options.join("|")).not.toContain("김대리"); // 담당자 직급 제외
    expect(options.join("|")).not.toContain("한팀장 (중간관리자)"); // 현재 총괄 제외
    await dialog.getByLabel("새 총괄").selectOption({ label: "박관리 (중간관리자)" });
    await expect(confirm).toBeDisabled(); // 사유 없음
    await dialog.getByLabel("변경 사유").fill("   ");
    await expect(confirm).toBeDisabled(); // 공백 금지
    await dialog.getByLabel("변경 사유").fill("인사 이동으로 총괄을 바꿉니다");
    await confirm.click();
    await expect(page.getByRole("status").filter({ hasText: "총괄을 박관리(으)로 바꿨습니다" })).toBeVisible();
    expect(server.calls[0]).toMatchObject({ path: "/projects/1/change-lead", body: { newLeadId: 10, reason: "인사 이동으로 총괄을 바꿉니다" } });
    const table = page.getByRole("table", { name: "참여자 목록" });
    await expect(table.getByRole("row").filter({ hasText: "박관리" })).toContainText("총괄");
    await expect(table.getByRole("row").filter({ hasText: "한팀장" })).toContainText("관리자");
    await expect(page.getByRole("button", { name: "총괄 변경", exact: true })).toHaveCount(0); // 서버가 이제 허용하지 않는다
  });

  test("서버 거부 메시지(409)를 그대로 보인다", async ({ page }) => {
    await start(page, { me: LEAD, projects: three() });
    await page.route(/\/api\/projects\/\d+\/members$/, (route) => json(route, { detail: "총괄·관리자 역할은 관리자 이상 직급만 맡을 수 있습니다" }, 409));
    await page.goto("/v2/projects/1");
    await page.getByRole("button", { name: "참여자 추가" }).click();
    const dialog = page.getByRole("dialog", { name: "참여자 추가" }).or(page.getByRole("alertdialog", { name: "참여자 추가" }));
    await dialog.getByLabel("추가할 사람").selectOption({ label: "권부장 (지시자)" });
    await dialog.getByRole("button", { name: "참여자 추가" }).click();
    await expect(dialog.getByRole("alert").filter({ hasText: "관리자 이상 직급만 맡을 수 있습니다" })).toBeVisible();
  });

  test("볼 수 없는 프로젝트(404) 안내와 목록 링크", async ({ page }) => {
    await start(page, { me: LEAD, projects: three() });
    await page.goto("/v2/projects/999");
    await expect(page.getByRole("heading", { level: 1, name: "프로젝트를 찾을 수 없습니다" })).toBeVisible();
    await page.getByRole("link", { name: "← 프로젝트 목록으로" }).click();
    await expect(page).toHaveURL(/\/v2\/projects$/);
  });

  test("참여자 표는 좁은 폭에서 겹치지 않고 가로 넘침이 없다", async ({ page }) => {
    for (const width of [1280, 768, 375]) {
      await page.setViewportSize({ width, height: 900 });
      await start(page, { me: LEAD, projects: [mk({ id: 1, name: "프로젝트", status: "active", members: [memberOf(7, "lead"), memberOf(8, "member"), memberOf(10, "manager")] })] });
      await page.goto("/v2/projects/1");
      await expect(page.getByRole("table", { name: "참여자 목록" })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      const cells = await page.locator('table[aria-label="참여자 목록"] tbody tr').first().evaluate((tr) =>
        Array.from(tr.querySelectorAll("td")).map((td) => { const r = td.getBoundingClientRect(); return { l: r.left, r: r.right, over: td.scrollWidth > td.clientWidth + 1 }; }),
      );
      cells.forEach((c, i) => { expect(c.over).toBe(false); if (i > 0) expect(c.l).toBeGreaterThanOrEqual(cells[i - 1].r - 0.5); });
    }
  });
});

// ================= 프로젝트 회의록 =================
test.describe("프로젝트 회의록", () => {
  test("항목·클릭 이동", async ({ page }) => {
    await start(page, { me: LEAD, projects: three(), meetings: [meetingRow(41, "주간 회의"), meetingRow(42, "착수 회의")], meetingsTotal: 2 });
    await page.route(/\/api\/meetings\/\d+$/, (route) => json(route, { detail: "x" }, 404));
    await page.goto("/v2/projects/1");
    const table = page.getByRole("table", { name: "프로젝트 회의록 목록" });
    await expect(table.getByRole("row")).toHaveCount(3);
    await expect(table.getByRole("row").filter({ hasText: "주간 회의" })).toContainText("진행중");
    await expect(table.getByRole("row").filter({ hasText: "주간 회의" })).toContainText("확정 완료");
    await table.getByRole("link", { name: "착수 회의" }).click();
    await expect(page).toHaveURL(/\/v2\/meetings\/42$/);
  });

  test("빈 목록 안내", async ({ page }) => {
    await start(page, { me: LEAD, projects: three() });
    await page.goto("/v2/projects/1");
    await expect(page.getByRole("status").filter({ hasText: "연결된 회의록이 없습니다" })).toBeVisible();
  });

  test("쪽 나눔", async ({ page }) => {
    const server = await start(page, { me: LEAD, projects: three(), meetings: [meetingRow(41, "첫 쪽 회의")], meetingsTotal: 45 });
    await page.goto("/v2/projects/1");
    await expect(page.getByText("1 / 3 쪽")).toBeVisible();
    await page.getByRole("button", { name: "다음" }).click();
    await expect(page.getByText("2 / 3 쪽")).toBeVisible();
    await expect(server.projects).toHaveLength(3);
  });
});

// ================= 접근성·키보드 =================
test("키보드(Enter)로 총괄 변경 팝업을 열고 닫으면 버튼으로 포커스가 돌아온다", async ({ page }) => {
  await start(page, { me: LEAD, projects: three() });
  await page.goto("/v2/projects/1");
  const open = page.getByRole("button", { name: "총괄 변경", exact: true });
  await open.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("변경 사유")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(open).toBeFocused();
});

test("표·목록·폼에 접근성 이름이 있다", async ({ page }) => {
  await start(page, { me: LEAD, projects: three() });
  await page.goto("/v2/projects");
  await expect(page.getByRole("table", { name: "프로젝트 목록" })).toBeVisible();
  await expect(page.getByRole("tablist", { name: "프로젝트 상태" })).toBeVisible();
  await page.goto("/v2/projects/1");
  await expect(page.getByRole("table", { name: "참여자 목록" })).toBeVisible();
  await expect(page.getByRole("region", { name: "프로젝트 개요" })).toBeVisible();
  await page.goto("/v2/projects/new");
  for (const label of ["프로젝트 이름", "등록 부서"]) await expect(page.getByLabel(label)).toBeVisible();
});

// ================= 두 테마 =================
for (const theme of ["light", "dark"] as const) {
  test(`${theme} 테마에서 새 화면이 콘솔 오류 없이 그려진다`, async ({ page }) => {
    const problems: string[] = [];
    page.on("console", (m) => { if (m.type() === "error") problems.push(m.text()); });
    page.on("pageerror", (e) => problems.push(e.message));
    await page.addInitScript(([k, v]) => window.localStorage.setItem(k, v), [THEME_KEY, theme]);
    await start(page, { me: LEAD, projects: three(), meetings: [meetingRow(41, "주간 회의")], meetingsTotal: 1 });
    for (const [url, ready] of [
      ["/v2/projects", page.getByRole("table", { name: "프로젝트 목록" })],
      ["/v2/projects/new", page.getByLabel("프로젝트 이름")],
      ["/v2/projects/1", page.getByRole("table", { name: "참여자 목록" })],
      ["/v2/projects/2", page.getByRole("button", { name: "승인", exact: true })],
    ] as const) {
      await page.goto(url);
      await expect(ready).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.getAttribute("data-v2-theme"))).toBe(theme);
    }
    expect(problems).toEqual([]);
  });
}
