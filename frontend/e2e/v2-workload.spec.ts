/*
 * v2 업무 현황(/v2/workload) E2E(작업 69-3): 메뉴, KPI, 스택 바, 과다 배지, 집중 관리 목록, 기준일 안내, 범위별 화면, 상태, 반응형, 두 테마.
 * 실제 백엔드 없이 page.route 로 GET /api/workload 를 가로챈다(데이터는 모두 가상).
 */
import { expect, test, type Page, type Route } from "./helpers/test";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const json = (route: Route, status: number, body: unknown) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const member = (accountId: number, name: string, completed: number, inProgress: number, overdue: number, overloaded = false) => ({
  accountId, name, completed, inProgress, overdue, open: inProgress + overdue, overloaded,
});
const urgent = (itemId: number, title: string, dueDate: string, kind: "overdue" | "due_soon", days: number, meetingId: number, assignee = "김담당") => ({
  itemId, title, dueDate, kind, days, meetingId, meetingTitle: `회의 ${meetingId}`, assignee: { id: 9, name: assignee },
});

const DEV = { id: 1, name: "개발팀" };
const SALES = { id: 2, name: "영업팀" };

const base = (over: Record<string, unknown> = {}) => ({
  snapshotDate: "2026-10-06",
  generatedAt: "2026-10-07T00:10:00Z",
  scope: { kind: "company", departments: [DEV, SALES], department: null },
  kpis: { assigned: 20, completionRate: 35, overdue: 7, avgOpenPerPerson: 2.7, memberCount: 4 },
  members: [
    member(1, "한과다", 1, 2, 4, true),
    member(2, "김담당", 3, 2, 1),
    member(3, "무업무", 0, 0, 0),
    member(4, "이완료", 3, 0, 0),
  ],
  urgentItems: [
    urgent(11, "견적서 송부", "2026-10-01", "overdue", 5, 41),
    urgent(12, "설비 점검", "2026-10-04", "overdue", 2, 42),
    urgent(13, "납품 일정 확인", "2026-10-07", "due_soon", 1, 43),
    urgent(14, "교육 자료 정리", "2026-10-09", "due_soon", 3, 44),
  ],
  ...over,
});

type Responder = (departmentId: string | null) => { status: number; body: unknown };

