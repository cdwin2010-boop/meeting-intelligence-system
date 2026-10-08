/*
 * v2 새 프로젝트 등록 후 올리기 화면 복귀 E2E(작업 69-2b).
 * 백엔드 없이 page.route 로 /api/auth/me, /api/projects, /api/projects/{id}, /api/meetings/upload 등을 가로챈다.
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withProjectActions } from "./helpers/project-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const THEME_KEY = "mi.v2.theme";
const DRAFT_KEY = "mi.v2.uploadDraft";
const json = (route: Route, status: number, body: unknown) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const AUDIO = { name: "weekly_1001.m4a", mimeType: "audio/mp4", buffer: Buffer.from("fake-audio") };
const ACCOUNTS = [
  { id: 7, name: "한팀장", rank: "manager" },
  { id: 8, name: "김대리", rank: "staff" },
];
const ref = (id: number) => ({ id, name: ACCOUNTS.find((a) => a.id === id)!.name });
const CANDIDATES = {
  ownDepartment: { departmentId: 1, departmentName: "개발팀", members: [{ accountId: 7, name: "한팀장", rank: "manager" }, { accountId: 8, name: "김대리", rank: "staff" }] },
  otherDepartments: [],
  executiveGroup: [],
};

interface Opts {
  role?: "head" | "member";
  detailStatus?: number;
}
interface Fake {
  uploads: string[];
  detailCalls: number;
}

async function setup(page: Page, opts: Opts = {}): Promise<Fake> {
  const role = opts.role ?? "head";
  const me = role === "head" ? ACCOUNTS[0] : ACCOUNTS[1];
  const fake: Fake = { uploads: [], detailCalls: 0 };
  const created: Record<string, unknown>[] = [];
  const mk = (id: number, name: string, status: string) => ({
    id, name, description: "", status, departmentId: 1, departmentName: "개발팀", registeredBy: ref(me.id), lead: status === "active" ? ref(me.id) : null,
    approver: null, myRole: "lead", memberCount: 1, createdAt: "2026-10-01T01:00:00Z", decidedAt: null,
    members: [{ accountId: me.id, name: me.name, rank: me.rank, role: "lead" }],
  });
  await page.route("**/api/**", (route) => json(route, 200, []));
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, { ...me, tenantId: 1 }) : json(route, 401, { detail: "x" }),
  );
  await page.route("**/api/accounts", (route) => json(route, 200, ACCOUNTS));
  await page.route("**/api/me/org", (route) => json(route, 200, [{ departmentId: 1, name: "개발팀", kind: "normal", role }]));
  await page.route("**/api/projects/candidates?*", (route) => json(route, 200, CANDIDATES));
  await page.route("**/api/projects", async (route) => {
    if (route.request().method() === "POST") {
      const input = route.request().postDataJSON() as { name: string };
      const project = mk(100, input.name, role === "head" ? "active" : "pending_approval");
      created.push(project);
      return json(route, 201, withProjectActions(project, { ...me, tenantId: 1 } as never, null));
    }
    return json(route, 200, [mk(2, "기존 프로젝트", "active"), ...created.filter((p) => p.status === "active")]);
  });
  await page.route(/\/api\/projects\/\d+$/, (route) => {
    fake.detailCalls += 1;
    if (opts.detailStatus && opts.detailStatus !== 200) return json(route, opts.detailStatus, { detail: "조회 실패" });
    const project = created[0] ?? mk(2, "기존 프로젝트", "active");
    return json(route, 200, withProjectActions(project as never, { ...me, tenantId: 1 } as never, null));
  });
  await page.route("**/api/meetings/upload", (route) => {
    fake.uploads.push(route.request().postDataBuffer()?.toString("utf-8") ?? "");
    return json(route, 202, { meetingId: 77, jobId: 5 });
  });
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
  return fake;
}

const typeLabel = (page: Page, label: string) =>
  page.getByRole("group", { name: "회의 유형" }).locator("label").filter({ has: page.getByRole("radio", { name: label, exact: true }) });
const newProjectLink = (page: Page) => page.getByRole("link", { name: "새 프로젝트 등록" });
const backLink = (page: Page) => page.getByRole("link", { name: "올리기로 돌아가기" });
const restoreNotice = (page: Page) => page.getByRole("status").filter({ hasText: "입력하던 내용을 복원했습니다. 파일은 다시 선택해 주세요." });

