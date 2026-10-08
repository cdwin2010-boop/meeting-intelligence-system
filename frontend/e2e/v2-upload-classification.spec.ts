/*
 * v2 회의록 올리기: 회의 유형(필수)·프로젝트 선택, 상세 화면 유형·프로젝트 표시 E2E(작업 69-2).
 * 실제 백엔드 없이 page.route 로 /api/auth/me, /api/projects, /api/meetings/* 를 가로챈다.
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const THEME_KEY = "mi.v2.theme";
const ACCOUNT = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const AUDIO = { name: "weekly_1001.m4a", mimeType: "audio/mp4", buffer: Buffer.from("fake-audio") };
const REF = { id: 7, name: "한팀장" };

const project = (id: number, name: string, status: string, myRole: string | null) => ({
  id, name, description: "", status, departmentId: 1, departmentName: "생산팀", registeredBy: REF, lead: REF, approver: null,
  myRole, memberCount: 2, createdAt: "2026-09-01T00:00:00Z", decidedAt: null,
});

// 후보가 되는 것은 "진행 중 + 내 역할 있음" 두 건뿐(가나다 순으로 보이는지도 확인하려고 일부러 순서를 섞음)
const PROJECTS = [
  project(3, "하반기 설비 교체", "active", "member"),
  project(4, "승인 대기 신규 라인", "pending_approval", "lead"),
  project(5, "반려된 시범 사업", "rejected", "lead"),
  project(6, "참여자 아닌 프로젝트", "active", null),
  project(2, "가동률 개선", "active", "lead"),
];

const DETAIL = {
  id: 77, title: "설비 점검 주간 회의", heldAt: "2026-10-01T04:20:00Z", summary: "", decisions: [], status: "processing",
  confirmKind: null, confirmedBy: null, confirmedAt: null, firstCreatedAt: "2026-10-01T05:00:00Z", autoConfirmAt: null,
  registeredBy: REF, origin: "audio_minutes", participants: [], actionItems: [], recentEvents: [],
};

interface Opts {
  upload?: (route: Route) => unknown;
  projects?: (route: Route) => unknown;
  detail?: Record<string, unknown>;
}

async function login(page: Page, opts: Opts = {}) {
  await page.route("**/api/**", (route) => json(route, 200, [])); // 가로채지 않은 요청이 닫힌 백엔드로 가지 않게(나중에 등록한 것이 우선)
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, ACCOUNT) : json(route, 401, { detail: "인증이 필요합니다" }),
  );
  await page.route("**/api/accounts", (route) => json(route, 200, [{ id: 7, name: "한팀장", rank: "manager" }]));
  await page.route("**/api/projects", (route) => (opts.projects ? (opts.projects(route) as Promise<void>) : json(route, 200, PROJECTS)));
  await page.route("**/api/meetings/upload", (route) => (opts.upload ? (opts.upload(route) as Promise<void>) : json(route, 202, { meetingId: 77, jobId: 5 })));
  await page.route(/\/api\/meetings\/\d+$/, (route) => json(route, 200, withAllowed({ ...DETAIL, ...(opts.detail ?? {}) }, ACCOUNT)));
  await page.route(/\/api\/meetings\/\d+\/speakers$/, (route) => json(route, 200, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route(/\/api\/meetings\/\d+\/change-requests$/, (route) => json(route, 200, []));
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
}

async function openUpload(page: Page, opts: Opts = {}) {
  await login(page, opts);
  await page.goto("/v2/upload");
  await expect(page.getByRole("heading", { level: 1, name: "회의록 올리기" })).toBeVisible();
}

async function fillBase(page: Page) {
  await page.getByLabel("음성 파일 선택").setInputFiles(AUDIO);
  await page.getByLabel("회의명").fill("설비 점검 주간 회의");
  await page.getByLabel("회의 날짜").fill("2026-10-01");
  await page.getByLabel("회의 시각").fill("13:20");
}

const group = (page: Page) => page.getByRole("group", { name: "회의 유형" });
const typeLabel = (page: Page, label: string) => group(page).locator("label").filter({ has: page.getByRole("radio", { name: label, exact: true }) });
const pickType = (page: Page, label: string) => typeLabel(page, label).click();
const submitButton = (page: Page) => page.getByRole("button", { name: "올리고 분석 시작" });
const formAlert = (page: Page) => page.locator("form").getByRole("alert");
const body = (route: Route) => route.request().postDataBuffer()?.toString("utf-8") ?? "";

const TYPES: [string, string][] = [
  ["regular", "정기회의"],
  ["irregular", "비정기회의"],
  ["project", "프로젝트 회의"],
  ["external", "외부(영업·상담) 회의"],
  ["other", "기타"],
];

test.describe("회의 유형 그룹", () => {
  test("legend 와 라디오 5개, 기본 선택 없음, 한글 라벨, 방향키 조작", async ({ page }) => {
    await openUpload(page);
    const radios = group(page).getByRole("radio");
    await expect(group(page)).toBeVisible();
    await expect(group(page).locator("legend")).toContainText("회의 유형");
    await expect(group(page).locator("legend")).toContainText("(필수)");
    await expect(radios).toHaveCount(5);
    for (const [, label] of TYPES) await expect(group(page).getByRole("radio", { name: label, exact: true })).not.toBeChecked();
    await radios.first().focus();
    await page.keyboard.press("ArrowRight");
    await expect(group(page).getByRole("radio", { name: "비정기회의", exact: true })).toBeChecked();
    await page.keyboard.press("ArrowRight");
    await expect(group(page).getByRole("radio", { name: "프로젝트 회의", exact: true })).toBeChecked();
    await page.keyboard.press("ArrowLeft");
    await expect(group(page).getByRole("radio", { name: "비정기회의", exact: true })).toBeChecked();
    await expect(group(page).getByRole("radio", { name: "프로젝트 회의", exact: true })).not.toBeChecked();
  });

  test("선택 상태는 색 외에 ●/○ 표시를 가진다", async ({ page }) => {
    await openUpload(page);
    const label = (text: string) => typeLabel(page, text);
    await expect(label("정기회의")).toContainText("○");
    await pickType(page, "기타");
    await expect(label("기타")).toContainText("●");
    await expect(label("정기회의")).toContainText("○");
  });

  test("유형 없이 제출 → 서버 호출 없음, 알림, 포커스가 유형 그룹으로", async ({ page }) => {
    let calls = 0;
    await openUpload(page, { upload: (route) => { calls += 1; return json(route, 202, { meetingId: 77, jobId: 5 }); } });
    await fillBase(page);
    await submitButton(page).click();
    await expect(formAlert(page)).toHaveText("회의 유형을 선택해 주세요");
    await expect(group(page).getByRole("radio").first()).toBeFocused();
    expect(calls).toBe(0);
  });
});

test.describe("제출 본문", () => {
  for (const [code, label] of TYPES) {
    test(`${label} → meetingType=${code}${code === "project" ? " + projectId" : ", projectId 없음"}`, async ({ page }) => {
      let sent = "";
      await openUpload(page, { upload: (route) => { sent = body(route); return json(route, 202, { meetingId: 77, jobId: 5 }); } });
      await fillBase(page);
      await pickType(page, label);
      if (code === "project") await page.locator("#meeting-project").selectOption({ label: "하반기 설비 교체" });
      await submitButton(page).click();
      await expect(page).toHaveURL(/\/v2\/meetings\/77$/);
      expect(sent).toMatch(new RegExp(`name="meetingType"\\r\\n\\r\\n${code}\\r\\n`));
      if (code === "project") expect(sent).toMatch(/name="projectId"\r\n\r\n3\r\n/);
      else expect(sent).not.toContain('name="projectId"');
    });
  }

  test("프로젝트를 고른 뒤 다른 유형으로 바꾸면 projectId 를 보내지 않는다", async ({ page }) => {
    let sent = "";
    await openUpload(page, { upload: (route) => { sent = body(route); return json(route, 202, { meetingId: 77, jobId: 5 }); } });
    await fillBase(page);
    await pickType(page, "프로젝트 회의");
    await page.locator("#meeting-project").selectOption({ label: "가동률 개선" });
    await pickType(page, "기타");
    await submitButton(page).click();
    await expect(page).toHaveURL(/\/v2\/meetings\/77$/);
    expect(sent).not.toContain('name="projectId"');
  });
});

test.describe("프로젝트 선택", () => {
  test("프로젝트 회의에서만 나타난다", async ({ page }) => {
    await openUpload(page);
    await expect(page.locator("#meeting-project")).toHaveCount(0);
    await pickType(page, "프로젝트 회의");
    await expect(page.locator("#meeting-project")).toBeVisible();
    await pickType(page, "정기회의");
    await expect(page.locator("#meeting-project")).toHaveCount(0);
  });

  test("내가 참여자인 진행 중 프로젝트만 이름순 후보(대기·반려·참여자 아님 제외)", async ({ page }) => {
    await openUpload(page);
    await pickType(page, "프로젝트 회의");
    const select = page.locator("#meeting-project");
    await expect(select.locator("option")).toHaveText(["프로젝트를 선택하세요", "가동률 개선", "하반기 설비 교체"]);
    await expect(page.getByRole("link", { name: "새 프로젝트 등록" })).toHaveAttribute("href", "/v2/projects/new");
  });

  test("고르지 않으면 제출 막힘(서버 호출 없음)", async ({ page }) => {
    let calls = 0;
    await openUpload(page, { upload: (route) => { calls += 1; return json(route, 202, { meetingId: 77, jobId: 5 }); } });
    await fillBase(page);
    await pickType(page, "프로젝트 회의");
    await expect(page.locator("#meeting-project")).toBeVisible();
    await submitButton(page).click();
    await expect(formAlert(page)).toHaveText("프로젝트를 선택해 주세요");
    await expect(page.locator("#meeting-project")).toBeFocused();
    expect(calls).toBe(0);
  });

  test("후보가 없으면 안내와 새 프로젝트 등록 링크, 제출 막힘", async ({ page }) => {
    let calls = 0;
    await openUpload(page, {
      projects: (route) => json(route, 200, [project(4, "대기", "pending_approval", "lead"), project(6, "남의 것", "active", null)]),
      upload: (route) => { calls += 1; return json(route, 202, { meetingId: 77, jobId: 5 }); },
    });
    await fillBase(page);
    await pickType(page, "프로젝트 회의");
    await expect(page.getByText("참여 중인 진행 중 프로젝트가 없습니다")).toBeVisible();
    await expect(page.getByRole("link", { name: "새 프로젝트 등록" })).toHaveAttribute("href", "/v2/projects/new");
    await submitButton(page).click();
    await expect(formAlert(page)).toHaveText("프로젝트를 선택해 주세요");
    expect(calls).toBe(0);
  });

  test("유형을 바꾸면 프로젝트 선택이 초기화된다", async ({ page }) => {
    await openUpload(page);
    await pickType(page, "프로젝트 회의");
    await page.locator("#meeting-project").selectOption({ label: "가동률 개선" });
    await pickType(page, "기타");
    await pickType(page, "프로젝트 회의");
    await expect(page.locator("#meeting-project")).toHaveValue("");
  });

  test("로딩 표시 → 오류 표시 → 다시 불러오기로 복구", async ({ page }) => {
    let calls = 0;
    let healed = false; // 개발 모드는 효과를 두 번 실행하므로 호출 횟수가 아니라 상태로 성공 시점을 정한다
    await openUpload(page, {
      projects: async (route) => {
        calls += 1;
        await new Promise((resolve) => setTimeout(resolve, 400));
        return healed ? json(route, 200, PROJECTS) : json(route, 500, { detail: "서버 오류" });
      },
    });
    await pickType(page, "프로젝트 회의");
    await expect(page.getByText("프로젝트 목록을 불러오는 중…")).toBeVisible();
    const alert = page.getByRole("alert").filter({ hasText: "프로젝트 목록을 불러오지 못했습니다" });
    await expect(alert).toContainText("서버 오류");
    healed = true;
    await alert.getByRole("button", { name: "목록 다시 불러오기" }).click();
    await expect(page.locator("#meeting-project")).toBeVisible();
    expect(calls).toBeGreaterThanOrEqual(2);
  });
});

test.describe("서버 오류", () => {
  const CASES: [number, string][] = [
    [422, "프로젝트 회의는 projectId 가 필요합니다."],
    [404, "프로젝트를 찾을 수 없습니다"],
    [409, "진행 중인 프로젝트에만 회의록을 연결할 수 있습니다"],
    [403, "프로젝트 참여자만 그 프로젝트 회의록을 올릴 수 있습니다"],
  ];
  for (const [status, message] of CASES) {
    test(`${status} → 서버 메시지 알림, 입력(파일·유형·프로젝트) 유지`, async ({ page }) => {
      await openUpload(page, { upload: (route) => json(route, status, { detail: message }) });
      await fillBase(page);
      await pickType(page, "프로젝트 회의");
      await page.locator("#meeting-project").selectOption({ label: "가동률 개선" });
      await submitButton(page).click();
      await expect(page.getByRole("alert").filter({ hasText: "올리지 못했습니다" })).toContainText(message);
      await expect(page).toHaveURL(/\/v2\/upload$/);
      await expect(page.locator("form").getByText("weekly_1001.m4a")).toBeVisible();
      await expect(group(page).getByRole("radio", { name: "프로젝트 회의", exact: true })).toBeChecked();
      await expect(page.locator("#meeting-project")).toHaveValue("2");
      await expect(submitButton(page)).toBeEnabled();
    });
  }

  test("중복 클릭해도 한 번만 전송", async ({ page }) => {
    let calls = 0;
    await openUpload(page, {
      upload: async (route) => {
        calls += 1;
        await new Promise((resolve) => setTimeout(resolve, 400));
        return json(route, 202, { meetingId: 77, jobId: 5 });
      },
    });
    await fillBase(page);
    await pickType(page, "정기회의");
    await submitButton(page).dblclick();
    await expect(page).toHaveURL(/\/v2\/meetings\/77$/);
    expect(calls).toBe(1);
  });
});

test.describe("상세 화면 유형·프로젝트", () => {
  const open = async (page: Page, detail?: Record<string, unknown>) => {
    await login(page, { detail });
    await page.goto("/v2/meetings/77");
    await expect(page.getByRole("heading", { level: 1, name: "설비 점검 주간 회의" })).toBeVisible();
  };
  const overview = (page: Page) => page.getByRole("region", { name: "회의 개요" });

  test("유형 한글 라벨과 프로젝트 링크(접근성 이름 '프로젝트: 이름')", async ({ page }) => {
    await open(page, { meetingType: "project", project: { id: 3, name: "하반기 설비 교체" } });
    await expect(overview(page).locator("dt", { hasText: "회의 유형" })).toBeVisible();
    await expect(overview(page)).toContainText("프로젝트 회의");
    const link = overview(page).getByRole("link", { name: "프로젝트: 하반기 설비 교체" });
    await expect(link).toHaveAttribute("href", "/v2/projects/3");
  });

  test("이전 회의록(meetingType null)은 '미지정', 프로젝트 없으면 항목 숨김", async ({ page }) => {
    await open(page, { meetingType: null, project: null });
    await expect(overview(page)).toContainText("회의 유형");
    await expect(overview(page)).toContainText("미지정");
    await expect(overview(page).locator("dt", { hasText: "프로젝트" })).toHaveCount(0);
  });

  test("프로젝트가 아닌 유형은 라벨만 보이고 프로젝트 항목은 숨김", async ({ page }) => {
    await open(page, { meetingType: "external", project: null });
    await expect(overview(page)).toContainText("외부(영업·상담) 회의");
    await expect(overview(page).locator("dt", { hasText: "프로젝트" })).toHaveCount(0);
  });

  test("필드 자체가 없는 응답도 오류 없이 '미지정'", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await open(page);
    await expect(overview(page)).toContainText("미지정");
    expect(errors).toEqual([]);
  });
});

