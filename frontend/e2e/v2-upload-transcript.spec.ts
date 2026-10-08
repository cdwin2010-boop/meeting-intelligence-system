/*
 * v2 자료 파일(txt) 등록 E2E(작업 73-1): 올리기 화면의 자료 종류 선택, 제출 본문, 검증·오류, 임시 저장·복원, 상세의 오디오 없음 표시.
 * 실제 백엔드 없이 page.route 로 응답한다(데이터는 모두 가상).
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const DRAFT_KEY = "mi.v2.uploadDraft";
const ACCOUNT = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const json = (route: Route, status: number, body: unknown) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const AUDIO = { name: "weekly_1001.m4a", mimeType: "audio/mp4", buffer: Buffer.from("fake-audio") };
const TXT = { name: "회의_전사문.txt", mimeType: "text/plain", buffer: Buffer.from("[00:03] 화자1: 안녕하세요\n[00:10] 화자2: 네", "utf-8") };

const DETAIL = (over: Record<string, unknown> = {}) => ({
  id: 77, title: "설비 점검 주간 회의", heldAt: "2026-10-01T04:20:00Z", summary: "", decisions: [], status: "confirmed",
  confirmKind: "manager", confirmedBy: null, confirmedAt: null, firstCreatedAt: "2026-10-01T05:00:00Z", autoConfirmAt: null,
  registeredBy: { id: 7, name: "한팀장" }, origin: "audio_minutes", participants: [], actionItems: [], recentEvents: [], phase: "active", ...over,
});

interface Setup {
  upload?: (route: Route) => unknown;
  detail?: Record<string, unknown>;
  theme?: "light" | "dark";
}
interface Calls {
  uploads: string[];
  audioUrlRequests: number;
}

async function login(page: Page, setup: Setup = {}): Promise<Calls> {
  const calls: Calls = { uploads: [], audioUrlRequests: 0 };
  await page.route("**/api/**", (route) => json(route, 200, []));
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, ACCOUNT) : json(route, 401, { detail: "x" }),
  );
  await page.route("**/api/accounts", (route) => json(route, 200, [{ id: 7, name: "한팀장", rank: "manager" }]));
  await page.route("**/api/projects", (route) =>
    json(route, 200, [{ id: 2, name: "가동률 개선", description: "", status: "active", departmentId: 1, departmentName: "생산팀", registeredBy: { id: 7, name: "한팀장" }, lead: { id: 7, name: "한팀장" }, approver: null, myRole: "lead", memberCount: 1, createdAt: "2026-09-01T00:00:00Z", decidedAt: null }]),
  );
  await page.route("**/api/meetings/upload", (route) => {
    calls.uploads.push(route.request().postDataBuffer()?.toString("utf-8") ?? "");
    return setup.upload ? (setup.upload(route) as Promise<void>) : json(route, 202, { meetingId: 77, jobId: 5 });
  });
  await page.route(/\/api\/meetings\/77$/, (route) => json(route, 200, withAllowed(DETAIL(setup.detail) as never, ACCOUNT)));
  await page.route(/\/api\/meetings\/77\/audio-url$/, (route) => {
    calls.audioUrlRequests += 1;
    return json(route, 404, { detail: "음성 파일이 없습니다" });
  });
  await page.route(/\/api\/meetings\/77\/(transcript)$/, (route) => json(route, 404, { detail: "전사문이 없습니다" }));
  await page.route(/\/api\/meetings\/77\/speakers$/, (route) => json(route, 200, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route(/\/api\/meetings\/77\/change-requests$/, (route) => json(route, 200, []));
  await page.goto("/v2/login");
  await page.evaluate(([k, t, th]) => {
    window.sessionStorage.setItem(k, t);
    if (th) window.localStorage.setItem("mi.v2.theme", th);
  }, [TOKEN_KEY, FAKE_TOKEN, setup.theme ?? ""]);
  return calls;
}

async function openUpload(page: Page, setup: Setup = {}, path = "/v2/upload") {
  const calls = await login(page, setup);
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1, name: "회의록 올리기" })).toBeVisible();
  return calls;
}

const kindGroup = (page: Page) => page.getByRole("group", { name: "자료 종류" });
const kindRadio = (page: Page, label: string) => kindGroup(page).getByRole("radio", { name: label, exact: true });
const kindLabel = (page: Page, label: string) => kindGroup(page).locator("label").filter({ has: page.getByRole("radio", { name: label, exact: true }) });
const fileInput = (page: Page) => page.locator("input[type='file']");
const typeLabel = (page: Page, label: string) =>
  page.getByRole("group", { name: "회의 유형" }).locator("label").filter({ has: page.getByRole("radio", { name: label, exact: true }) });