async function fillUpload(page: Page) {
  await page.getByLabel("회의명").fill("설비 점검 주간 회의");
  await page.getByLabel("회의 날짜").fill("2026-10-01");
  await page.getByLabel("회의 시각").fill("13:20");
  await page.locator("label").filter({ hasText: /김대리/ }).click();
  await typeLabel(page, "프로젝트 회의").click();
  await expect(page.locator("#meeting-project")).toBeVisible();
}

async function openUploadAndLeave(page: Page) {
  await page.goto("/v2/upload");
  await expect(page.getByRole("heading", { level: 1, name: "회의록 올리기" })).toBeVisible();
  await page.getByLabel("음성 파일 선택").setInputFiles(AUDIO);
  await fillUpload(page);
  await newProjectLink(page).click();
  await expect(page).toHaveURL(/\/v2\/projects\/new\?returnTo=\/v2\/upload$/);
}

async function createProject(page: Page, name: string) {
  await page.getByLabel("프로젝트 이름").fill(name);
  await page.getByRole("button", { name: "등록하기" }).click();
}

async function expectRestored(page: Page) {
  await expect(page.getByLabel("회의명")).toHaveValue("설비 점검 주간 회의");
  await expect(page.getByLabel("회의 날짜")).toHaveValue("2026-10-01");
  await expect(page.getByLabel("회의 시각")).toHaveValue("13:20");
  await expect(page.getByLabel(/김대리/)).toBeChecked();
  await expect(page.getByRole("radio", { name: "프로젝트 회의", exact: true })).toBeChecked();
  await expect(restoreNotice(page)).toBeVisible();
  await expect(page.getByText("선택한 파일 없음")).toBeVisible();
}

const draftOf = (page: Page) => page.evaluate((key) => window.sessionStorage.getItem(key), DRAFT_KEY);

test.describe("새 프로젝트 등록 링크와 임시 저장", () => {
  test("링크가 returnTo 를 싣고, 클릭하면 입력값이 저장된 채 등록 화면으로 이동(파일·토큰은 저장 안 함)", async ({ page }) => {
    await setup(page);
    await page.goto("/v2/upload");
    await page.getByLabel("음성 파일 선택").setInputFiles(AUDIO);
    await fillUpload(page);
    await expect(newProjectLink(page)).toHaveAttribute("href", "/v2/projects/new?returnTo=/v2/upload");
    await newProjectLink(page).click();
    await expect(page).toHaveURL(/\/v2\/projects\/new\?returnTo=\/v2\/upload$/);
    const draft = JSON.parse((await draftOf(page))!);
    expect(Object.keys(draft).sort()).toEqual(["date", "meetingType", "participantIds", "savedAt", "time", "title"]);
    expect(draft).toMatchObject({ title: "설비 점검 주간 회의", date: "2026-10-01", time: "13:20", participantIds: [8], meetingType: "project" });
    expect(JSON.stringify(draft)).not.toContain(FAKE_TOKEN);
    expect(JSON.stringify(draft)).not.toContain("weekly_1001");
  });

  test("returnTo 가 있으면 '올리기로 돌아가기'가 보이고, 없으면 보이지 않는다", async ({ page }) => {
    await setup(page);
    await page.goto("/v2/projects/new?returnTo=/v2/upload");
    await expect(backLink(page)).toHaveAttribute("href", "/v2/upload?resume=1");
    await page.goto("/v2/projects/new");
    await expect(page.getByLabel("프로젝트 이름")).toBeVisible();
    await expect(backLink(page)).toHaveCount(0);
  });
});

