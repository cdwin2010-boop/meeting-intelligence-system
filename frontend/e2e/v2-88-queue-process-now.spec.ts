/*
 * 작업 88-2 E2E: 변환 대기열 표시(대기/진행/완료/실패 4단계·앞에 N건·우선 처리·처리기 응답 없음)와 [즉시 변환] 버튼.
 * 백엔드 없이 page.route 로 응답한다. 응답 모양은 docs/contracts/process-now-v1.json 을 따른다(데이터는 모두 가상).
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { ME_MANAGER, ME_STAFF, horizontalOverflow, json, loginWithMocks, processNowBody, queuedDetail } from "./helpers/queue-mock";

const WAITING = "시스템의 선행 작업완료 후 처리하겠습니다.";
const WORKER_DOWN = "처리기 응답이 없어 대기 중입니다. 관리자에게 문의해 주세요.";

async function openDetail(page: Page, detail: unknown, me = ME_MANAGER) {
  await loginWithMocks(page, { me, detail });
  await page.goto("/v2/meetings/41");
  await expect(page.getByRole("heading", { level: 1, name: "주간 생산 현안 회의" })).toBeVisible();
}

const banner = (page: Page) => page.getByRole("status", { name: "처리 상태" });

// ---------------- 상태 표시 ----------------
test("대기 중: 대기 중 칩·앞에 N건·(우선 처리 아님)", async ({ page }) => {
  await openDetail(page, queuedDetail({}, { queueAhead: 2 }));
  await expect(banner(page)).toContainText("대기 중");
  await expect(banner(page)).toContainText("앞에 2건");
  await expect(banner(page)).not.toContainText("우선 처리");
  await expect(banner(page)).toContainText("음성을 처리하는 중입니다. 완료되면 업무가 표시됩니다."); // 기존 문구 그대로
});

test("우선 처리 건: 우선 처리 표시", async ({ page }) => {
  await openDetail(page, queuedDetail({}, { queueAhead: 0, prioritized: true }, []));
  await expect(banner(page)).toContainText("우선 처리");
  await expect(banner(page)).toContainText("앞에 0건");
});

test("진행 중: 처리 중 칩, 앞에 N건·처리기 안내는 대기 상태에서만", async ({ page }) => {
  await openDetail(page, queuedDetail({}, { status: "running", startedAt: "2026-10-01T02:05:00Z", queueAhead: 0, workerAlive: false }, []));
  await expect(banner(page)).toContainText("처리 중");
  await expect(banner(page)).not.toContainText("앞에");
  await expect(page.getByText(WORKER_DOWN)).toHaveCount(0);
});

test("처리기 응답 없음(workerAlive=false)이고 대기 중이면 안내 문구", async ({ page }) => {
  await openDetail(page, queuedDetail({}, { workerAlive: false }));
  await expect(banner(page).getByText(WORKER_DOWN)).toBeVisible();
});

test("실패: 처리 실패 칩과 기존 실패 문구", async ({ page }) => {
  await openDetail(
    page,
    queuedDetail({ status: "failed" }, { status: "failed", errorCode: "timeout", queueAhead: null, workerAlive: null, startedAt: "2026-10-01T02:05:00Z", finishedAt: "2026-10-01T02:30:00Z" }, ["reprocess_meeting"]),
  );
  const alert = page.getByRole("alert", { name: "처리 상태" });
  await expect(alert).toContainText("처리 실패");
  await expect(alert).toContainText("처리에 실패했습니다 ·");
});

// ---------------- 왼쪽 메뉴 처리 현황 ----------------
test("왼쪽 메뉴: 대기·진행·완료·실패 4단계가 같은 칩으로 보이고 대기 항목에 앞에 N건·우선 처리", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.route("**/api/me/processing", (route) =>
    json(route, 200, [
      { meetingId: 1, jobId: 11, title: "대기 회의", status: "queued", errorCode: null, elapsedSec: 5, finishedAt: null, canReprocess: false, queueAhead: 3, prioritized: false, waitingForPrior: true, workerAlive: true },
      { meetingId: 2, jobId: 12, title: "진행 회의", status: "running", errorCode: null, elapsedSec: 65, finishedAt: null, canReprocess: false, queueAhead: 0, prioritized: false, waitingForPrior: false, workerAlive: true },
      { meetingId: 3, jobId: 13, title: "완료 회의", status: "completed", errorCode: null, elapsedSec: 90, finishedAt: "2026-10-01T02:30:00Z", canReprocess: false },
      { meetingId: 4, jobId: 14, title: "실패 회의", status: "failed", errorCode: "timeout", elapsedSec: 30, finishedAt: "2026-10-01T02:30:00Z", canReprocess: false },
    ]),
  );
  await loginWithMocks(page);
  await page.goto("/v2");
  await page.getByRole("button", { name: /외 1건/ }).click();
  const panel = page.getByRole("region", { name: "처리 현황" });
  await expect(panel).toContainText("대기 중 · 경과");
  await expect(panel).toContainText("앞에 3건");
  await expect(panel).toContainText("처리 중 · 경과");
  await expect(panel).toContainText("처리 완료");
  await expect(panel).toContainText("처리 실패 · ");
  // 모두 chip 모양(StatusChip) 한 컴포넌트
  expect(await panel.locator(".mn-chip").count()).toBeGreaterThanOrEqual(4);
});

