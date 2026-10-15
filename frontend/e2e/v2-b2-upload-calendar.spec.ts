/*
 * 작업 B-2 E2E: ① 엑셀 업로드 화면 비노출(플래그) ② 웹 직접 입력(붙여넣기) ③ 개인별 할 일 캘린더.
 * 백엔드 없이 page.route 로 응답한다(데이터는 모두 가상).
 */
import { expect, test, type Page, type Route, enableXlsxUpload } from "./helpers/test";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const MANAGER = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const STAFF = { id: 8, name: "김담당", rank: "staff", tenantId: 1 };
const json = (route: Route, status: number, body: unknown) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

async function signIn(page: Page, me: Record<string, unknown>) {
  await page.route("**/api/auth/me", (route) => (route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, me) : json(route, 401, { detail: "no" })));
  await page.route("**/api/accounts", (route) => json(route, 200, [{ id: 7, name: "한팀장", rank: "manager" }]));
  await page.goto("/v2/login");
  await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
}

/* ---------------- ①② 업로드 화면 ---------------- */
const kindGroup = (page: Page) => page.getByRole("group", { name: "자료 종류" });
const typeLabel = (page: Page, label: string) =>
  page.getByRole("group", { name: "회의 유형" }).locator("label").filter({ has: page.getByRole("radio", { name: label, exact: true }) });

async function openUpload(page: Page, uploads: string[], options: { xlsx?: boolean; pasteMaxChars?: number } = {}) {
  await signIn(page, MANAGER);
  await page.route("**/api/meetings/upload", (route) => {
    uploads.push(route.request().postDataBuffer()?.toString("utf-8") ?? "");
    return json(route, 202, { meetingId: 77, jobId: 5 });
  });
  if (options.xlsx || options.pasteMaxChars) await enableXlsxUpload(page, { pasteMaxChars: options.pasteMaxChars });
  await page.goto("/v2/upload");
  await expect(page.getByRole("heading", { level: 1, name: "회의록 올리기" })).toBeVisible();
}

test.describe("엑셀 화면 비노출", () => {
  test("기본은 엑셀 종류·양식 내려받기가 보이지 않고 허용 확장자에도 xlsx 가 없다", async ({ page }) => {
    await openUpload(page, []);
    await expect(kindGroup(page).getByRole("radio", { name: "음성 파일", exact: true })).toBeVisible();
    await expect(kindGroup(page).getByRole("radio", { name: "자료 파일(txt)", exact: true })).toBeVisible();
    await expect(kindGroup(page).getByRole("radio", { name: "텍스트 직접 입력", exact: true })).toBeVisible();
    await expect(kindGroup(page).getByRole("radio", { name: "자료 파일(엑셀)" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "엑셀 양식 내려받기" })).toHaveCount(0);
    await expect(page.locator('input[type="file"]')).not.toHaveAttribute("accept", /xlsx/);
  });

  test("숨김 상태에서 xlsx 파일을 골라도 엑셀 종류로 바뀌지 않는다", async ({ page }) => {
    await openUpload(page, []);
    await page.locator('input[type="file"]').setInputFiles({ name: "회의.xlsx", mimeType: "application/octet-stream", buffer: Buffer.from("PK") });
    await expect(kindGroup(page).getByRole("radio", { name: "음성 파일", exact: true })).toBeChecked();
  });

  test("플래그를 켜면 엑셀 종류가 보인다", async ({ page }) => {
    await openUpload(page, [], { xlsx: true });
    await expect(kindGroup(page).getByRole("radio", { name: "자료 파일(엑셀)", exact: true })).toBeVisible();
  });
});

test.describe("텍스트 직접 입력", () => {
  test("붙여넣은 글이 txt 파일(sourceKind=transcript_txt)로 올라간다", async ({ page }) => {
    const uploads: string[] = [];
    await openUpload(page, uploads);
    await kindGroup(page).locator("label").filter({ hasText: "텍스트 직접 입력" }).click();
    await expect(page.locator('input[type="file"]')).toHaveCount(0);
    await page.getByLabel("회의 내용").fill("[00:00:10] 화자1: 견적서는 금요일까지 보내겠습니다.\r\n[00:00:20] 화자2: 알겠습니다.");
    await page.getByLabel("회의명").fill("붙여넣기 회의");
    await page.getByLabel("회의 날짜").fill("2026-09-01"); // 파일이 없어 제안값이 없다: 회의 일시는 직접 입력(기존 txt 업로드와 같은 필수 규칙)
    await page.getByLabel("회의 시각").fill("10:00");
    await typeLabel(page, "정기회의").click();
    await page.getByRole("button", { name: "올리고 분석 시작" }).click();
    await expect.poll(() => uploads.length).toBe(1);
    expect(uploads[0]).toContain("transcript_txt");
    expect(uploads[0]).toContain("filename=\"붙여넣기 회의.txt\"");
    expect(uploads[0]).toContain("견적서는 금요일까지 보내겠습니다.\n[00:00:20] 화자2"); // 줄바꿈이 LF 로 통일된다
  });

  test("빈 입력은 서버를 부르지 않고 안내한다", async ({ page }) => {
    const uploads: string[] = [];
    await openUpload(page, uploads);
    await kindGroup(page).locator("label").filter({ hasText: "텍스트 직접 입력" }).click();
    await page.getByLabel("회의 내용").fill("   \n  ");
    await typeLabel(page, "정기회의").click();
    await page.getByRole("button", { name: "올리고 분석 시작" }).click();
    await expect(page.getByText("회의 내용을 입력해 주세요.")).toBeVisible();
    expect(uploads).toHaveLength(0);
  });

  test("최대 글자 수를 넘으면 글자 수를 알리고 서버를 부르지 않는다", async ({ page }) => {
    const uploads: string[] = [];
    await openUpload(page, uploads, { pasteMaxChars: 20 });
    await kindGroup(page).locator("label").filter({ hasText: "텍스트 직접 입력" }).click();
    await page.getByLabel("회의 내용").fill("가".repeat(30));
    await expect(page.getByText("글자 수를 넘었습니다")).toBeVisible();
    await typeLabel(page, "정기회의").click();
    await page.getByRole("button", { name: "올리고 분석 시작" }).click();
    await expect(page.getByText("입력한 글이 너무 깁니다(최대 20자, 현재 30자).")).toBeVisible();
    expect(uploads).toHaveLength(0);
  });
});