test.describe("복귀와 복원", () => {
  test("활성 프로젝트 등록 → 입력 복원, 파일 비어 있음, 새 프로젝트 자동 선택, 제출 본문에 meetingType·projectId", async ({ page }) => {
    const fake = await setup(page, { role: "head" });
    await openUploadAndLeave(page);
    await createProject(page, "신규 라인 구축");
    await expect(page).toHaveURL(/\/v2\/upload(\?|$)/);
    await expectRestored(page);
    await expect(page.locator("#meeting-project")).toHaveValue("100");
    await expect(page.locator("#meeting-project option:checked")).toHaveText("신규 라인 구축");
    expect(await draftOf(page)).toBeNull();
    // 파일은 다시 선택해야 제출된다
    await page.getByLabel("음성 파일 선택").setInputFiles(AUDIO);
    await page.getByRole("button", { name: "올리고 분석 시작" }).click();
    await expect(page).toHaveURL(/\/v2\/meetings\/77$/);
    expect(fake.uploads[0]).toContain('name="meetingType"');
    expect(fake.uploads[0]).toMatch(/name="meetingType"\r\n\r\nproject/);
    expect(fake.uploads[0]).toMatch(/name="projectId"\r\n\r\n100/);
  });

  test("새로고침하면 resume·newProject 가 주소에서 사라지고 자동 선택은 다시 적용되지 않는다", async ({ page }) => {
    const fake = await setup(page, { role: "head" });
    await openUploadAndLeave(page);
    await createProject(page, "신규 라인 구축");
    await expect(page.locator("#meeting-project")).toHaveValue("100");
    await expect(page).toHaveURL(/\/v2\/upload$/);
    const callsBefore = fake.detailCalls;
    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: "회의록 올리기" })).toBeVisible();
    await expect(page).toHaveURL(/\/v2\/upload$/);
    await expect(restoreNotice(page)).toHaveCount(0);
    await expect(page.getByLabel("회의명")).toHaveValue("");
    await expect(page.getByRole("radio", { name: "프로젝트 회의", exact: true })).not.toBeChecked();
    expect(fake.detailCalls).toBe(callsBefore);
  });

  test("승인 대기 프로젝트 등록 → 선택하지 않고 승인 대기 안내", async ({ page }) => {
    await setup(page, { role: "member" });
    await openUploadAndLeave(page);
    await createProject(page, "대기될 프로젝트");
    await expectRestored(page);
    await expect(page.getByRole("status").filter({ hasText: "새 프로젝트 '대기될 프로젝트'은 승인 대기 중입니다. 부서장 승인 후 선택할 수 있습니다." })).toBeVisible();
    await expect(page.locator("#meeting-project")).toHaveValue("");
  });

  test("'올리기로 돌아가기'로 돌아오면 입력값이 복원된다(프로젝트 생성 없음)", async ({ page }) => {
    const fake = await setup(page);
    await openUploadAndLeave(page);
    await backLink(page).click();
    await expect(page).toHaveURL(/\/v2\/upload$/);
    await expectRestored(page);
    await expect(page.locator("#meeting-project")).toHaveValue("");
    expect(fake.detailCalls).toBe(0);
  });

  test("프로젝트 정보 조회 실패 시 자동 선택·안내·오류 표시 없이 일반 상태", async ({ page }) => {
    await setup(page, { detailStatus: 500 });
    await openUploadAndLeave(page);
    await createProject(page, "신규 라인 구축");
    await expectRestored(page);
    await expect(page.locator("#meeting-project")).toHaveValue("");
    await expect(page.getByText("승인 대기 중입니다")).toHaveCount(0);
    await expect(page.locator("form").getByRole("alert")).toHaveCount(0);
  });
});

test.describe("returnTo 허용 목록", () => {
  for (const bad of ["//evil.example", "https://evil.example", "/v2/meetings", "\\v2\\upload"]) {
    test(`허용 밖 returnTo(${bad})는 무시하고 상세로 이동`, async ({ page }) => {
      await setup(page);
      await page.goto(`/v2/projects/new?returnTo=${encodeURIComponent(bad)}`);
      await expect(backLink(page)).toHaveCount(0);
      await createProject(page, "일반 등록");
      await expect(page).toHaveURL(/\/v2\/projects\/100$/);
    });
  }
});

