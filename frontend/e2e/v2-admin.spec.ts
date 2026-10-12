/*
 * 시스템 관리자 화면·본인 비밀번호 변경·표시 보완 E2E(작업 82-2). 백엔드 없이 page.route 로 /api/* 를 가로챈다(데이터는 모두 가상).
 * 범위: 접근 제어·메뉴, 계정 관리(목록·등록·거부 문구·비활성화·초기화·지정/해제), 부서 관리, 시스템 설정(경고·사유), 라이선스, 반응형 가로 넘침,
 *       비밀번호 변경(400/422), 처리 경고·새 오류 코드 문구, 승인 대기 패널.
 */
import { expect, test, type Page, type Route } from "./helpers/test";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const json = (route: Route, status: number, body: unknown) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const SYS = { id: 99, name: "시스템관리", rank: "staff", tenantId: 1, isSystemAdmin: true };
const NORMAL = { id: 7, name: "한팀장", rank: "manager", tenantId: 1, isSystemAdmin: false };

const acct = (id: number, over: Record<string, unknown> = {}) => ({
  id, loginId: `user${id}`, name: `사용자${id}`, email: `u${id}@example.com`, rank: "staff", isActive: true, isSystemAdmin: false,
  departments: [], createdAt: "2026-10-01T00:00:00Z", ...over,
});

interface Mock {
  accounts: ReturnType<typeof acct>[];
  calls: { method: string; path: string; body: unknown }[];
  settings: { enforced: boolean; items: unknown[] };
}

const baseSettings = (enforced = true) => ({
  enforced,
  items: [
    { key: "external_llm_enabled", value: false, source: "default", updatedAt: null, updatedBy: null },
    { key: "local_llm_external_host_allowed", value: false, source: "env", updatedAt: null, updatedBy: null },
  ],
});

async function mount(page: Page, me: Record<string, unknown>, path: string, setup?: (mock: Mock) => void | Promise<void>): Promise<Mock> {
  const mock: Mock = {
    accounts: [acct(1, { name: "김담당", departments: [{ id: 1, name: "개발팀", role: "member" }] }), acct(2, { name: "박관리", rank: "manager", isSystemAdmin: false }), acct(99, { name: "시스템관리", loginId: "sys", isSystemAdmin: true })],
    calls: [],
    settings: baseSettings(),
  };
  await setup?.(mock);
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, me) : json(route, 401, { detail: "x" }),
  );
  await page.route(/\/api\/admin\/accounts(\?.*)?$/, (route) => {
    const request = route.request();
    mock.calls.push({ method: request.method(), path: new URL(request.url()).pathname, body: request.postDataJSON?.() ?? null });
    if (request.method() === "POST") {
      const created = acct(50, { ...(request.postDataJSON() as object), id: 50 });
      mock.accounts.push(created);
      return json(route, 201, created);
    }
    const q = new URL(request.url()).searchParams.get("q") ?? "";
    const items = mock.accounts.filter((a) => !q || a.name.includes(q) || a.loginId.includes(q));
    return json(route, 200, { items, total: items.length, page: 1, size: 20 });
  });
  await page.route(/\/api\/admin\/accounts\/\d+(\/.*)?$/, (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    mock.calls.push({ method: request.method(), path, body: request.postDataJSON?.() ?? null });
    return json(route, request.method() === "POST" && path.endsWith("password-reset") ? 204 : 200, request.method() === "POST" && path.endsWith("password-reset") ? null : acct(1));
  });
  await page.goto("/v2/login");
  await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto(path);
  return mock;
}

const dialogNamed = (page: Page, name: string) => page.getByRole("dialog", { name }).or(page.getByRole("alertdialog", { name }));

test.describe("접근 제어와 메뉴", () => {
  test("일반 사용자는 /v2/admin 에서 /v2 로 돌려보내진다", async ({ page }) => {
    await mount(page, NORMAL, "/v2/admin/accounts");
    await expect(page).toHaveURL(/\/v2$/);
    await expect(page.getByRole("navigation", { name: "주 메뉴" }).getByRole("link", { name: "시스템 관리" })).toHaveCount(0);
  });

  test("시스템 관리자는 회의록·업무·프로젝트·업무 현황 메뉴가 없고 /v2/admin 으로 바로 이동한다", async ({ page }) => {
    await mount(page, SYS, "/v2/meetings");
    await expect(page).toHaveURL(/\/v2\/admin\/accounts$/);
    const nav = page.getByRole("navigation", { name: "주 메뉴" });
    await expect(nav.getByRole("link")).toHaveText(["시스템 관리"]);
    await expect(page.getByRole("link", { name: "회의록 올리기" })).toHaveCount(0);
    await expect(page.getByRole("heading", { level: 1, name: "시스템 관리" })).toBeVisible();
    const tabs = page.getByRole("navigation", { name: "시스템 관리 메뉴" });
    await expect(tabs.getByRole("link")).toHaveText(["계정 관리", "부서 관리", "시스템 설정", "라이선스"]);
    await expect(tabs.getByRole("link", { name: "계정 관리" })).toHaveAttribute("aria-current", "page");
  });
});

