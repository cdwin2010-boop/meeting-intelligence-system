/*
 * v2 왼쪽 메뉴 "처리 현황" E2E. 실제 백엔드 없이 page.route 로 가로챈다(데이터는 모두 가상).
 * 자동 새로고침 주기는 playwright 설정이 NEXT_PUBLIC_PROCESSING_REFRESH_SEC=1 로 줄여 둔다.
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const ME = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const OTHER = { id: 8, name: "정관리", rank: "manager", tenantId: 1 };

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

type Item = {
  meetingId: number; jobId: number; title: string; status: string; errorCode: string | null; elapsedSec: number;
  finishedAt: string | null; canReprocess: boolean;
  /** 가짜 서버용: 있으면 응답 시점마다 elapsedSec 을 (지금 - 이 시각) + 기본값으로 다시 계산한다(서버가 경과 초를 계산해 주는 것을 흉내) */
  _startMs?: number;
};

const item = (jobId: number, extra: Partial<Item> = {}): Item => ({
  meetingId: 100 + jobId, jobId, title: `회의 ${jobId}`, status: "completed", errorCode: null, elapsedSec: 12, finishedAt: "2026-10-06T01:00:00Z",
  canReprocess: false, ...extra,
});

const detailOf = (id: number, status: string, actionItems: unknown[] = []) => ({
  id, title: `회의 ${id - 100}`, heldAt: "2026-10-01T01:00:00Z", summary: "", decisions: [], status, confirmKind: null, confirmedBy: null,
  confirmedAt: null, firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: null, registeredBy: { id: 7, name: "한팀장" },
  origin: "audio_minutes", participants: [], guestParticipants: [], recentEvents: [], phase: "active",
  minutes: { purpose: "내용없음", discussion: "내용없음", decisions: "내용없음", risks: "내용없음", nextAgenda: "내용없음", engine: "gemini", updatedBy: null, updatedAt: null },
  processing: null, actionItems,
});

interface Server {
  items: Item[];
  gets: number;
  details: Record<number, () => unknown>;
  reprocessCalls: number[];
  account: typeof ME;
}