/* ---------------- ③ 캘린더 ---------------- */
const EMPTY = { total: 0, items: [] };
const TODOS = { awaitingConfirmMeetings: EMPTY, needsCompletionItems: EMPTY, myItems: EMPTY, unreadAutoConfirmed: EMPTY, pendingChangeRequests: EMPTY };

function item(id: number, title: string, dueDate: string | null, extra: Record<string, unknown> = {}) {
  return {
    meetingId: 41, meetingTitle: "주간 생산 회의", itemId: id, title, assignee: { id: 7, name: "한팀장" },
    dueDate, dueUndetermined: dueDate === null, status: "confirmed", ...extra,
  };
}

const ITEMS = [
  item(1, "납기 조정 안내", "2026-10-05"),
  item(2, "월 말 보고", "2026-10-31"),
  item(3, "견적서 발송", "2026-10-20", { status: "pending" }),
  item(4, "A", "2026-10-21"),
  item(5, "B", "2026-10-21"),
  item(6, "C", "2026-10-21"),
  item(7, "D", "2026-10-21"),
  item(8, "E", "2026-10-21"),
];
const UNDETERMINED = [item(20, "협력사 단가 재협의", null)];

async function openCalendar(page: Page, me: Record<string, unknown>, requests: string[] = []) {
  await page.clock.setFixedTime(new Date("2026-10-15T09:00:00"));
  await signIn(page, me);
  await page.route("**/api/me/todos", (route) => json(route, 200, TODOS));
  await page.route("**/api/me/notices", (route) => json(route, 200, []));
  await page.route("**/api/me/calendar**", (route) => {
    const url = new URL(route.request().url());
    requests.push(url.search);
    const from = url.searchParams.get("from") ?? "";
    const to = url.searchParams.get("to") ?? "";
    const inRange = ITEMS.filter((i) => i.dueDate !== null && i.dueDate >= from && i.dueDate <= to);
    return json(route, 200, {
      from, to, scope: url.searchParams.get("scope"),
      dated: { total: inRange.length, items: inRange }, undetermined: { total: UNDETERMINED.length, items: UNDETERMINED },
    });
  });
  await page.route("**/api/meetings/41", (route) =>
    json(route, 200, {
      id: 41, title: "주간 생산 회의", heldAt: "2026-10-01T10:00:00+09:00", status: "awaiting_confirmation",
      actionItems: [
        { id: 1, title: "납기 조정 안내", assignee: { id: 7, name: "한팀장" }, dueDate: "2026-10-05", dueUndetermined: false, status: "confirmed",
          evidenceStartSec: 73, evidenceQuote: "납기는 다음 주 월요일로 조정합니다.", needsCompletion: false, missingFields: [] },
        { id: 3, title: "견적서 발송", assignee: { id: 7, name: "한팀장" }, dueDate: "2026-10-20", dueUndetermined: false, status: "pending",
          evidenceStartSec: null, evidenceQuote: "견적서는 화요일까지 보내겠습니다.", needsCompletion: false, missingFields: [] },
      ],
    }),
  );
  await page.goto("/v2");
  await page.getByRole("button", { name: "캘린더" }).click();
  await expect(page.getByRole("grid", { name: /마감일 캘린더/ })).toBeVisible();
}