test.describe("계정 관리", () => {
  test("목록·검색", async ({ page }) => {
    const mock = await mount(page, SYS, "/v2/admin/accounts");
    const table = page.getByRole("table", { name: "계정 목록" });
    await expect(table.getByRole("row")).toHaveCount(4); // 머리 + 3
    await expect(table).toContainText("김담당");
    await expect(table).toContainText("개발팀");
    await expect(table).toContainText("시스템 관리자");
    await page.getByRole("searchbox").or(page.getByPlaceholder("로그인 ID·이름·이메일 검색")).first().fill("박관리");
    await page.getByRole("button", { name: "검색" }).click();
    await expect(table.getByRole("row")).toHaveCount(2);
    expect(mock.calls.some((c) => c.method === "GET" || c.method === "POST")).toBe(true);
  });

  test("등록: 비밀번호 확인이 다르면 보내지 않고, 맞으면 등록한다", async ({ page }) => {
    const mock = await mount(page, SYS, "/v2/admin/accounts");
    await page.getByRole("button", { name: "계정 등록" }).click();
    const dialog = dialogNamed(page, "계정 등록");
    await dialog.getByLabel("로그인 ID").fill("newbie");
    await dialog.getByLabel("이름").fill("신입");
    await dialog.getByLabel("초기 비밀번호").fill("Valid-Pass-123");
    await dialog.getByLabel("비밀번호 확인").fill("Different-Pass-1");
    await dialog.getByRole("button", { name: "등록" }).click();
    await expect(dialog.getByRole("alert")).toContainText("서로 다릅니다");
    expect(mock.calls.filter((c) => c.method === "POST")).toHaveLength(0);
    await dialog.getByLabel("비밀번호 확인").fill("Valid-Pass-123");
    await dialog.getByRole("button", { name: "등록" }).click();
    await expect(page.getByRole("status").filter({ hasText: "계정을 등록했습니다" })).toBeVisible();
    expect(mock.calls.filter((c) => c.method === "POST")[0].body).toMatchObject({ loginId: "newbie", name: "신입", rank: "staff" });
  });

  test("서버 거부 문구(마지막 관리자 해제)를 그대로 보인다", async ({ page }) => {
    await mount(page, SYS, "/v2/admin/accounts", () => undefined);
    await page.route(/\/api\/admin\/accounts\/99\/system-admin$/, (route) => json(route, 409, { detail: "마지막 시스템 관리자는 해제할 수 없습니다." }));
    await page.getByRole("button", { name: "시스템관리 시스템 관리자 해제" }).click();
    const dialog = dialogNamed(page, "시스템 관리자 해제");
    await dialog.getByRole("button", { name: "시스템 관리자 해제하기" }).click();
    await expect(dialog.getByRole("alert")).toContainText("마지막 시스템 관리자는 해제할 수 없습니다.");
  });

  test("자기 자신 비활성화 거부 문구를 그대로 보인다", async ({ page }) => {
    await mount(page, SYS, "/v2/admin/accounts");
    await page.route(/\/api\/admin\/accounts\/99$/, (route) => json(route, 409, { detail: "자기 자신의 계정은 비활성화할 수 없습니다." }));
    await page.getByRole("button", { name: "시스템관리 비활성화" }).click();
    const dialog = dialogNamed(page, "계정 비활성화");
    await dialog.getByRole("button", { name: "비활성화하기" }).click();
    await expect(dialog.getByRole("alert")).toContainText("자기 자신의 계정은 비활성화할 수 없습니다.");
  });

  test("비밀번호 초기화는 요청 본문에만 비밀번호를 담고 화면에 남기지 않는다", async ({ page }) => {
    const mock = await mount(page, SYS, "/v2/admin/accounts");
    await page.getByRole("button", { name: "김담당 비밀번호 초기화" }).click();
    const dialog = dialogNamed(page, "비밀번호 초기화");
    await dialog.getByLabel(/^새 비밀번호(?! 확인)/).fill("Reset-Pass-456");
    await dialog.getByLabel("새 비밀번호 확인").fill("Reset-Pass-456");
    await dialog.getByRole("button", { name: "초기화" }).click();
    await expect(page.getByRole("status").filter({ hasText: "비밀번호를 초기화했습니다" })).toBeVisible();
    expect(mock.calls.some((c) => c.path.endsWith("/password-reset") && JSON.stringify(c.body).includes("Reset-Pass-456"))).toBe(true);
    await expect(page.locator("body")).not.toContainText("Reset-Pass-456");
  });
});