async function mount(page: Page, responder: Responder, rank: "executive" | "manager" | "staff" = "executive", path = "/v2/workload") {
  const requests: (string | null)[] = [];
  await page.route("**/api/**", (route) => json(route, 200, []));
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}`
      ? json(route, 200, { id: 7, name: "한팀장", rank, tenantId: 1 })
      : json(route, 401, { detail: "x" }),
  );
  await page.route(/\/api\/workload(\?.*)?$/, (route) => {
    const departmentId = new URL(route.request().url()).searchParams.get("departmentId");
    requests.push(departmentId);
    const { status, body } = responder(departmentId);
    return json(route, status, body);
  });
  await page.goto("/v2/login");
  await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto(path);
  return requests;
}

const ok = (body: unknown): Responder => () => ({ status: 200, body });
const heading = (page: Page) => page.getByRole("heading", { level: 1, name: "업무 현황" });
const memberRow = (page: Page, name: string) => page.getByRole("region", { name: "구성원별 업무 현황" }).locator("li", { hasText: name }).first();

test.describe("메뉴", () => {
  test("할 일 아래에 '업무 현황', 이동하면 현재 위치 표시", async ({ page }) => {
    await mount(page, ok(base()));
    await expect(heading(page)).toBeVisible();
    const nav = page.getByRole("navigation", { name: "주 메뉴" });
    await expect(nav.getByRole("link")).toHaveText(["할 일", "업무 현황", "회의록", "프로젝트"]);
    await expect(nav.getByRole("link", { name: "업무 현황" })).toHaveAttribute("aria-current", "page");
    await expect(nav.getByRole("link", { name: "할 일" })).not.toHaveAttribute("aria-current", "page");
    await nav.getByRole("link", { name: "프로젝트" }).click();
    await expect(page).toHaveURL(/\/v2\/projects$/);
    await page.getByRole("navigation", { name: "주 메뉴" }).getByRole("link", { name: "업무 현황" }).click();
    await expect(page).toHaveURL(/\/v2\/workload$/);
    await expect(heading(page)).toBeVisible();
  });

  test("모바일 드로어에도 있다", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await mount(page, ok(base()));
    await expect(heading(page)).toBeVisible();
    await page.getByRole("button", { name: "메뉴 열기" }).click();
    await expect(page.getByRole("navigation", { name: "주 메뉴" }).getByRole("link", { name: "업무 현황" })).toHaveAttribute("aria-current", "page");
  });
});

test.describe("화면 구성", () => {
  test("KPI 4개", async ({ page }) => {
    await mount(page, ok(base()));
    await expect(heading(page)).toBeVisible();
    const kpi = page.getByRole("region", { name: "요약" });
    await expect(kpi).toContainText("총 배정 업무");
    await expect(kpi.locator("div", { hasText: /^총 배정 업무20$/ })).toBeVisible();
    await expect(kpi.locator("div", { hasText: /^완료율35%$/ })).toBeVisible();
    await expect(kpi.locator("div", { hasText: /^지연 건수7$/ })).toBeVisible();
    await expect(kpi.locator("div", { hasText: /^1인당 평균 잔여 업무2\.7/ })).toBeVisible();
  });

  test("완료율이 null 이면 대시(—)", async ({ page }) => {
    await mount(page, ok(base({ kpis: { assigned: 0, completionRate: null, overdue: 0, avgOpenPerPerson: 0, memberCount: 1 } })));
    await expect(page.getByRole("region", { name: "요약" }).locator("div", { hasText: /^완료율—$/ })).toBeVisible();
  });

  test("스택 바: 글자 라벨·건수, 스크린리더 대체 텍스트, 과다 배지, 서버 순서(미완료 많은 순)", async ({ page }) => {
    await mount(page, ok(base()));
    await expect(heading(page)).toBeVisible();
    await expect(memberRow(page, "한과다")).toBeVisible();
    const names = await page.getByRole("region", { name: "구성원별 업무 현황" }).locator("li > div:first-child > span:first-child").allTextContents();
    expect(names).toEqual(["한과다", "김담당", "무업무", "이완료"]);
    const over = memberRow(page, "한과다");
    await expect(over.getByRole("img", { name: "한과다: 완료 1건, 진행 중 2건, 지연 4건" })).toBeVisible();
    await expect(over).toContainText("완료 1건");
    await expect(over).toContainText("진행 중 2건");
    await expect(over).toContainText("지연 4건");
    await expect(over.getByText("미완료 과다")).toBeVisible();
    await expect(memberRow(page, "김담당").getByText("미완료 과다")).toHaveCount(0);
    // 업무 0건: 빈 바와 "업무 없음"
    const idle = memberRow(page, "무업무");
    await expect(idle).toContainText("업무 없음");
    await expect(idle.getByRole("img", { name: "무업무: 업무 없음" })).toBeVisible();
    // 구간 폭은 건수 비율(한과다: 1 : 2 : 4)
    const widths = await over.getByRole("img").locator("span").evaluateAll((els) => els.map((el) => (el as HTMLElement).style.width));
    expect(widths.map((w) => Math.round(parseFloat(w)))).toEqual([14, 29, 57]);
  });

  test("집중 관리: 지연 먼저·기한 오래된 순, 라벨, 링크와 접근성 이름", async ({ page }) => {
    await mount(page, ok(base()));
    const section = page.getByRole("region", { name: "집중 관리" });
    const links = section.getByRole("link");
    await expect(links).toHaveCount(4);
    const labels = await links.evaluateAll((els) => els.map((el) => el.getAttribute("aria-label")));
    expect(labels).toEqual([
      "견적서 송부 업무, 담당 김담당, 기한 2026-10-01, 지연 5일, 회의록 회의 41",
      "설비 점검 업무, 담당 김담당, 기한 2026-10-04, 지연 2일, 회의록 회의 42",
      "납품 일정 확인 업무, 담당 김담당, 기한 2026-10-07, D-1, 회의록 회의 43",
      "교육 자료 정리 업무, 담당 김담당, 기한 2026-10-09, D-3, 회의록 회의 44",
    ]);
    expect(new Set(labels).size).toBe(4);
    await expect(section).toContainText("지연 5일");
    await expect(section).toContainText("D-3");
    await links.nth(1).click();
    await expect(page).toHaveURL(/\/v2\/meetings\/42$/, { timeout: 30_000 });
  });

  test("집중 관리가 비어 있으면 안내", async ({ page }) => {
    await mount(page, ok(base({ urgentItems: [] })));
    await expect(page.getByRole("region", { name: "집중 관리" })).toContainText("지연·임박 업무가 없습니다");
  });

  test("하단 안내: 날짜 YY년 MM월 DD일과 범위·한정 문구", async ({ page }) => {
    await mount(page, ok(base()));
    const note = page.getByRole("contentinfo", { name: "통계 기준 안내" }).or(page.locator("footer[aria-label='통계 기준 안내']"));
    await expect(note).toContainText("전일(26년 10월 06일 기준) 통계 실적입니다.");
    for (const phrase of ["담당자가 계정으로 확정된 업무만 집계", "직권 종료·삭제·보류 회의록·대체된 업무는 제외", "종결 구분 도입 전에 종결된 업무는 완료로 집계하지 않습니다", "지연은 기한 다음 날부터"]) {
      await expect(note).toContainText(phrase);
    }
    await expect(note.locator("ul, ol")).toHaveCount(0); // 목록 없이 짧은 문장
  });

  test("날짜가 매일 바뀐다(기준일이 다르면 안내 날짜도 다르다)", async ({ page }) => {
    await mount(page, ok(base({ snapshotDate: "2027-01-02" })));
    await expect(page.getByText("전일(27년 01월 02일 기준) 통계 실적입니다.")).toBeVisible();
  });

  test("집계 전(snapshotDate null): 안내만, KPI·바·하단 안내 없음", async ({ page }) => {
    await mount(page, ok({ snapshotDate: null, generatedAt: null, scope: { kind: "company", departments: [DEV], department: null },
      kpis: { assigned: 0, completionRate: null, overdue: 0, avgOpenPerPerson: 0, memberCount: 0 }, members: [], urgentItems: [] }));
    await expect(page.getByText("아직 집계된 통계가 없습니다. 집계는 매일 새벽에 갱신됩니다.")).toBeVisible();
    await expect(page.getByRole("region", { name: "요약" })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "구성원별 업무 현황" })).toHaveCount(0);
    await expect(page.getByText("통계 실적입니다")).toHaveCount(0);
  });
});

test.describe("범위별 화면", () => {
  test("전사(지시자): 부서 선택 기본 전사, 선택하면 departmentId 로 다시 조회", async ({ page }) => {
    const requests = await mount(page, (id) =>
      ({ status: 200, body: id === null ? base() : base({ scope: { kind: "department", departments: [DEV, SALES], department: SALES }, members: [member(5, "정영업", 1, 1, 0)] }) }));
    const select = page.getByLabel("부서");
    await expect(select).toHaveValue("");
    await expect(select.locator("option")).toHaveText(["전사", "개발팀", "영업팀"]);
    await select.selectOption("2");
    await expect(memberRow(page, "정영업")).toBeVisible();
    expect(requests.at(-1)).toBe("2");
    await expect(select).toHaveValue("2");
    // 다시 전사로
    await select.selectOption("");
    await expect(memberRow(page, "한과다")).toBeVisible();
    expect(requests.at(-1)).toBeNull();
    expect(requests).toContain("2");
  });

  test("부서장 여러 부서: 서버 기본 부서가 선택돼 있고 바꾸면 다시 조회", async ({ page }) => {
    const scope = (department: typeof DEV) => ({ kind: "department", departments: [DEV, SALES], department });
    const requests = await mount(page, (id) => ({ status: 200, body: base({ scope: scope(id === "2" ? SALES : DEV), members: [member(5, id === "2" ? "정영업" : "박개발", 0, 1, 0)] }) }), "manager");
    const select = page.getByLabel("부서");
    await expect(select).toHaveValue("1");
    await expect(select.locator("option")).toHaveText(["개발팀", "영업팀"]);
    await expect(memberRow(page, "박개발")).toBeVisible();
    await select.selectOption("2");
    await expect(memberRow(page, "정영업")).toBeVisible();
    expect(requests.at(-1)).toBe("2");
  });

  test("부서장 한 부서: 선택 UI 없음", async ({ page }) => {
    await mount(page, ok(base({ scope: { kind: "department", departments: [DEV], department: DEV } })), "manager");
    await expect(memberRow(page, "한과다")).toBeVisible();
    await expect(page.getByLabel("부서")).toHaveCount(0);
  });

  test("개인 범위: 선택 UI 없이 '내 현황'", async ({ page }) => {
    const requests = await mount(page, ok(base({
      scope: { kind: "self", departments: [], department: null },
      kpis: { assigned: 6, completionRate: 50, overdue: 1, avgOpenPerPerson: 3, memberCount: 1 },
      members: [member(7, "한팀장", 3, 2, 1)],
    })), "staff");
    await expect(page.getByText("내 현황")).toBeVisible();
    await expect(page.getByLabel("부서")).toHaveCount(0);
    await expect(memberRow(page, "한팀장")).toBeVisible();
    expect(new Set(requests)).toEqual(new Set([null])); // 부서 지정 요청 없음
  });
});

test.describe("로딩·오류·403", () => {
  test("로딩 표시 후 내용", async ({ page }) => {
    await mount(page, ok(base()));
    await expect(heading(page)).toBeVisible();
    await expect(page.getByRole("region", { name: "요약" })).toBeVisible();
    await expect(page.getByText("현황을 불러오는 중…")).toHaveCount(0);
  });

  test("서버 오류 → 알림과 다시 시도", async ({ page }) => {
    let failing = true;
    await mount(page, () => (failing ? { status: 500, body: { detail: "서버 오류" } } : { status: 200, body: base() }));
    const alert = page.getByRole("alert").filter({ hasText: "현황을 불러오지 못했습니다" });
    await expect(alert).toContainText("서버 오류");
    failing = false;
    await alert.getByRole("button", { name: "다시 시도" }).click();
    await expect(page.getByRole("region", { name: "요약" })).toBeVisible();
    await expect(page.getByRole("alert").filter({ hasText: "현황을 불러오지 못했습니다" })).toHaveCount(0);
  });

  test("403 → 권한 안내(재시도 버튼 없음)", async ({ page }) => {
    await mount(page, () => ({ status: 403, body: { detail: "본인이 부서장인 부서만 볼 수 있습니다" } }));
    const alert = page.getByRole("alert").filter({ hasText: "볼 수 없습니다" });
    await expect(alert).toContainText("본인이 부서장인 부서만 볼 수 있습니다");
    await expect(alert.getByRole("button")).toHaveCount(0);
  });
});

test.describe("반응형", () => {
  for (const width of [1280, 768, 375]) {
    test(`${width}px: 가로 넘침 없음`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await mount(page, ok(base({ members: [member(1, "아주아주아주긴이름을가진구성원", 12, 34, 56, true), ...base().members] })));
      await expect(page.getByRole("region", { name: "구성원별 업무 현황" })).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
    });
  }

  test("375px: 카드로 쌓이고(이름 아래에 바), 1280px: 이름 옆에 바", async ({ page }) => {
    const positions = async () =>
      page.evaluate(() => {
        const row = document.querySelector("section[aria-label='구성원별 업무 현황'] li")!;
        const name = (row.querySelector("span") as HTMLElement).getBoundingClientRect();
        const bar = (row.querySelector("[role='img']") as HTMLElement).getBoundingClientRect();
        return { nameBottom: name.bottom, barTop: bar.top, nameRight: name.right, barLeft: bar.left };
      });
    await page.setViewportSize({ width: 375, height: 800 });
    await mount(page, ok(base()));
    await expect(page.getByRole("region", { name: "구성원별 업무 현황" })).toBeVisible();
    const mobile = await positions();
    expect(mobile.barTop).toBeGreaterThanOrEqual(mobile.nameBottom);
    await page.setViewportSize({ width: 1280, height: 900 });
    const desktop = await positions();
    expect(desktop.barLeft).toBeGreaterThanOrEqual(desktop.nameRight);
    expect(desktop.barTop).toBeLessThan(desktop.nameBottom + 30);
  });
});

for (const theme of ["light", "dark"] as const) {
  test(`${theme} 테마: 콘솔 오류 없음, 안내·배지 대비 4.5:1 이상`, async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    page.on("pageerror", (e) => errors.push(e.message));
    await page.addInitScript((t) => window.localStorage.setItem("mi.v2.theme", t), theme);
    await mount(page, ok(base()));
    await expect(page.getByRole("region", { name: "요약" })).toBeVisible();
    const ratios = await page.evaluate(() => {
      const parse = (c: string) => (c.match(/[\d.]+/g) ?? []).slice(0, 4).map(Number);
      const lum = ([r, g, b]: number[]) => {
        const f = (v: number) => ((v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
      };
      const ratio = (el: Element) => {
        let bg = [0, 0, 0, 0];
        for (let n: Element | null = el; n; n = n.parentElement) {
          const c = parse(getComputedStyle(n).backgroundColor);
          if (c.length === 3 || (c.length === 4 && c[3] > 0)) {
            bg = c;
            break;
          }
        }
        const a = lum(parse(getComputedStyle(el).color));
        const b = lum(bg);
        return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      };
      const targets = [
        ...document.querySelectorAll("footer[aria-label='통계 기준 안내'] p"),
        ...document.querySelectorAll(".mn-chip"),
      ];
      return targets.map(ratio);
    });
    expect(ratios.length).toBeGreaterThan(3);
    for (const value of ratios) expect(value).toBeGreaterThanOrEqual(4.5);
    expect(errors).toEqual([]);
  });
}