const submit = (page: Page) => page.getByRole("button", { name: "올리고 분석 시작" });
const formAlert = (page: Page) => page.locator("form").getByRole("alert");
const NOTICE = '전사문(txt)을 올리면 음성 인식 없이 바로 업무를 추출합니다. 시각 표기가 없으면 "근거 위치 보기"는 쓸 수 없습니다.';

async function fill(page: Page) {
  await page.getByLabel("회의명").fill("설비 점검 주간 회의");
  await page.getByLabel("회의 날짜").fill("2026-10-01");
  await page.getByLabel("회의 시각").fill("13:20");
  await typeLabel(page, "정기회의").click();
}

test.describe("자료 종류 선택", () => {
  test("legend '자료 종류', 라디오 둘, 기본은 음성 파일(●/○), 음성 선택 입력 그대로", async ({ page }) => {
    await openUpload(page);
    await expect(kindGroup(page).locator("legend")).toHaveText("자료 종류");
    await expect(kindGroup(page).getByRole("radio")).toHaveCount(2);
    await expect(kindRadio(page, "음성 파일")).toBeChecked();
    await expect(kindRadio(page, "자료 파일(txt)")).not.toBeChecked();
    await expect(kindLabel(page, "음성 파일")).toContainText("●");
    await expect(kindLabel(page, "자료 파일(txt)")).toContainText("○");
    await expect(page.getByLabel("음성 파일 선택")).toHaveAttribute("accept", /audio/);
    await expect(page.getByText(NOTICE)).toHaveCount(0);
  });

  test("자료 파일(txt)을 고르면 .txt 만 받고 안내가 role=status 로 보인다. 종류를 바꾸면 선택한 파일이 비워진다", async ({ page }) => {
    await openUpload(page);
    await page.getByLabel("음성 파일 선택").setInputFiles(AUDIO);
    await expect(page.getByText("weekly_1001.m4a")).toBeVisible();
    await kindLabel(page, "자료 파일(txt)").click();
    await expect(kindLabel(page, "자료 파일(txt)")).toContainText("●");
    await expect(page.getByText("선택한 파일 없음")).toBeVisible();
    const input = page.getByLabel("자료 파일 선택");
    await expect(input).toHaveAttribute("accept", ".txt,text/plain");
    await expect(page.getByRole("status").filter({ hasText: NOTICE })).toBeVisible();
    await input.setInputFiles(TXT);
    await expect(page.getByText("회의_전사문.txt")).toBeVisible();
    await kindLabel(page, "음성 파일").click();
    await expect(page.getByText("선택한 파일 없음")).toBeVisible();
    await expect(page.getByLabel("음성 파일 선택")).toHaveAttribute("accept", /audio/);
    await expect(page.getByText(NOTICE)).toHaveCount(0);
  });
});

test.describe("제출", () => {
  test("음성 파일(기본): sourceKind=audio 를 보낸다", async ({ page }) => {
    const calls = await openUpload(page);
    await fill(page);
    await page.getByLabel("음성 파일 선택").setInputFiles(AUDIO);
    await submit(page).click();
    await expect(page).toHaveURL(/\/v2\/meetings\/77$/);
    expect(calls.uploads[0]).toMatch(/name="sourceKind"\r\n\r\naudio/);
  });

  test("자료 파일(txt): sourceKind=transcript_txt 와 txt 파일을 보낸다", async ({ page }) => {
    const calls = await openUpload(page);
    await fill(page);
    await kindLabel(page, "자료 파일(txt)").click();
    await page.getByLabel("자료 파일 선택").setInputFiles(TXT);
    await submit(page).click();
    await expect(page).toHaveURL(/\/v2\/meetings\/77$/);
    expect(calls.uploads).toHaveLength(1);
    expect(calls.uploads[0]).toMatch(/name="sourceKind"\r\n\r\ntranscript_txt/);
    expect(calls.uploads[0]).toContain('filename="');
    expect(calls.uploads[0]).toContain("[00:03] 화자1: 안녕하세요");
  });

  test("확장자가 .txt 가 아니면 서버를 호출하지 않고 알림", async ({ page }) => {
    const calls = await openUpload(page);
    await fill(page);
    await kindLabel(page, "자료 파일(txt)").click();
    await page.getByLabel("자료 파일 선택").setInputFiles(AUDIO); // accept 는 선택창만 거른다. 끌어다 놓기 등으로 들어올 수 있다
    await submit(page).click();
    await expect(formAlert(page)).toHaveText("자료 파일은 .txt 파일만 올릴 수 있습니다.");
    expect(calls.uploads).toEqual([]);
    await expect(page).toHaveURL(/\/v2\/upload$/);
  });

  test("파일 없이 제출하면 종류에 맞는 안내", async ({ page }) => {
    await openUpload(page);
    await fill(page);
    await kindLabel(page, "자료 파일(txt)").click();
    await submit(page).click();
    await expect(formAlert(page)).toHaveText("자료 파일(txt)을 선택하세요.");
  });

  test("서버 오류(422) 메시지를 알림으로 보이고 입력을 유지한다(상한 문구는 서버 응답 그대로)", async ({ page }) => {
    await openUpload(page, { upload: (route) => json(route, 422, { detail: "txt 파일이 너무 큽니다(최대 2048KB)." }) });
    await fill(page);
    await kindLabel(page, "자료 파일(txt)").click();
    await page.getByLabel("자료 파일 선택").setInputFiles(TXT);
    await submit(page).click();
    await expect(formAlert(page)).toContainText("txt 파일이 너무 큽니다(최대 2048KB).");
    await expect(page.getByLabel("회의명")).toHaveValue("설비 점검 주간 회의");
    await expect(kindRadio(page, "자료 파일(txt)")).toBeChecked();
    await expect(page.getByText("회의_전사문.txt")).toBeVisible();
    await expect(submit(page)).toBeEnabled();
  });
});