test.describe("부서 관리", () => {
  const DEPTS = [
    { id: 1, name: "본부", parentId: null, kind: "normal", head: { accountId: 2, name: "박관리", rank: "manager", role: "head", isActive: true }, members: [{ accountId: 2, name: "박관리", rank: "manager", role: "head", isActive: true }, { accountId: 1, name: "김담당", rank: "staff", role: "member", isActive: true }] },
    { id: 2, name: "개발팀", parentId: 1, kind: "normal", head: null, members: [] },
  ];

  async function mountDepartments(page: Page) {
    const calls: { method: string; path: string; body: unknown }[] = [];
    await page.route("**/api/auth/me", (route) => json(route, 200, SYS));
    await page.route(/\/api\/admin\/departments(\/.*)?$/, (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() !== "GET") calls.push({ method: request.method(), path, body: request.postDataJSON?.() ?? null });
      if (path.endsWith("/head")) return json(route, 409, { detail: "부서장은 관리자 이상 사용권한 계정만 될 수 있습니다." });
      if (request.method() === "GET") return json(route, 200, DEPTS);
      return json(route, 200, DEPTS[0]);
    });
    await page.route(/\/api\/admin\/accounts(\?.*)?$/, (route) => json(route, 200, { items: [acct(5, { name: "신규직원" })], total: 1, page: 1, size: 20 }));
    await page.goto("/v2/login");
    await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
    await page.goto("/v2/admin/departments");
    return calls;
  }

  test("트리 표시·삭제 버튼 없음·이름 변경·구성원 추가·부서장 거부 문구", async ({ page }) => {
    const calls = await mountDepartments(page);
    await expect(page.getByRole("heading", { name: "본부", level: 2 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "개발팀", level: 2 })).toBeVisible();
    await expect(page.getByRole("button", { name: /삭제/ })).toHaveCount(0);
    await page.getByRole("button", { name: "본부 이름 변경" }).click();
    const rename = dialogNamed(page, "부서 이름 변경");
    await rename.getByLabel("부서 이름").fill("사업본부");
    await rename.getByRole("button", { name: "변경" }).click();
    await expect(page.getByRole("status").filter({ hasText: "부서 이름을 변경했습니다" })).toBeVisible();
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ name: "사업본부" });
    // 구성원 추가
    await page.getByRole("button", { name: "본부 구성원 추가" }).click();
    const add = dialogNamed(page, "구성원 추가");
    await add.getByRole("button", { name: "검색" }).click();
    await add.getByRole("button", { name: /신규직원/ }).click();
    await add.getByRole("button", { name: "추가" }).click();
    await expect(page.getByRole("status").filter({ hasText: "부서에 추가했습니다" })).toBeVisible();
    // 부서장 지정 거부 문구
    await page.getByRole("button", { name: "김담당 부서장 지정" }).click();
    const head = dialogNamed(page, "부서장 지정");
    await head.getByRole("button", { name: "부서장으로 지정하기" }).click();
    await expect(head.getByRole("alert")).toContainText("관리자 이상 사용권한 계정만");
  });
});

