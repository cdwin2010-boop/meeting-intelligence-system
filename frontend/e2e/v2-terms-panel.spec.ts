/*
 * v2 용어 정리("직급"→"사용권한")와 접힌 패널 잔여 글자 숨김 E2E(작업 71-1 파트 A).
 * 실제 백엔드 없이 page.route 로 응답한다(데이터는 모두 가상).
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withProjectActions } from "./helpers/project-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const json = (route: Route, status: number, body: unknown) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
const ME = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const ACCOUNTS = [
  { id: 7, name: "한팀장", rank: "manager" },
  { id: 8, name: "김대리", rank: "staff" },
  { id: 10, name: "박관리", rank: "manager" },
];

async function login(page: Page) {
  await page.route("**/api/**", (route) => json(route, 200, []));
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, ME) : json(route, 401, { detail: "x" }),
  );
  await page.route("**/api/accounts", (route) => json(route, 200, ACCOUNTS));
  await page.goto("/v2/login");
  await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
}

async function openProject(page: Page) {
  await login(page);
  const project = {
    id: 1, name: "신제품 개발", description: "", status: "active", departmentId: 1, departmentName: "개발팀",
    registeredBy: { id: 7, name: "한팀장" }, lead: { id: 7, name: "한팀장" }, approver: null, myRole: "lead", memberCount: 2,
    createdAt: "2026-10-01T01:00:00Z", decidedAt: null,
    members: [
      { accountId: 7, name: "한팀장", rank: "manager", role: "lead" },
      { accountId: 8, name: "김대리", rank: "staff", role: "member" },
    ],
  };
  await page.route(/\/api\/projects\/1$/, (route) => json(route, 200, withProjectActions(project, ME)));
  await page.route(/\/api\/projects\/1\/meetings(\?.*)?$/, (route) => json(route, 200, { items: [], total: 0, page: 1, size: 20, availablePhases: ["active"] }));
  await page.route("**/api/projects/candidates?*", (route) => json(route, 200, { ownDepartment: { departmentId: 1, departmentName: "개발팀", members: [] }, otherDepartments: [], executiveGroup: [] }));
  await page.goto("/v2/projects/1");
  await expect(page.getByRole("heading", { level: 1, name: "신제품 개발" })).toBeVisible();
}

test.describe("용어: 사용권한", () => {
  test("프로젝트 상세 참여자 표 열 이름이 '사용권한'이고 '직급'은 없다", async ({ page }) => {
    await openProject(page);
    const table = page.getByRole("table").filter({ has: page.getByRole("columnheader", { name: "사용권한" }) });
    await expect(table.getByRole("columnheader")).toHaveText(["이름", "사용권한", "역할", "동작"]);
    await expect(page.getByRole("columnheader", { name: "직급" })).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText("직급");
  });

  test("참여자 추가·총괄 변경 팝업 안내문이 '사용권한'을 쓴다", async ({ page }) => {
    await openProject(page);
    await page.getByRole("button", { name: "참여자 추가" }).click();
    const add = page.getByRole("alertdialog").or(page.getByRole("dialog")).first();
    await expect(add).toContainText("관리자 역할은 관리자 이상 사용권한만 맡을 수 있습니다.");
    await expect(add).not.toContainText("직급");
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "총괄 변경" }).click();
    const change = page.getByRole("alertdialog").or(page.getByRole("dialog")).first();
    await expect(change).toContainText("새 총괄은 관리자 이상 사용권한이어야 합니다.");
    await expect(change).not.toContainText("직급");
  });
});

test.describe("접힌 패널 잔여 글자", () => {
  test.use({ viewport: { width: 1280, height: 800 } });
  const aside = (page: Page) => page.locator("aside:not([aria-label])"); // 앱 틀의 오른쪽 패널(화면 안의 다른 aside 는 이름이 있다)
  const toggle = (page: Page, name: "접기" | "펼치기") => page.getByRole("button", { name: `회의록 추적 패널 ${name}` });

  test("접으면 '회의록 추적' 글자와 패널 내용이 화면·접근성 트리에서 사라지고, 펼치면 돌아온다", async ({ page }) => {
    await login(page);
    await page.goto("/v2/upload");
    await expect(page.getByRole("heading", { level: 1, name: "회의록 올리기" })).toBeVisible();
    await expect(aside(page).getByRole("link", { name: "회의록 추적" })).toBeVisible();
    await toggle(page, "접기").click();
    await expect(toggle(page, "펼치기")).toHaveAttribute("aria-expanded", "false");
    // 글자가 DOM 에서 빠지고(화면에 남지 않고), 접근성 트리에도 없다
    await expect(aside(page).getByText("회의록 추적")).toHaveCount(0);
    expect((await aside(page).innerText()).includes("회의록 추적")).toBe(false);
    await expect(page.getByRole("link", { name: "회의록 추적" })).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "주 메뉴" })).toHaveCount(0);
    await expect(aside(page).getByRole("link")).toHaveCount(0);
    // 접힌 띠 안에서 눈에 보이는 글자가 없다(처리 중 건수 표시는 건수가 있을 때만)
    const visibleText = await aside(page).evaluate((el) => {
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      const found: string[] = [];
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const parent = (n as Text).parentElement!;
        const style = getComputedStyle(parent);
        const box = parent.getBoundingClientRect();
        if ((n.textContent ?? "").trim() && style.display !== "none" && style.visibility !== "hidden" && box.width > 0 && box.height > 0 && !parent.closest(".sr-only")) {
          found.push((n.textContent ?? "").trim());
        }
      }
      return found;
    });
    expect(visibleText).toEqual([]);
    // 펼치기 버튼은 헤더에 그대로 있다
    await expect(page.locator("header").getByRole("button", { name: "회의록 추적 패널 펼치기" })).toBeVisible();
    await toggle(page, "펼치기").click();
    await expect(toggle(page, "접기")).toHaveAttribute("aria-expanded", "true");
    await expect(aside(page).getByRole("link", { name: "회의록 추적" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "주 메뉴" })).toBeVisible();
  });

  test("새로고침해도 접힘 상태가 유지되고 글자는 계속 숨겨져 있다", async ({ page }) => {
    await login(page);
    await page.goto("/v2/upload");
    await toggle(page, "접기").click();
    await expect(toggle(page, "펼치기")).toBeVisible();
    await page.reload();
    await expect(toggle(page, "펼치기")).toBeVisible();
    await expect(aside(page).getByText("회의록 추적")).toHaveCount(0);
    await toggle(page, "펼치기").click();
    await page.reload();
    await expect(toggle(page, "접기")).toBeVisible();
    await expect(aside(page).getByRole("link", { name: "회의록 추적" })).toBeVisible();
  });

  test("모바일 드로어는 그대로: 열면 '회의록 추적'이 보인다", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await login(page);
    await page.goto("/v2/upload");
    await page.getByRole("button", { name: "메뉴 열기" }).click();
    await expect(page.getByRole("link", { name: "회의록 추적" })).toBeVisible();
  });
});