test.describe("임시 저장·복원", () => {
  test("새 프로젝트 등록으로 떠날 때 sourceKind 를 저장하고(파일 제외), 복귀하면 복원한다", async ({ page }) => {
    await openUpload(page);
    await fill(page);
    await kindLabel(page, "자료 파일(txt)").click();
    await page.getByLabel("자료 파일 선택").setInputFiles(TXT);
    await typeLabel(page, "프로젝트 회의").click();
    await page.getByRole("link", { name: "새 프로젝트 등록" }).click();
    await expect(page).toHaveURL(/\/v2\/projects\/new/);
    const draft = JSON.parse((await page.evaluate((k) => window.sessionStorage.getItem(k), DRAFT_KEY))!);
    expect(draft.sourceKind).toBe("transcript_txt");
    expect(JSON.stringify(draft)).not.toContain("회의_전사문");
    await page.goto("/v2/upload?resume=1");
    await expect(kindRadio(page, "자료 파일(txt)")).toBeChecked();
    await expect(page.getByRole("status").filter({ hasText: "입력하던 내용을 복원했습니다. 파일은 다시 선택해 주세요." })).toBeVisible();
    await expect(page.getByText("선택한 파일 없음")).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: NOTICE })).toBeVisible();
  });

  test("sourceKind 가 없는 예전 저장값은 음성 파일로 복원한다", async ({ page }) => {
    await login(page);
    await page.evaluate(
      ([k]) => window.sessionStorage.setItem(k, JSON.stringify({ title: "예전 저장", date: "2026-10-01", time: "13:20", participantIds: [], meetingType: "regular", savedAt: Date.now() })),
      [DRAFT_KEY],
    );
    await page.goto("/v2/upload?resume=1");
    await expect(page.getByLabel("회의명")).toHaveValue("예전 저장");
    await expect(kindRadio(page, "음성 파일")).toBeChecked();
  });
});

test.describe("상세 화면", () => {
  test("hasAudio=false: 플레이어 없이 안내, 자료 종류 항목, 재생 주소를 요청하지 않는다", async ({ page }) => {
    const calls = await login(page, { detail: { sourceKind: "transcript_txt", hasAudio: false } });
    await page.goto("/v2/meetings/77");
    await expect(page.getByRole("heading", { level: 1, name: "설비 점검 주간 회의" })).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "자료 파일로 등록된 회의록입니다(음성 없음)" })).toBeVisible();
    await expect(page.getByText("음성 파일이 없습니다")).toHaveCount(0);
    const dl = page.getByRole("region", { name: "회의 개요" }).locator("dl");
    await expect(dl.locator("div", { hasText: /^자료 종류/ })).toContainText("자료 파일(txt)");
    expect(calls.audioUrlRequests).toBe(0);
  });

  test("음성 회의록: 자료 종류 '음성 파일', 플레이어 영역(재생 주소 요청)", async ({ page }) => {
    const calls = await login(page, { detail: { sourceKind: "audio", hasAudio: true } });
    await page.goto("/v2/meetings/77");
    await expect(page.getByRole("heading", { level: 1, name: "설비 점검 주간 회의" })).toBeVisible();
    const dl = page.getByRole("region", { name: "회의 개요" }).locator("dl");
    await expect(dl.locator("div", { hasText: /^자료 종류/ })).toContainText("음성 파일");
    await expect(page.getByText("자료 파일로 등록된 회의록입니다")).toHaveCount(0);
    await expect.poll(() => calls.audioUrlRequests).toBeGreaterThan(0);
  });

  test("필드가 없는 응답은 오류 없이 음성 파일로 취급한다", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const calls = await login(page);
    await page.goto("/v2/meetings/77");
    const dl = page.getByRole("region", { name: "회의 개요" }).locator("dl");
    await expect(dl.locator("div", { hasText: /^자료 종류/ })).toContainText("음성 파일");
    await expect.poll(() => calls.audioUrlRequests).toBeGreaterThan(0);
    expect(errors).toEqual([]);
  });
});