test.describe("시스템 설정", () => {
  async function mountSettings(page: Page, enforced = true) {
    const puts: { key: string; body: { value: boolean; reason: string } }[] = [];
    await page.route("**/api/auth/me", (route) => json(route, 200, SYS));
    await page.route(/\/api\/admin\/settings(\/.*)?$/, (route) => {
      const request = route.request();
      if (request.method() === "PUT") {
        const key = new URL(request.url()).pathname.split("/").pop() as string;
        puts.push({ key, body: request.postDataJSON() });
        return json(route, 200, { key, value: true, source: "admin", updatedAt: "2026-10-12T00:00:00Z", updatedBy: { id: 99, name: "시스템관리" } });
      }
      return json(route, 200, baseSettings(enforced));
    });
    await page.goto("/v2/login");
    await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
    await page.goto("/v2/admin/settings");
    return puts;
  }

  test("두 스위치와 값의 출처를 보여 준다", async ({ page }) => {
    await mountSettings(page);
    const list = page.getByRole("list", { name: "시스템 설정 목록" });
    await expect(list.getByRole("listitem")).toHaveCount(2);
    await expect(list).toContainText("외부 AI(LLM) 사용 허용");
    await expect(list).toContainText("기본값(설정 없음)");
    await expect(list).toContainText(".env 설정값");
    await expect(list).toContainText("허용 안 함");
  });

  test("켤 때는 경고 확인 → 사유 필수 → 변경", async ({ page }) => {
    const puts = await mountSettings(page);
    await page.getByRole("button", { name: "외부 AI(LLM) 사용 허용 허용", exact: true }).click();
    const warn = page.getByRole("alertdialog", { name: "외부 전송 허용" });
    await expect(warn).toContainText("외부 AI 서비스로 전송");
    await warn.getByRole("button", { name: "외부 전송 허용으로 계속" }).click();
    const reason = page.getByRole("alertdialog", { name: "외부 연동 허용" });
    const confirm = reason.getByRole("button", { name: "허용하기" });
    await expect(confirm).toBeDisabled(); // 사유가 비어 있으면 못 보낸다
    await reason.getByLabel(/사유/).fill("고객사 협의 완료");
    await confirm.click();
    await expect(page.getByRole("status").filter({ hasText: "허용으로 변경했습니다" })).toBeVisible();
    expect(puts).toEqual([{ key: "external_llm_enabled", body: { value: true, reason: "고객사 협의 완료" } }]);
  });

  test("정책 적용 스위치가 꺼져 있으면 안내한다", async ({ page }) => {
    await mountSettings(page, false);
    await expect(page.getByRole("status").filter({ hasText: "EXTERNAL_POLICY_ENFORCED" })).toBeVisible();
  });
});

test.describe("라이선스", () => {
  test("상태를 표시만 한다", async ({ page }) => {
    await page.route("**/api/auth/me", (route) => json(route, 200, SYS));
    await page.route("**/api/system/license", (route) =>
      json(route, 200, {
        status: "expiring_soon", licensee: "예시 고객사", maxAccounts: 50, activeAccounts: 52, overLimit: true,
        expiresAt: "2026-11-01T00:00:00Z", daysLeft: 20, hardwareMatch: null, message: "라이선스 만료가 가깝습니다.",
        warnings: ["허용 계정 수(50)를 초과했습니다(현재 활성 계정 52)."],
      }),
    );
    await page.goto("/v2/login");
    await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
    await page.goto("/v2/admin/license");
    const card = page.getByRole("region", { name: "라이선스 상태" });
    await expect(card).toContainText("만료 임박");
    await expect(card).toContainText("예시 고객사");
    await expect(card).toContainText("허용 수 초과");
    await expect(page.getByRole("region", { name: "라이선스 경고" })).toContainText("초과했습니다");
    await expect(page.getByRole("main").getByRole("button")).toHaveCount(0); // 본문에는 변경 버튼이 없다(표시만)
  });
});

test.describe("반응형", () => {
  for (const width of [375, 768, 1280]) {
    test(`${width}px 에서 가로 넘침이 없다`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await mount(page, SYS, "/v2/admin/accounts", (mock) => {
        mock.accounts.push(acct(77, { name: "아주 긴 이름을 가진 사용자입니다 가나다라마바사", loginId: "a-very-long-login-id-without-spaces-1234567890", email: "very.long.email.address.for.overflow.check@example.com" }));
      });
      await expect(page.getByRole("heading", { level: 1, name: "시스템 관리" })).toBeVisible();
      await expect(page.getByText("아주 긴 이름을 가진 사용자입니다").filter({ visible: true }).first()).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
    });
  }
});