test.describe("임시 저장값 처리", () => {
  const seed = (page: Page, value: string | null) =>
    page.evaluate(([key, v]) => (v === null ? window.sessionStorage.removeItem(key) : window.sessionStorage.setItem(key, v)), [DRAFT_KEY, value] as const);
  const goodDraft = (savedAt: number) =>
    JSON.stringify({ title: "저장된 회의", date: "2026-10-01", time: "13:20", participantIds: [8], meetingType: "regular", savedAt });

  test("resume 없이 올리기 화면에 들어가면 저장값을 적용하지 않고 지운다", async ({ page }) => {
    await setup(page);
    await seed(page, goodDraft(Date.now()));
    await page.goto("/v2/upload");
    await expect(page.getByRole("heading", { level: 1, name: "회의록 올리기" })).toBeVisible();
    await expect(page.getByLabel("회의명")).toHaveValue("");
    await expect(restoreNotice(page)).toHaveCount(0);
    expect(await draftOf(page)).toBeNull();
  });

  test("resume=1 이면 1시간 이내 저장값을 복원하고 지운다", async ({ page }) => {
    await setup(page);
    await seed(page, goodDraft(Date.now() - 59 * 60 * 1000));
    await page.goto("/v2/upload?resume=1");
    await expect(page.getByLabel("회의명")).toHaveValue("저장된 회의");
    await expect(restoreNotice(page)).toBeVisible();
    expect(await draftOf(page)).toBeNull();
  });

  for (const [name, value] of [
    ["1시간을 넘긴 값", goodDraft(Date.now() - 61 * 60 * 1000)],
    ["깨진 JSON", "{not json"],
    ["형식이 틀린 값", JSON.stringify({ title: 1, savedAt: Date.now() })],
  ] as const) {
    test(`${name}은 무시하고 오류 없이 동작`, async ({ page }) => {
      const errors: string[] = [];
      page.on("pageerror", (e) => errors.push(e.message));
      await setup(page);
      await seed(page, value);
      await page.goto("/v2/upload?resume=1");
      await expect(page.getByRole("heading", { level: 1, name: "회의록 올리기" })).toBeVisible();
      await expect(page.getByLabel("회의명")).toHaveValue("");
      await expect(restoreNotice(page)).toHaveCount(0);
      expect(errors).toEqual([]);
    });
  }

  test("세션 저장소 접근이 막혀도 오류 없이 동작(복원 없이 진행)", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await setup(page);
    await page.addInitScript(() => {
      const deny = () => {
        throw new DOMException("denied", "SecurityError");
      };
      const real = window.sessionStorage;
      const token = real.getItem("mi.v2.accessToken");
      Object.defineProperty(window, "sessionStorage", {
        configurable: true,
        value: new Proxy(real, {
          get: (target, prop) => {
            if (prop === "getItem") return (k: string) => (k === "mi.v2.accessToken" ? token : deny());
            if (prop === "setItem" || prop === "removeItem") return deny;
            return Reflect.get(target, prop);
          },
        }),
      });
    });
    await page.goto("/v2/upload?resume=1&newProject=100");
    await expect(page.getByRole("heading", { level: 1, name: "회의록 올리기" })).toBeVisible();
    await expect(restoreNotice(page)).toHaveCount(0);
    await page.getByLabel("회의명").fill("계속 입력 가능");
    await expect(page.getByLabel("회의명")).toHaveValue("계속 입력 가능");
    expect(errors).toEqual([]);
  });
});

for (const theme of ["light", "dark"] as const) {
  test(`${theme} 테마: 복원 안내 대비 4.5:1 이상, 콘솔 오류 없음`, async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    page.on("pageerror", (e) => errors.push(e.message));
    await setup(page, { role: "member" });
    await page.evaluate(([k, v]) => window.localStorage.setItem(k, v), [THEME_KEY, theme]);
    await openUploadAndLeave(page);
    await createProject(page, "대기될 프로젝트");
    await expectRestored(page);
    const notices = page.getByRole("status").filter({ hasText: /복원했습니다|승인 대기 중입니다/ });
    await expect(notices).toHaveCount(2);
    const ratios = await notices.evaluateAll((els) => {
      const parse = (c: string) => (c.match(/[\d.]+/g) ?? []).slice(0, 4).map(Number);
      const lum = ([r, g, b]: number[]) => {
        const f = (v: number) => ((v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
      };
      return els.map((el) => {
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
      });
    });
    for (const ratio of ratios) expect(ratio).toBeGreaterThanOrEqual(4.5);
    expect(errors).toEqual([]);
  });
}