const LONG = "아주아주아주긴파일이름".repeat(7); // 공백 없는 60자 이상

test.describe("긴 파일명(작업 73-2)", () => {
  for (const width of [1280, 768, 375]) {
    for (const kind of ["음성 파일", "자료 파일(txt)"] as const) {
      test(`${width}px ${kind}: 60자 넘는 공백 없는 파일명도 가로 넘침 없이 영역 안에 보이고, 종류 전환·다시 선택이 동작한다`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await openUpload(page);
        const txt = kind === "자료 파일(txt)";
        if (txt) await kindLabel(page, kind).click();
        const input = page.getByLabel(txt ? "자료 파일 선택" : "음성 파일 선택");
        const long = txt ? { name: `${LONG}.txt`, mimeType: "text/plain", buffer: Buffer.from("화자1: 안녕") } : { name: `${LONG}.m4a`, mimeType: "audio/mp4", buffer: Buffer.from("x") };
        await input.setInputFiles(long);
        const nameEl = page.locator(`span[title="${long.name}"]`);
        await expect(nameEl).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
        // 파일명 요소가 파일 선택 영역(점선 상자) 안에 있다
        const inside = await nameEl.evaluate((el) => {
          const box = el.closest("div.border-dashed")!.getBoundingClientRect();
          const r = el.getBoundingClientRect();
          return r.left >= box.left - 1 && r.right <= box.right + 1;
        });
        expect(inside).toBe(true);
        // 줄바꿈으로 전체 이름이 보인다(말줄임이 아니다)
        expect(await nameEl.evaluate((el) => getComputedStyle(el).textOverflow)).not.toBe("ellipsis");
        // 종류를 바꾸면 선택이 비워지고, 다시 골라 선택할 수 있다
        await kindLabel(page, txt ? "음성 파일" : "자료 파일(txt)").click();
        await expect(page.getByText("선택한 파일 없음")).toBeVisible();
        await kindLabel(page, kind).click();
        await page.getByLabel(txt ? "자료 파일 선택" : "음성 파일 선택").setInputFiles(long);
        await expect(nameEl).toBeVisible();
        await page.getByLabel(txt ? "자료 파일 선택" : "음성 파일 선택").setInputFiles(txt ? { name: "다른.txt", mimeType: "text/plain", buffer: Buffer.from("화자1: 네") } : { name: "다른.m4a", mimeType: "audio/mp4", buffer: Buffer.from("x") });
        await expect(page.getByText(txt ? "다른.txt" : "다른.m4a")).toBeVisible();
      });
    }
  }
});

test.describe("반응형", () => {
  for (const width of [1280, 768, 375]) {
    test(`${width}px: 올리기·상세에 가로 넘침이 없다`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await openUpload(page);
      await kindLabel(page, "자료 파일(txt)").click();
      await page.getByLabel("자료 파일 선택").setInputFiles({ name: "아주아주아주긴파일이름".repeat(6) + ".txt", mimeType: "text/plain", buffer: Buffer.from("화자1: 안녕") });
      await expect(page.getByRole("status").filter({ hasText: NOTICE })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
      await page.goto("/v2/meetings/77");
      await expect(page.getByRole("heading", { level: 1, name: "설비 점검 주간 회의" })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
    });
  }
});

for (const theme of ["light", "dark"] as const) {
  test(`${theme} 테마: 콘솔 오류 없음, 안내·라디오 글자 대비 4.5:1 이상`, async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    page.on("pageerror", (e) => errors.push(e.message));
    await openUpload(page, { theme });
    await kindLabel(page, "자료 파일(txt)").click();
    await expect(page.getByRole("status").filter({ hasText: NOTICE })).toBeVisible();
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
      return [...document.querySelectorAll("fieldset legend, fieldset label, fieldset p[role='status']")].map(ratio);
    });
    expect(ratios.length).toBeGreaterThan(3);
    for (const value of ratios) expect(value).toBeGreaterThanOrEqual(4.5);
    expect(errors).toEqual([]);
  });
}