test.describe("본인 비밀번호 변경", () => {
  async function open(page: Page, status: number, body: unknown) {
    const seen: unknown[] = [];
    await page.route("**/api/auth/me", (route) => json(route, 200, NORMAL));
    await page.route("**/api/auth/change-password", (route) => {
      seen.push(route.request().postDataJSON());
      return status === 204 ? route.fulfill({ status: 204 }) : json(route, status, body);
    });
    await page.goto("/v2/login");
    await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
    await page.goto("/v2");
    await page.getByRole("button", { name: "비밀번호 변경" }).first().click();
    return { seen, dialog: dialogNamed(page, "비밀번호 변경") };
  }

  test("성공하면 닫히고 안내가 보인다", async ({ page }) => {
    const { seen, dialog } = await open(page, 204, null);
    await dialog.getByLabel("현재 비밀번호").fill("Old-Pass-12345");
    await dialog.getByLabel(/^새 비밀번호(?! 확인)/).fill("New-Pass-12345");
    await dialog.getByLabel("새 비밀번호 확인").fill("New-Pass-12345");
    await dialog.getByRole("button", { name: "변경" }).click();
    await expect(page.getByRole("status").filter({ hasText: "비밀번호를 변경했습니다" })).toBeVisible();
    expect(seen).toEqual([{ currentPassword: "Old-Pass-12345", newPassword: "New-Pass-12345" }]);
    await expect(page.locator("body")).not.toContainText("New-Pass-12345");
  });

  test("400 은 현재 비밀번호 불일치 문구", async ({ page }) => {
    const { dialog } = await open(page, 400, { detail: "비밀번호를 변경하지 못했습니다." });
    await dialog.getByLabel("현재 비밀번호").fill("wrong-wrong-1");
    await dialog.getByLabel(/^새 비밀번호(?! 확인)/).fill("New-Pass-12345");
    await dialog.getByLabel("새 비밀번호 확인").fill("New-Pass-12345");
    await dialog.getByRole("button", { name: "변경" }).click();
    await expect(dialog.getByRole("alert")).toContainText("현재 비밀번호가 올바르지 않습니다");
  });

  test("422 는 정책 위반 문구(최소 길이 안내)", async ({ page }) => {
    const { dialog } = await open(page, 422, { detail: "비밀번호는 10자 이상이어야 합니다." });
    await dialog.getByLabel("현재 비밀번호").fill("Old-Pass-12345");
    await dialog.getByLabel(/^새 비밀번호(?! 확인)/).fill("short");
    await dialog.getByLabel("새 비밀번호 확인").fill("short");
    await dialog.getByRole("button", { name: "변경" }).click();
    await expect(dialog.getByRole("alert")).toContainText("정책에 맞지 않습니다");
    await expect(dialog.getByRole("alert")).toContainText("10자 이상");
  });

  test("새 비밀번호 확인이 다르면 보내지 않는다", async ({ page }) => {
    const { seen, dialog } = await open(page, 204, null);
    await dialog.getByLabel("현재 비밀번호").fill("Old-Pass-12345");
    await dialog.getByLabel(/^새 비밀번호(?! 확인)/).fill("New-Pass-12345");
    await dialog.getByLabel("새 비밀번호 확인").fill("New-Pass-99999");
    await dialog.getByRole("button", { name: "변경" }).click();
    await expect(dialog.getByRole("alert")).toContainText("서로 다릅니다");
    expect(seen).toHaveLength(0);
  });
});

