/*
 * v2 회의록 상세(조회 전용) E2E. 실제 백엔드 없이 page.route 로 v2 API(/api/auth/me, /api/meetings/*)를 가로채 가짜로 응답한다.
 * 데이터는 모두 가상이고, 가짜 토큰은 고정 문자열이다.
 */
import { expect, test, type Page, type Route } from "@playwright/test";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const ACCOUNT = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const item = (overrides: Record<string, unknown>) => ({
  id: 1,
  title: "",
  assignee: null,
  dueDate: null,
  dueUndetermined: false,
  status: "pending",
  confirmKind: null,
  evidenceStartSec: null,
  evidenceQuote: null,
  needsCompletion: false,
  missingFields: [],
  ...overrides,
});

const DETAIL = {
  id: 41,
  title: "주간 생산 현안 회의",
  heldAt: "2026-10-01T01:00:00Z",
  summary: "3호기 납기 지연 원인은 협력사 부품 재입고 지연입니다.",
  decisions: ["3호기 납기를 10월 24일로 조정합니다."],
  status: "awaiting_confirmation",
  confirmKind: null,
  confirmedBy: null,
  confirmedAt: null,
  firstCreatedAt: "2026-10-01T02:00:00Z",
  autoConfirmAt: "2026-10-06T01:00:00Z",
  registeredBy: { id: 3, name: "김대리" },
  origin: "audio",
  participants: [
    { id: 3, name: "김대리" },
    { id: 7, name: "한팀장" },
  ],
  actionItems: [
    item({
      id: 101,
      title: "협력사 부품 재입고 일정 확인 후 공유",
      assignee: { id: 3, name: "김대리" },
      dueDate: "2026-10-05",
      evidenceStartSec: 760,
      evidenceQuote: "김대리가 협력사에 재입고 날짜 확인해서 월요일까지 공유해 주세요.",
    }),
    item({ id: 102, title: "고객사에 납기 조정 안내", assignee: { id: 7, name: "한팀장" }, dueUndetermined: true, status: "confirmed" }),
    item({ id: 103, title: "설비 점검 일정 수립", needsCompletion: true, missingFields: ["assignee", "dueDate"], evidenceStartSec: 2122 }),
  ],
  recentEvents: [],
};

const TRANSCRIPT = {
  fullText: "3호기 납기가 지연되는 원인부터 정리하겠습니다. 네, 확인하겠습니다.",
  segments: [
    { speaker: "화자1", start_sec: 192, end_sec: 196, text: "3호기 납기가 지연되는 원인부터 정리하겠습니다." },
    { speaker: "화자2", start_sec: 768, end_sec: 770, text: "네, 확인하겠습니다." },
  ],
  sttProvider: "fake",
};