async function start(page: Page, items: Item[], account = ME): Promise<Server> {
  const server: Server = { items, gets: 0, details: {}, reprocessCalls: [], account };
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, server.account) : json(route, 401, { detail: "인증 필요" }),
  );
  await page.route("**/api/me/processing", (route) => {
    server.gets += 1;
    const body = server.items.map(({ _startMs, ...rest }) => (_startMs && rest.status === "running" ? { ...rest, elapsedSec: rest.elapsedSec + Math.floor((Date.now() - _startMs) / 1000) } : rest));
    return json(route, 200, body);
  });
  await page.route(/\/api\/meetings\/(\d+)$/, (route) => {
    const id = Number(/meetings\/(\d+)$/.exec(route.request().url())![1]);
    return json(route, 200, withAllowed((server.details[id] ?? (() => detailOf(id, "confirmed")))() as never, server.account));
  });
  await page.route(/\/api\/meetings\/\d+\/(transcript|audio-url)$/, (route) => json(route, 404, { detail: "없음" }));
  await page.route(/\/api\/meetings\/\d+\/speakers$/, (route) => json(route, 200, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route(/\/api\/meetings\/\d+\/change-requests$/, (route) => json(route, 200, []));
  await page.route("**/api/accounts", (route) => json(route, 200, []));
  await page.route("**/api/meetings?*", (route) => json(route, 200, { items: [], total: 0, page: 1, size: 20, availablePhases: ["active", "ended"] }));
  await page.route(/\/api\/meetings\/(\d+)\/reprocess$/, (route) => {
    const id = Number(/meetings\/(\d+)\//.exec(route.request().url())![1]);
    server.reprocessCalls.push(id);
    server.items = server.items.map((it) => (it.meetingId === id ? { ...it, jobId: it.jobId + 50, status: "running", errorCode: null, elapsedSec: 0, canReprocess: false } : it));
    return json(route, 202, { meetingId: id, jobId: 99 });
  });
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
  return server;
}

const panel = (page: Page) => page.getByLabel("처리 현황", { exact: true });
const openHome = async (page: Page) => {
  await page.goto("/v2");
  await expect(page.getByRole("navigation", { name: "주 메뉴" })).toBeVisible();
};

test.describe("처리 현황: 표시와 복원", () => {
  test("업로드 직후 항목이 나타나고 경과 시간이 증가, 완료되면 '처리 완료 [열기]' → 열기로 이동", async ({ page }) => {
    const server = await start(page, []);
    await page.route("**/api/meetings/upload", (route) => {
      server.items = [item(5, { meetingId: 77, title: "업로드한 회의", status: "running", elapsedSec: 5, finishedAt: null, _startMs: Date.now() })];
      return json(route, 202, { meetingId: 77, jobId: 5 });
    });
    server.details[77] = () => detailOf(77, "processing");
    await page.goto("/v2/upload");
    await page.getByLabel("음성 파일 선택").setInputFiles({ name: "a.m4a", mimeType: "audio/mp4", buffer: Buffer.from("fake-audio") });
    await page.getByLabel("회의명").fill("업로드한 회의");
    await page.getByLabel("회의 날짜").fill("2026-10-01");
    await page.getByLabel("회의 시각").fill("13:20");
    await page.getByRole("button", { name: "올리고 분석 시작" }).click();
    await expect(page).toHaveURL(/\/v2\/meetings\/77$/);
    // 업로드 직후 바로 조회해 항목이 즉시 나타난다
    await expect(panel(page)).toContainText("업로드한 회의");
    await expect(panel(page)).toContainText(/처리 중 · 경과 00:0[5-9]/);
    const first = (await panel(page).innerText()).match(/경과 (\d\d:\d\d)/)![1];
    await expect.poll(async () => (await panel(page).innerText()).match(/경과 (\d\d:\d\d)/)?.[1], { timeout: 6000 }).not.toBe(first); // 경과 시간 증가
    // 완료
    server.items = [item(5, { meetingId: 77, title: "업로드한 회의", status: "completed" })];
    await expect(panel(page)).toContainText("처리 완료", { timeout: 8000 });
    server.details[77] = () => detailOf(77, "confirmed");
    await page.goto("/v2");
    await panel(page).getByRole("button", { name: "업로드한 회의 열기" }).click();
    await expect(page).toHaveURL(/\/v2\/meetings\/77$/);
  });

  test("새로고침해도 진행·완료·실패 항목이 복원된다", async ({ page }) => {
    await start(page, [item(1, { status: "running", elapsedSec: 30, finishedAt: null }), item(2), item(3, { status: "failed", errorCode: "gemini_api_503" })]);
    await openHome(page);
    await expect(panel(page)).toContainText("처리 중 · 경과 00:3");
    await expect(panel(page)).toContainText("처리 완료");
    await expect(panel(page)).toContainText("처리 실패");
    await page.reload();
    await expect(panel(page)).toContainText("처리 중 · 경과");
    await expect(panel(page)).toContainText("처리 완료");
  });

  test("닫으면 다시 보이지 않고 새로고침해도 유지(계정별 저장)", async ({ page }) => {
    await start(page, [item(2, { title: "닫을 회의" }), item(3, { title: "남을 회의" })]);
    await openHome(page);
    await panel(page).getByRole("button", { name: "닫을 회의 닫기" }).click();
    await expect(panel(page)).not.toContainText("닫을 회의");
    await expect(panel(page)).toContainText("남을 회의");
    await page.reload();
    await expect(panel(page)).toContainText("남을 회의");
    await expect(panel(page)).not.toContainText("닫을 회의");
  });

  test("닫은 목록은 계정별: 다른 계정으로 로그인하면 그 계정의 목록만 따른다", async ({ page }) => {
    const server = await start(page, [item(2, { title: "공유 회의" })]);
    await openHome(page);
    await panel(page).getByRole("button", { name: "공유 회의 닫기" }).click();
    await expect(panel(page)).not.toContainText("공유 회의");
    server.account = OTHER; // 다른 사용자(서버는 그 사용자의 항목만 준다: 같은 작업 번호라도 별개)
    await page.reload();
    await expect(panel(page)).toContainText("공유 회의");
  });

  test("내용 없음 항목", async ({ page }) => {
    await start(page, [item(4, { status: "no_content", title: "무음 회의" })]);
    await openHome(page);
    await expect(panel(page)).toContainText("내용 없음, 파일 확인 요청");
    await expect(panel(page).getByRole("button", { name: "무음 회의 열기" })).toBeVisible();
    await expect(panel(page).getByRole("button", { name: "무음 회의 닫기" })).toBeVisible();
  });

  test("다른 사용자의 항목은 서버가 주지 않으므로 보이지 않는다(내 항목만)", async ({ page }) => {
    await start(page, []);
    await openHome(page);
    await expect(panel(page).getByRole("heading", { name: "처리 현황" })).toHaveCount(0);
    await expect(panel(page).locator("li")).toHaveCount(0);
  });
});

test.describe("처리 현황: 실패와 재처리", () => {
  test("실패 이유와 오류 코드, [다시 처리]는 canReprocess 가 true 일 때만, 확인 창 뒤 처리 중으로", async ({ page }) => {
    const server = await start(page, [
      item(1, { title: "재처리 가능", status: "failed", errorCode: "gemini_api_503", canReprocess: true }),
      item(2, { title: "재처리 불가", status: "failed", errorCode: "gemini_key_missing", canReprocess: false }),
    ]);
    await openHome(page);
    await expect(panel(page)).toContainText("Gemini 서버가 일시적으로 응답하지 않았습니다. 잠시 뒤 다시 처리해 보세요.");
    await expect(panel(page)).toContainText("gemini_api_503");
    await expect(panel(page)).toContainText("Gemini 키 설정을 확인해야 합니다.");
    await expect(panel(page)).toContainText("gemini_key_missing");
    await expect(panel(page).getByRole("button", { name: /다시 처리/ })).toHaveCount(1);
    await expect(panel(page).getByRole("button", { name: "재처리 불가 다시 처리" })).toHaveCount(0);
    await panel(page).getByRole("button", { name: "재처리 가능 다시 처리" }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog.getByRole("button", { name: "취소" })).toBeFocused();
    await expect(dialog).toContainText("비용이 생길 수 있습니다");
    await dialog.getByRole("button", { name: "재처리하기" }).click();
    await expect(dialog).toBeHidden();
    expect(server.reprocessCalls).toEqual([101]);
    await expect(panel(page)).toContainText("처리 중 · 경과"); // 바로 다시 조회해 처리 중으로 바뀐다
  });
});

test.describe("처리 현황: 3건 제한과 폴링", () => {
  test("4건 이상이면 3건만 보이고 '외 N건'(펼치기)", async ({ page }) => {
    await start(page, [item(1), item(2), item(3), item(4), item(5)]);
    await openHome(page);
    await expect(panel(page).locator("li")).toHaveCount(3);
    const more = panel(page).getByRole("button", { name: "외 2건" });
    await expect(more).toHaveAttribute("aria-expanded", "false");
    await more.focus();
    await page.keyboard.press("Enter"); // 키보드로 펼친다
    await expect(panel(page).locator("li")).toHaveCount(5);
    await expect(panel(page).getByRole("button", { name: "접기" })).toHaveAttribute("aria-expanded", "true");
  });

  test("진행 항목이 없으면 폴링을 멈추고, 있으면 주기적으로 조회한다", async ({ page }) => {
    const server = await start(page, [item(2)]);
    await openHome(page);
    await expect(panel(page)).toContainText("처리 완료");
    await page.waitForTimeout(800);
    const settled = server.gets;
    await page.waitForTimeout(3000);
    expect(server.gets).toBe(settled); // 처리 중인 항목이 없으니 더 부르지 않는다
    server.items = [item(2, { status: "running", finishedAt: null })];
    await page.reload();
    const afterReload = server.gets;
    await expect.poll(() => server.gets, { timeout: 8000 }).toBeGreaterThan(afterReload + 1); // 진행 중에는 계속 조회
  });
});

test.describe("처리 현황: 저장소와 접근성", () => {
  test("브라우저 저장소 접근이 막혀도 정상 동작(닫기는 이번 방문 동안만)", async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(window, "localStorage", { get() { throw new Error("blocked"); } });
    });
    await start(page, [item(2, { title: "차단 환경 회의" })]);
    await openHome(page);
    await expect(panel(page)).toContainText("차단 환경 회의");
    await panel(page).getByRole("button", { name: "차단 환경 회의 닫기" }).click();
    await expect(panel(page)).not.toContainText("차단 환경 회의");
  });

  test("aria-live(polite) 영역이 있고 상태가 바뀌면 안내문이 갱신된다", async ({ page }) => {
    const server = await start(page, [item(1, { title: "안내 회의", status: "running", finishedAt: null, elapsedSec: 3 })]);
    await openHome(page);
    await expect(page.locator('section[aria-label="처리 현황"][aria-live="polite"]')).toHaveCount(1);
    await expect(panel(page)).toContainText("처리 중 · 경과"); // 처리 중 상태를 먼저 받은 뒤에 완료로 바꾼다
    server.items = [item(1, { title: "안내 회의", status: "completed" })];
    await expect(page.getByLabel("처리 상태 변경 안내")).toHaveText("안내 회의: 처리 완료", { timeout: 8000 });
  });
});