test.describe("표시 보완", () => {
  const meeting = (over: Record<string, unknown>) => ({
    id: 41, title: "주간 회의", heldAt: "2026-10-01T01:00:00Z", summary: "", decisions: [], status: "awaiting_confirmation",
    confirmKind: null, confirmedBy: null, confirmedAt: null, firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: null,
    registeredBy: { id: 3, name: "김대리" }, origin: "audio", participants: [], actionItems: [], recentEvents: [], allowedActions: [], ...over,
  });

  async function openMeeting(page: Page, detail: Record<string, unknown>) {
    await page.route("**/api/auth/me", (route) => json(route, 200, NORMAL));
    await page.route(/\/api\/meetings\/41$/, (route) => json(route, 200, detail));
    await page.goto("/v2/login");
    await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
    await page.goto("/v2/meetings/41");
    await expect(page.getByRole("heading", { level: 1, name: "주간 회의" })).toBeVisible();
  }

  test("processingWarnings 가 있으면 안내와 목록을 보인다", async ({ page }) => {
    await openMeeting(page, meeting({ processingWarnings: ["구간 2/3 실패: LLM 호출 실패(LocalLlmError)"] }));
    const region = page.getByRole("region", { name: "처리 경고" });
    await expect(region).toContainText("일부 구간 처리 실패");
    await expect(region).toContainText("구간 2/3 실패");
  });

  test("processingWarnings 가 비었거나 없으면 아무것도 보이지 않는다", async ({ page }) => {
    await openMeeting(page, meeting({ processingWarnings: [] }));
    await expect(page.getByRole("region", { name: "처리 경고" })).toHaveCount(0);
    await page.unrouteAll({ behavior: "ignoreErrors" });
    const noField = await page.context().newPage();
    await noField.route("**/api/auth/me", (route) => json(route, 200, NORMAL));
    await noField.route(/\/api\/meetings\/41$/, (route) => json(route, 200, meeting({})));
    await noField.goto("/v2/login");
    await noField.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
    await noField.goto("/v2/meetings/41");
    await expect(noField.getByRole("heading", { level: 1, name: "주간 회의" })).toBeVisible();
    await expect(noField.getByRole("region", { name: "처리 경고" })).toHaveCount(0);
  });

  const CODES: [string, RegExp][] = [
    ["llm_unavailable", /로컬 AI 서버에 연결하지 못했/],
    ["llm_error", /시스템 관리자에게 모델·주소 설정 확인/],
    ["llm_host_blocked", /주소가 허용되지 않습니다/],
    ["external_disabled", /외부 AI 사용이 허용되지 않아/],
    ["worker_stopped", /처리 프로그램이 멈춰/],
    ["extraction_invalid", /업무 추출 결과의 형식이 올바르지 않았습니다/],
  ];
  for (const [code, text] of CODES) {
    test(`실패 코드 ${code} 문구`, async ({ page }) => {
      await openMeeting(page, meeting({ status: "failed", processing: { status: "failed", errorCode: code, startedAt: null, finishedAt: null } }));
      const banner = page.getByRole("alert", { name: "처리 상태" });
      await expect(banner).toContainText(text);
      await expect(banner).toContainText(code);
    });
  }
});

test.describe("승인 대기 패널", () => {
  test("내가 승인할 프로젝트와 수정 요청을 오른쪽 패널에 보인다", async ({ page }) => {
    await page.route("**/api/auth/me", (route) => json(route, 200, NORMAL));
    await page.route("**/api/projects", (route) =>
      json(route, 200, [
        { id: 5, name: "신제품 도입", description: "", status: "pending_approval", departmentId: 1, departmentName: "개발팀", registeredBy: { id: 3, name: "김대리" }, lead: null, approver: { id: 7, name: "한팀장" }, myRole: null, memberCount: 2, createdAt: "2026-10-01T00:00:00Z", decidedAt: null },
        { id: 6, name: "남의 승인", description: "", status: "pending_approval", departmentId: 1, departmentName: "개발팀", registeredBy: { id: 3, name: "김대리" }, lead: null, approver: { id: 8, name: "타인" }, myRole: null, memberCount: 1, createdAt: "2026-10-01T00:00:00Z", decidedAt: null },
      ]),
    );
    await page.route("**/api/me/todos", (route) =>
      json(route, 200, {
        awaitingConfirmMeetings: { total: 0, items: [] }, needsCompletionItems: { total: 0, items: [] }, myItems: { total: 0, items: [] },
        unreadAutoConfirmed: { total: 0, items: [] },
        pendingChangeRequests: { total: 1, items: [{ requestId: 9, meetingId: 41, meetingTitle: "주간 회의", itemId: 1, itemTitle: "견적서 송부", requester: { id: 3, name: "김대리" }, createdAt: "2026-10-01T00:00:00Z", commentPreview: "", kind: "edit" }] },
      }),
    );
    await page.goto("/v2/login");
    await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
    await page.goto("/v2");
    const panel = page.getByRole("region", { name: "승인 대기" });
    await expect(panel).toContainText("2");
    await expect(panel.getByRole("link", { name: "프로젝트 승인 대기: 신제품 도입" })).toHaveAttribute("href", "/v2/projects/5");
    await expect(panel.getByRole("link", { name: /남의 승인/ })).toHaveCount(0);
    await expect(panel.getByRole("link", { name: "수정 요청 처리 대기: 주간 회의" })).toHaveAttribute("href", "/v2/meetings/41");
  });

  test("대기 건이 없으면 패널이 보이지 않는다", async ({ page }) => {
    await page.route("**/api/auth/me", (route) => json(route, 200, NORMAL));
    await page.goto("/v2/login");
    await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
    await page.goto("/v2");
    await expect(page.getByRole("navigation", { name: "주 메뉴" })).toBeVisible();
    await expect(page.getByRole("region", { name: "승인 대기" })).toHaveCount(0);
  });
});