test.describe("개인별 할 일 캘린더", () => {
  test("월간 보기: 마감일 칸에 업무가 들어가고 기한 미확정은 별도 목록이다", async ({ page }) => {
    await openCalendar(page, MANAGER);
    await expect(page.getByRole("heading", { name: "2026년 10월" })).toBeVisible();
    const cell = page.getByRole("gridcell", { name: /10월 5일 .*업무 1건/ });
    await expect(cell.getByRole("button", { name: /납기 조정 안내/ })).toBeVisible();
    await expect(page.getByRole("gridcell", { name: /10월 31일 .*업무 1건/ }).getByRole("button", { name: /월 말 보고/ })).toBeVisible();
    const undetermined = page.getByRole("region", { name: "기한 미확정 업무" });
    await expect(undetermined.getByRole("button", { name: /협력사 단가 재협의/ })).toBeVisible();
    await expect(page.getByRole("grid").getByRole("button", { name: /협력사 단가 재협의/ })).toHaveCount(0);
  });

  test("월간·주간 전환과 이동은 보이는 기간만 조회한다", async ({ page }) => {
    const requests: string[] = [];
    await openCalendar(page, MANAGER, requests);
    await page.getByRole("button", { name: "주간" }).click();
    await expect(page.getByRole("heading", { name: /10월 11일 일요일 ~ 10월 17일 토요일/ })).toBeVisible();
    const last = requests[requests.length - 1];
    expect(last).toContain("from=2026-10-11");
    expect(last).toContain("to=2026-10-17");
    await page.getByRole("button", { name: "다음 주" }).click();
    await expect.poll(() => requests[requests.length - 1]).toContain("from=2026-10-18");
    await page.getByRole("button", { name: "월간" }).click();
    await page.getByRole("button", { name: "다음 달" }).click();
    await expect(page.getByRole("heading", { name: "2026년 11월" })).toBeVisible();
  });

  test("한 칸에 업무가 많으면 +N건으로 접고 누르면 목록이 열린다", async ({ page }) => {
    await openCalendar(page, MANAGER);
    const cell = page.getByRole("gridcell", { name: /10월 21일 .*업무 5건/ });
    await expect(cell.getByRole("button", { name: /^[A-E] · / })).toHaveCount(3);
    await cell.getByRole("button", { name: /2건 더 보기/ }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("button", { name: /^E · / })).toBeVisible();
    await expect(dialog.getByRole("button", { name: /^A · / })).toBeVisible();
  });

  test("카드를 누르면 회의록·업무 상세(근거 시각·인용문)가 모달로 보인다", async ({ page }) => {
    await openCalendar(page, MANAGER);
    await page.getByRole("gridcell", { name: /10월 5일 / }).getByRole("button", { name: /납기 조정 안내/ }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText("회의록: 주간 생산 회의")).toBeVisible();
    await expect(dialog.getByText("00:01:13")).toBeVisible();
    await expect(dialog.getByText("납기는 다음 주 월요일로 조정합니다.")).toBeVisible();
    await expect(dialog.getByText("근거 미검증")).toHaveCount(0);
  });

  test("인용문은 있는데 위치를 찾지 못한 업무에는 근거 미검증 배지가 붙는다", async ({ page }) => {
    await openCalendar(page, MANAGER);
    await page.getByRole("gridcell", { name: /10월 20일 / }).getByRole("button", { name: /견적서 발송/ }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText("견적서는 화요일까지 보내겠습니다.")).toBeVisible();
    await expect(dialog.getByText("근거 미검증")).toBeVisible();
    await expect(dialog.getByText("확정 대기")).toBeVisible();
  });

  test("담당자(staff)에게는 전체 업무 전환이 없고 내 업무만 조회한다", async ({ page }) => {
    const requests: string[] = [];
    await openCalendar(page, STAFF, requests);
    await expect(page.getByRole("button", { name: "전체 업무" })).toHaveCount(0);
    expect(requests.every((query) => query.includes("scope=self"))).toBe(true);
  });

  test("관리자는 전체 업무로 전환하면 scope=all 로 조회하고 담당자 이름이 보인다", async ({ page }) => {
    const requests: string[] = [];
    await openCalendar(page, MANAGER, requests);
    await page.getByRole("button", { name: "전체 업무" }).click();
    await expect.poll(() => requests[requests.length - 1]).toContain("scope=all");
    await expect(page.getByRole("gridcell", { name: /10월 5일 / }).getByRole("button", { name: /담당 한팀장/ })).toBeVisible();
  });

  test("방향키로 날짜 칸을 이동한다", async ({ page }) => {
    await openCalendar(page, MANAGER);
    const today = page.getByRole("button", { name: /10월 15일 목요일, 업무 0건/ });
    await today.focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByRole("button", { name: /10월 16일 금요일/ })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(page.getByRole("button", { name: /10월 23일 금요일/ })).toBeFocused();
  });
});

test.describe("개인별 할 일 캘린더(좁은 화면)", () => {
  for (const width of [375, 768]) {
    test(`${width}px 에서 페이지가 가로로 넘치지 않는다`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await openCalendar(page, MANAGER);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
    });
  }
});

test.describe("개인별 할 일 캘린더(시간대)", () => {
  test.use({ timezoneId: "Pacific/Auckland" });

  test("날짜가 가장 앞선 시간대에서도 마감일이 하루 밀리지 않는다", async ({ page }) => {
    await openCalendar(page, MANAGER);
    await expect(page.getByRole("gridcell", { name: /10월 31일 .*업무 1건/ }).getByRole("button", { name: /월 말 보고/ })).toBeVisible();
    await expect(page.getByRole("gridcell", { name: /11월 1일 .*업무 0건/ })).toBeVisible();
  });
});