test.describe("레이아웃·테마", () => {
  for (const width of [1280, 768, 375]) {
    test(`${width}px 에서 가로 넘침 없음, 라디오 그룹은 줄바꿈`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await openUpload(page);
      await pickType(page, "프로젝트 회의");
      await expect(page.locator("#meeting-project")).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      if (width === 375) {
        const tops = await group(page).locator("label").evaluateAll((nodes) => new Set(nodes.map((n) => Math.round(n.getBoundingClientRect().top))).size);
        expect(tops).toBeGreaterThan(1);
      }
    });
  }

  for (const theme of ["light", "dark"] as const) {
    test(`${theme} 테마에서 올리기·상세 화면에 콘솔 오류 없음`, async ({ page }) => {
      const problems: string[] = [];
      page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()}`); });
      page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
      await login(page, { detail: { meetingType: "project", project: { id: 3, name: "하반기 설비 교체" } } });
      await page.evaluate(([k, v]) => window.localStorage.setItem(k, v), [THEME_KEY, theme]);
      await page.goto("/v2/upload");
      await pickType(page, "프로젝트 회의");
      await expect(page.locator("#meeting-project")).toBeVisible();
      await page.goto("/v2/meetings/77");
      await expect(page.getByRole("link", { name: "프로젝트: 하반기 설비 교체" })).toBeVisible();
      expect(problems).toEqual([]);
    });
  }
});