test.describe("처리 현황: 완료 시 상세의 조용한 갱신", () => {
  test("열린 팝업·입력값·화면 상태가 유지되고 전체 새로고침이 없다", async ({ page }) => {
    const server = await start(page, [item(41 - 100 + 100, { meetingId: 41, title: "회의 41", status: "running", finishedAt: null, elapsedSec: 4 })]);
    const state = { done: false, gets: 0 };
    server.details[41] = () => {
      state.gets += 1;
      return state.done
        ? detailOf(41, "confirmed", [{
            id: 301, title: "견적서 송부", assignee: { id: 9, name: "이서연" }, dueDate: "2026-10-09", dueUndetermined: false, status: "pending",
            confirmKind: null, evidenceStartSec: null, evidenceQuote: null, needsCompletion: false, missingFields: [], origin: "ai",
          }])
        : detailOf(41, "processing");
    };
    await page.goto("/v2/meetings/41");
    await expect(page.getByLabel("처리 상태", { exact: true })).toContainText("음성을 처리하는 중입니다");
    await page.evaluate(() => ((window as unknown as { __marker: string }).__marker = "same-page")); // 전체 새로고침이 있으면 사라진다
    await page.getByRole("button", { name: "회의록 전체 수정 요청" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("textbox").fill("입력 중인 수정 요청 글");
    const before = state.gets;
    state.done = true;
    server.items = [item(141, { meetingId: 41, title: "회의 41", status: "completed" })];
    await expect.poll(() => state.gets, { timeout: 8000 }).toBeGreaterThan(before); // 상세를 다시 불러왔다
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("textbox")).toHaveValue("입력 중인 수정 요청 글");
    expect(await page.evaluate(() => (window as unknown as { __marker?: string }).__marker)).toBe("same-page");
    await page.keyboard.press("Escape");
    await expect(page.getByRole("table", { name: "업무 원장" }).locator("tbody tr").filter({ hasText: "견적서 송부" })).toBeVisible();
    await expect(page.getByLabel("처리 상태", { exact: true })).toHaveCount(0);
  });
});