/** me 는 가짜 토큰일 때만 성공. 상세·전사문·목록은 handler 로 테스트별 응답 */
async function openDetail(
  page: Page,
  path: string,
  handlers: { detail: (route: Route) => unknown; transcript?: (route: Route) => unknown },
) {
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}`
      ? json(route, 200, ACCOUNT)
      : json(route, 401, { detail: "인증이 필요합니다" }),
  );
  await page.route("**/api/meetings?*", (route) => json(route, 200, { items: [], total: 0, page: 1, size: 20 }));
  await page.route(/\/api\/meetings\/\d+$/, (route) => handlers.detail(route) as Promise<void>);
  await page.route(/\/api\/meetings\/\d+\/transcript$/, (route) =>
    (handlers.transcript ?? ((r: Route) => json(r, 404, { detail: "전사문이 없습니다" })))(route) as Promise<void>,
  );
  // 화자 요약·화자 지정 팝업이 부르는 API(이 스펙의 검증 대상 아님): 화자 없음·계정 없음으로 응답
  await page.route(/\/api\/meetings\/\d+\/speakers$/, (route) => json(route, 200, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route(/\/api\/meetings\/\d+\/change-requests$/, (route) => json(route, 200, []));
  await page.route("**/api/accounts", (route) => json(route, 200, []));
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto(path);
}

test.describe("v2 회의록 상세", () => {
  test("상세 표시: 제목·일시·상태, 업무 원장, 근거, 전사문 접고 펼치기", async ({ page }) => {
    let transcriptCalls = 0;
    await openDetail(page, "/v2/meetings/41", {
      detail: (route) => json(route, 200, DETAIL),
      transcript: (route) => {
        transcriptCalls += 1;
        return json(route, 200, TRANSCRIPT);
      },
    });

    await expect(page.getByRole("heading", { level: 1, name: "주간 생산 현안 회의" })).toBeVisible();
    const header = page.locator("main header");
    await expect(header).toContainText("확정 대기");
    await expect(header).toContainText("2026-10-01 10:00"); // Asia/Seoul
    await expect(page.getByRole("region", { name: "회의 개요" })).toContainText("자동 확정 예정");
    // 요약·결정사항 자리는 5개 항목으로 대체(이 응답엔 minutes 가 없어 생성 전 안내)
    await expect(page.getByRole("region", { name: "회의 개요" })).toContainText("아직 생성되지 않았습니다");

    const rows = page.getByRole("table", { name: "업무 원장" }).locator("tbody tr");
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0)).toContainText("협력사 부품 재입고 일정 확인 후 공유");
    await expect(rows.nth(0)).toContainText("김대리");
    await expect(rows.nth(0)).toContainText("2026-10-05");
    await expect(rows.nth(0)).toContainText("00:12:40");
    await expect(rows.nth(0)).toContainText("월요일까지 공유해 주세요");
    await expect(rows.nth(1)).toContainText("확정됨");
    await expect(rows.nth(1)).toContainText("미확정");
    await expect(rows.nth(2)).toContainText("보완 필요");
    await expect(rows.nth(2)).toContainText("담당자 필요");
    await expect(rows.nth(2)).toContainText("기한 필요");

    // 근거 타임스탬프는 누르면 그 위치부터 재생하는 버튼(재생기 동작은 e2e/v2-audio-player.spec.ts). 확정·수정 요청 버튼은 e2e/v2-confirm.spec.ts 에서 확인
    await expect(rows.nth(0).getByRole("button", { name: "00:12:40부터 재생" })).toBeVisible();

    // 전사문: 처음엔 접혀 있고 요청도 없음 → 펼치면 1회 조회 → 접었다 다시 펼쳐도 재조회 없음
    const toggle = page.getByRole("button", { name: /전사문/ });
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(transcriptCalls).toBe(0);
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    const panel = page.getByRole("region", { name: "전사문" });
    await expect(panel).toContainText("00:03:12");
    await expect(panel).toContainText("화자1");
    await expect(panel).toContainText("3호기 납기가 지연되는 원인부터 정리하겠습니다.");
    await toggle.click();
    await expect(panel).not.toContainText("화자1");
    await toggle.click();
    await expect(panel).toContainText("화자2");
    expect(transcriptCalls).toBe(1);
  });

  test("오류 → 오류 문구와 다시 시도", async ({ page }) => {
    let fail = true;
    await openDetail(page, "/v2/meetings/41", {
      detail: (route) => (fail ? json(route, 500, { detail: "서버 오류" }) : json(route, 200, DETAIL)),
    });
    const alert = page.getByRole("alert").filter({ hasText: "회의록을 불러오지 못했습니다" });
    await expect(alert).toBeVisible();
    await expect(alert).toContainText("서버 오류");
    fail = false;
    await alert.getByRole("button", { name: "다시 시도" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "주간 생산 현안 회의" })).toBeVisible();
  });

  test("없는 회의록(404) → 찾을 수 없음 안내, 다시 시도 없음", async ({ page }) => {
    await openDetail(page, "/v2/meetings/999", {
      detail: (route) => json(route, 404, { detail: "회의록을 찾을 수 없습니다" }),
    });
    await expect(page.getByRole("heading", { level: 1, name: "회의록을 찾을 수 없습니다" })).toBeVisible();
    await expect(page.getByRole("button", { name: "다시 시도" })).toHaveCount(0);
  });

  test("목록으로 돌아가기 링크 → /v2/meetings", async ({ page }) => {
    await openDetail(page, "/v2/meetings/41", { detail: (route) => json(route, 200, DETAIL) });
    await expect(page.getByRole("heading", { level: 1, name: "주간 생산 현안 회의" })).toBeVisible();
    await page.getByRole("link", { name: "← 회의록 목록으로" }).click();
    await expect(page).toHaveURL(/\/v2\/meetings$/);
    await expect(page.getByRole("heading", { level: 1, name: "회의록" })).toBeVisible();
  });
});