test("왼쪽 메뉴: 서버가 process_now 를 허용한 대기 항목에만 [즉시 변환], 누르면 서버 문구 표시", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const posts: string[] = [];
  await page.route("**/api/me/processing", (route) =>
    json(route, 200, [
      { meetingId: 41, jobId: 77, title: "허용 회의", status: "queued", errorCode: null, elapsedSec: 5, finishedAt: null, canReprocess: false, queueAhead: 1, prioritized: false, waitingForPrior: true, workerAlive: true },
      { meetingId: 42, jobId: 78, title: "불허 회의", status: "queued", errorCode: null, elapsedSec: 5, finishedAt: null, canReprocess: false, queueAhead: 2, prioritized: false, waitingForPrior: true, workerAlive: true },
    ]),
  );
  await loginWithMocks(page);
  await page.route(/\/api\/meetings\/41$/, (route) => json(route, 200, queuedDetail({ id: 41 })));
  await page.route(/\/api\/meetings\/42$/, (route) => json(route, 200, queuedDetail({ id: 42 }, {}, [])));
  await page.route("**/api/meetings/41/process-now", (route) => {
    posts.push(route.request().method());
    return json(route, 200, processNowBody());
  });
  await page.goto("/v2");
  const panel = page.getByRole("region", { name: "처리 현황" });
  const buttons = panel.getByRole("button", { name: "즉시 변환" });
  await expect(buttons).toHaveCount(1);
  await buttons.click();
  await expect(panel.getByText(WAITING)).toBeVisible();
  expect(posts).toEqual(["POST"]);
});

// ---------------- 즉시 변환 버튼(회의록 상세) ----------------
test("관리자·지시자: 서버가 허용 동작을 내려주면 [즉시 변환]이 보인다", async ({ page }) => {
  await openDetail(page, queuedDetail());
  await expect(banner(page).getByRole("button", { name: "즉시 변환" })).toBeVisible();
});

test("담당자(허용 동작 없음): [즉시 변환]이 아예 없다", async ({ page }) => {
  await openDetail(page, queuedDetail({}, {}, []), ME_STAFF);
  await expect(banner(page)).toBeVisible();
  await expect(page.getByRole("button", { name: "즉시 변환" })).toHaveCount(0);
});

test("누르면 요청 한 번, 요청 중에는 비활성(중복 클릭 방지), 선행 작업이 있으면 서버 문구를 그대로 보인다", async ({ page }) => {
  let posts = 0;
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await openDetail(page, queuedDetail());
  await page.route("**/api/meetings/41/process-now", async (route) => {
    posts += 1;
    await gate;
    await json(route, 200, processNowBody({ waitingForPrior: true, message: WAITING }));
  });
  const button = banner(page).getByRole("button", { name: "즉시 변환" });
  await button.click();
  const busy = banner(page).getByRole("button", { name: "요청 중…" });
  await expect(busy).toBeDisabled();
  await busy.click({ force: true }).catch(() => undefined);
  release();
  await expect(banner(page).getByText(WAITING)).toBeVisible();
  expect(posts).toBe(1);
  await expect(banner(page).getByRole("button", { name: "즉시 변환" })).toHaveCount(0);
});

test("선행 작업이 없으면(waitingForPrior=false) \"변환을 바로 시작합니다.\" 를 보이고 상태를 다시 받는다", async ({ page }) => {
  let started = false; // 즉시 변환 요청 뒤에는 서버가 진행 중으로 답한다
  let detailsAfter = 0;
  await loginWithMocks(page);
  await page.route(/\/api\/meetings\/\d+$/, (route) => {
    if (started) detailsAfter += 1;
    return json(route, 200, started ? queuedDetail({}, { status: "running", queueAhead: 0, startedAt: "2026-10-01T02:06:00Z" }, []) : queuedDetail());
  });
  await page.route(/\/api\/meetings\/\d+\/transcript$/, (route) => json(route, 404, { detail: "없음" }));
  await page.route("**/api/meetings/41/process-now", (route) => {
    started = true;
    return json(route, 200, processNowBody({ waitingForPrior: false, message: "곧 변환을 시작합니다.", queueAhead: 0 }));
  });
  await page.goto("/v2/meetings/41");
  await banner(page).getByRole("button", { name: "즉시 변환" }).click();
  await expect(banner(page).getByText("변환을 바로 시작합니다.")).toBeVisible();
  await expect(banner(page)).toContainText("처리 중"); // 상세를 다시 받아 진행 상태로 바뀜
  expect(detailsAfter).toBeGreaterThan(0);
});

for (const [status, reason] of [
  [409, "이미 변환이 진행 중입니다. 완료될 때까지 기다려 주세요."],
  [403, "관리자 또는 지시자만 즉시 변환을 요청할 수 있습니다"],
  [404, "회의록을 찾을 수 없습니다"],
] as const) {
  test(`${status}: 서버가 준 한국어 사유를 그대로 보여 준다`, async ({ page }) => {
    await openDetail(page, queuedDetail());
    await page.route("**/api/meetings/41/process-now", (route: Route) => json(route, status, { detail: reason }));
    await banner(page).getByRole("button", { name: "즉시 변환" }).click();
    await expect(banner(page).getByRole("alert")).toContainText(reason);
    await expect(banner(page).getByRole("button", { name: "즉시 변환" })).toBeVisible(); // 다시 시도할 수 있다
  });
}

for (const width of [375, 768, 1280]) {
  test(`${width}px: 대기 안내·즉시 변환 영역에서 가로 넘침이 없다`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await openDetail(page, queuedDetail({}, { workerAlive: false, prioritized: true }));
    await expect(banner(page).getByRole("button", { name: "즉시 변환" })).toBeVisible();
    await expect.poll(() => horizontalOverflow(page)).toBeLessThanOrEqual(0);
  });
}
