import { expect, test, type Page, type Route } from "./helpers/test";
import { withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const THEME_KEY = "mi.v2.theme";
const ME = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const item = (overrides: Record<string, unknown>) => ({
  id: 1, title: "", assignee: { id: 7, name: "한팀장" }, dueDate: "2026-10-05", dueUndetermined: false, status: "pending", confirmKind: null,
  evidenceStartSec: null, evidenceQuote: null, needsCompletion: false, missingFields: [], origin: "ai", ...overrides,
});

// 근거 시각은 구간 시작 시각과 같은 초 단위 숫자다. 구간 i 의 시작 = (i + 1) * 10 초(600 구간)
const SEGMENTS = Array.from({ length: 600 }, (_, i) => ({ speaker: i % 2 ? "화자2" : "화자1", start_sec: (i + 1) * 10, end_sec: (i + 1) * 10 + 9, text: `발언 ${i} 번 내용입니다` }));
const LONG_TITLE = "협력사 부품 재입고 일정을 확인한 뒤 생산관리팀과 구매팀에 공유하고 납기 조정안까지 정리해서 보고하기";
const LONG_QUOTE = "김대리가 협력사에 재입고 날짜를 확인해서 월요일까지 공유해 주세요. 그리고 구매팀과 생산관리팀은 그 일정에 맞춰 3호기 납기 조정안을 같이 만들어 주시고, 고객사에는 수요일 오전에 먼저 전화로 사정을 설명한 다음 서면으로도 안내문을 보내 주시기 바랍니다.";

const MINUTES = {
  purpose: "3호기 납기 지연 원인 점검", discussion: "협력사 부품 재입고 지연이 주된 원인으로 확인됨", decisions: "납기를 10월 24일로 조정", risks: "내용없음",
  nextAgenda: "설비 점검 일정", engine: "gemini", updatedBy: null, updatedAt: null,
};

const BASE_DETAIL = {
  id: 41, title: "주간 생산 현안 회의", heldAt: "2026-10-01T01:00:00Z", summary: "요약", decisions: [], status: "awaiting_confirmation", confirmKind: null,
  confirmedBy: null, confirmedAt: null, firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: "2026-10-06T01:00:00Z", registeredBy: { id: 3, name: "김대리" },
  origin: "audio", participants: [{ id: 3, name: "김대리" }, { id: 7, name: "한팀장" }], guestParticipants: ["외부 참석자"],
  actionItems: [
    item({ id: 101, title: "업무 A", evidenceStartSec: 3000, evidenceQuote: "A 인용" }),
    item({ id: 102, title: "업무 B", evidenceStartSec: 3504.5, evidenceQuote: "B 인용" }),
    item({ id: 103, title: "업무 C", evidenceStartSec: null, evidenceQuote: "등록자 직권 지정", origin: "manual" }),
    item({ id: 104, title: "업무 D", evidenceStartSec: 5, evidenceQuote: "D 인용" }),
    item({ id: 105, title: "업무 E", evidenceStartSec: 6000, evidenceQuote: "E 인용" }),
  ],
  recentEvents: [], phase: "active", processing: null, minutes: MINUTES,
};

interface Options { transcript?: (route: Route) => unknown; detail?: unknown }

async function open(page: Page, options: Options = {}) {
  await page.route("**/api/auth/me", (route) => (route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, ME) : json(route, { detail: "x" }, 401)));
  await page.route("**/api/meetings?*", (route) => json(route, { items: [], total: 0, page: 1, size: 20, availablePhases: ["active"] }));
  await page.route(/\/api\/meetings\/\d+$/, (route) => json(route, withAllowed((options.detail ?? DETAIL) as never, ME)));
  await page.route(/\/api\/meetings\/\d+\/transcript$/, (route) =>
    options.transcript ? (options.transcript(route) as Promise<void>) : json(route, { fullText: "x", segments: SEGMENTS, sttProvider: "fake", speakers: [] }),
  );
  await page.route(/\/api\/meetings\/\d+\/(speakers)$/, (route) => json(route, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route(/\/api\/meetings\/\d+\/change-requests$/, (route) => json(route, []));
  await page.route("**/api/me/processing", (route) => json(route, []));
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto("/v2/meetings/41");
  await expect(page.getByRole("heading", { level: 1, name: "주간 생산 현안 회의" })).toBeVisible();
}

const deepLink = (page: Page, title: string, time: string) => page.getByRole("button", { name: `근거 위치 보기: ${title} ${time}` });
const panel = (page: Page) => page.getByRole("region", { name: "전사문" });
const row = (page: Page, index: number) => page.locator(`[data-segment-index="${index}"]`);
const rowInView = (page: Page, index: number) =>
  page.evaluate((i) => {
    const el = document.querySelector(`[data-segment-index="${i}"]`);
    const box = el?.parentElement?.parentElement?.getBoundingClientRect(); // ol → 스크롤 컨테이너
    const container = el?.closest(".overflow-y-auto")?.getBoundingClientRect();
    const r = el?.getBoundingClientRect();
    return !!(r && container && box && r.top >= container.top - 0.5 && r.bottom <= container.bottom + 0.5);
  }, index);


// B-1c-3 전용 업무: 인용문만(시각 없음)·시각만·근사(인용문 못 찾음)·둘 다 없음
const DETAIL = {
  ...BASE_DETAIL,
  actionItems: [
    item({ id: 201, title: "인용문만", evidenceStartSec: null, evidenceQuote: "발언 7 번 내용" }),
    item({ id: 202, title: "근사 위치", evidenceStartSec: 125, evidenceQuote: "전사문에 없는 말입니다 전혀" }),
    item({ id: 203, title: "근거 없음", evidenceStartSec: null, evidenceQuote: null }),
  ],
};
const TRANSCRIPT = (route: Route) => json(route, { fullText: "x", segments: SEGMENTS, sttProvider: "fake", speakers: [] });

test.describe("근거 위치 보기(작업 B-1c-3)", () => {
  test("시각이 없어도 인용문이 있으면 버튼이 활성이고 인용문이 든 구간으로 이동한다", async ({ page }) => {
    await open(page, { detail: DETAIL, transcript: TRANSCRIPT });
    const button = page.getByRole("button", { name: "근거 위치 보기: 인용문만" });
    await expect(button).toBeEnabled();
    await button.click();
    await expect(row(page, 7)).toBeFocused();
    await expect(page.getByTestId("evidence-approx-notice")).toHaveCount(0);
  });

  test("인용문을 못 찾고 시각만 있으면 가까운 앞쪽 발화로 가고 '근사 위치'를 안내한다", async ({ page }) => {
    await open(page, { detail: DETAIL, transcript: TRANSCRIPT });
    await page.getByRole("button", { name: /근거 위치 보기: 근사 위치/ }).click();
    await expect(row(page, 11)).toBeFocused(); // 125 초 이전 가장 가까운 구간 = 120 초(index 11)
    await expect(page.getByTestId("evidence-approx-notice")).toContainText("근사 위치");
  });

  test("시각·인용문이 모두 없으면 비활성 버튼 대신 '근거 위치 없음(사유)'를 보인다", async ({ page }) => {
    await open(page, { detail: DETAIL, transcript: TRANSCRIPT });
    await expect(page.getByTestId("evidence-none")).toContainText("근거 위치 없음");
    await expect(page.getByRole("button", { name: "근거 위치 보기: 근거 없음" })).toHaveCount(0);
  });
});
