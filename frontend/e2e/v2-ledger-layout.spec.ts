/*
 * 업무 원장 열 레이아웃 E2E(작업 67-1): 칸 겹침 없음, 칸 내용 넘침 없음, 페이지 가로 스크롤 없음, 업무명·근거 폭 균형, 버튼 줄바꿈, 표 컨테이너 키보드 스크롤.
 * 실제 백엔드 없이 page.route 로 가로챈다(데이터는 모두 가상).
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const ACCOUNT = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const LONG_TITLE = "협력사 부품 재입고 일정을 확인한 뒤 생산관리팀과 구매팀에 공유하고 납기 조정안까지 정리해서 보고하기";
const LONG_QUOTE =
  "김대리가 협력사에 재입고 날짜를 확인해서 월요일까지 공유해 주세요. 그리고 구매팀과 생산관리팀은 그 일정에 맞춰 3호기 납기 조정안을 같이 만들어 주시고, " +
  "고객사에는 수요일 오전에 먼저 전화로 사정을 설명한 다음 서면으로도 안내문을 보내 주시기 바랍니다. 이 부분은 꼭 기록으로 남겨 주세요.";
const LONG_NAME = "아주긴이름을가진담당자홍길동";

const base = (overrides: Record<string, unknown>) => ({
  id: 1, title: "", assignee: null, dueDate: null, dueUndetermined: false, status: "pending", confirmKind: null,
  evidenceStartSec: null, evidenceQuote: null, needsCompletion: false, missingFields: [], origin: "ai", ...overrides,
});

const DETAIL = {
  id: 41, title: "주간 생산 현안 회의", heldAt: "2026-10-01T01:00:00Z", summary: "요약", decisions: [], status: "awaiting_confirmation",
  confirmKind: null, confirmedBy: null, confirmedAt: null, firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: "2026-10-06T01:00:00Z",
  registeredBy: { id: 7, name: "한팀장" }, origin: "audio", participants: [{ id: 7, name: "한팀장" }], guestParticipants: [],
  actionItems: [
    base({ id: 101, title: LONG_TITLE, assignee: { id: 3, name: LONG_NAME }, dueDate: "2026-10-05", evidenceStartSec: 760, evidenceQuote: LONG_QUOTE }),
    base({ id: 102, title: "짧은 업무", assignee: { id: 7, name: "한팀장" }, dueUndetermined: true, status: "confirmed", needsCompletion: true, missingFields: ["dueDate"] }),
  ],
  recentEvents: [], phase: "active", processing: null,
};

async function open(page: Page) {
  await page.route("**/api/auth/me", (route) => (route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, ACCOUNT) : json(route, { detail: "x" }, 401)));
  await page.route("**/api/me/processing", (route) => json(route, []));
  await page.route("**/api/meetings?*", (route) => json(route, { items: [], total: 0, page: 1, size: 20 }));
  await page.route(/\/api\/meetings\/\d+$/, (route) => json(route, withAllowed(DETAIL as never, ACCOUNT)));
  await page.route(/\/api\/meetings\/\d+\/transcript$/, (route) => json(route, { detail: "없음" }, 404));
  await page.route(/\/api\/meetings\/\d+\/(speakers|change-requests)$/, (route) => json(route, []));
  await page.route("**/api/accounts", (route) => json(route, []));
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto("/v2/meetings/41");
  await expect(page.getByRole("table", { name: "업무 원장" })).toBeVisible();
}

interface Rect { l: number; r: number; t: number; b: number }
interface Measure {
  cells: { rect: Rect; overflow: boolean }[][];
  wrapper: { sw: number; cw: number; overflowX: string; tabindex: string | null; label: string | null };
  pageOverflow: boolean;
  aside: Rect | null;
  wrapperRect: Rect;
  buttons: { due: Rect[][]; action: Rect[][] };
  titleW: number;
  evidenceW: number;
}

/** 표 1회 측정(브라우저 안에서 계산) */
async function measure(page: Page): Promise<Measure> {
  return page.evaluate(() => {
    const rectOf = (el: Element): Rect => {
      const r = el.getBoundingClientRect();
      return { l: r.left, r: r.right, t: r.top, b: r.bottom };
    };
    const table = document.querySelector('table[aria-label="업무 원장"]') as HTMLTableElement;
    const wrapper = table.parentElement as HTMLElement;
    const rows = Array.from(table.querySelectorAll("tbody tr"));
    const cells = rows.map((row) =>
      Array.from(row.querySelectorAll("td")).map((td) => ({ rect: rectOf(td), overflow: td.scrollWidth > td.clientWidth + 1 })),
    );
    const buttonsIn = (index: number) => rows.map((row) => Array.from(row.querySelectorAll("td")[index].querySelectorAll("button")).map(rectOf));
    const head = Array.from(table.querySelectorAll("thead th"));
    const aside = document.querySelector("aside");
    return {
      cells,
      wrapper: { sw: wrapper.scrollWidth, cw: wrapper.clientWidth, overflowX: getComputedStyle(wrapper).overflowX, tabindex: wrapper.getAttribute("tabindex"), label: wrapper.getAttribute("aria-label") },
      pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      aside: aside && getComputedStyle(aside).display !== "none" ? rectOf(aside) : null,
      wrapperRect: rectOf(wrapper),
      buttons: { due: buttonsIn(3), action: buttonsIn(6) },
      titleW: head[1].getBoundingClientRect().width,
      evidenceW: head[5].getBoundingClientRect().width,
    };
  });
}

const overlap = (a: Rect, b: Rect) => a.l < b.r - 0.5 && b.l < a.r - 0.5 && a.t < b.b - 0.5 && b.t < a.b - 0.5;
const inside = (inner: Rect, outer: Rect) => inner.l >= outer.l - 0.5 && inner.r <= outer.r + 0.5 && inner.t >= outer.t - 0.5 && inner.b <= outer.b + 0.5;

for (const width of [1280, 1024, 900, 768, 600, 375]) {
  test.describe(`업무 원장 열 레이아웃 ${width}px`, () => {
    test.use({ viewport: { width, height: 900 } });

    test("칸이 겹치지 않고 내용이 칸 밖으로 넘치지 않으며 페이지 가로 스크롤이 없다", async ({ page }) => {
      await open(page);
      const m = await measure(page);
      expect(m.pageOverflow).toBe(false);
      for (const row of m.cells) {
        for (let i = 0; i + 1 < row.length; i += 1) expect(overlap(row[i].rect, row[i + 1].rect), `칸 ${i} 와 ${i + 1} 겹침`).toBe(false);
        // 칸 내용은 칸 밖으로 넘치지 않는다(표 컨테이너가 가로 스크롤되는 폭에서도 칸 자체는 내용을 담는다)
        row.forEach((cell, i) => expect(cell.overflow, `칸 ${i} 내용 넘침`).toBe(false));
      }
      // 표 컨테이너(스크롤 영역)는 오른쪽 패널(있으면)과 겹치지 않는다. 표 자체는 컨테이너 안에서만 스크롤된다
      if (m.aside) expect(m.wrapperRect.r).toBeLessThanOrEqual(m.aside.l + 0.5);
    });

    test("동작·완료 기한 칸의 버튼이 칸 안에 있고 서로 겹치지 않는다", async ({ page }) => {
      await open(page);
      const m = await measure(page);
      m.cells.forEach((row, r) => {
        for (const [index, key] of [[3, "due"], [6, "action"]] as const) {
          const group = m.buttons[key][r];
          expect(group.length).toBeGreaterThan(0);
          group.forEach((btn) => expect(inside(btn, row[index].rect), `${key} 버튼이 칸 밖`).toBe(true));
          for (let i = 0; i < group.length; i += 1) for (let j = i + 1; j < group.length; j += 1) expect(overlap(group[i], group[j])).toBe(false);
        }
      });
    });

    test("버튼의 DOM 순서와 Tab 순서가 기존과 같다", async ({ page }) => {
      await open(page);
      const names = await page.locator('table[aria-label="업무 원장"] tbody tr').first().evaluate((row) =>
        Array.from(row.querySelectorAll("button")).map((b) => b.getAttribute("aria-label") ?? b.textContent),
      );
      expect(names).toEqual([
        "협력사 부품 재입고 일정을 확인한 뒤 생산관리팀과 구매팀에 공유하고 납기 조정안까지 정리해서 보고하기 담당자 변경",
        "협력사 부품 재입고 일정을 확인한 뒤 생산관리팀과 구매팀에 공유하고 납기 조정안까지 정리해서 보고하기 기한 변경",
        "00:12:40부터 재생",
        "근거 위치 보기: 협력사 부품 재입고 일정을 확인한 뒤 생산관리팀과 구매팀에 공유하고 납기 조정안까지 정리해서 보고하기 00:12:40", // 작업 68-2 에서 추가된 버튼(시각 버튼 바로 뒤)
        "협력사 부품 재입고 일정을 확인한 뒤 생산관리팀과 구매팀에 공유하고 납기 조정안까지 정리해서 보고하기 업무 확정",
        "협력사 부품 재입고 일정을 확인한 뒤 생산관리팀과 구매팀에 공유하고 납기 조정안까지 정리해서 보고하기 수정 요청",
        "협력사 부품 재입고 일정을 확인한 뒤 생산관리팀과 구매팀에 공유하고 납기 조정안까지 정리해서 보고하기 업무 삭제",
      ]);
    });
  });
}

for (const width of [1280, 1024]) {
  test(`업무명 칸과 근거 칸 폭 비율이 0.8~1.25 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await open(page);
    const m = await measure(page);
    const ratio = m.titleW / m.evidenceW;
    expect(ratio).toBeGreaterThanOrEqual(0.8);
    expect(ratio).toBeLessThanOrEqual(1.25);
  });
}

for (const width of [1280, 768, 375]) {
  test(`동작 칸 버튼이 칸 안에서 다음 줄로 넘어간다 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await open(page);
    const m = await measure(page);
    const rowsOf = (group: Rect[]) => new Set(group.map((b) => Math.round(b.t))).size;
    // 동작 칸은 rem 폭이라 확정·수정 요청·삭제 세 버튼이 한 줄에 못 들어가 칸 안에서 다음 줄로 넘어간다(표가 스크롤되는 폭에서도 같다)
    expect(rowsOf(m.buttons.action[0])).toBeGreaterThanOrEqual(2);
  });
}

for (const width of [768, 600, 375]) {
  test(`표 컨테이너: 가로 스크롤 가능하면 키보드로 스크롤되고 접근성 이름이 있다 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await open(page);
    const wrapper = page.getByRole("group", { name: "업무 원장 표, 가로 스크롤 가능" });
    await expect(wrapper).toHaveCount(1);
    const scrollable = await wrapper.evaluate((el) => el.scrollWidth > el.clientWidth + 1);
    if (scrollable) {
      await wrapper.focus();
      await expect(wrapper).toBeFocused();
      await page.keyboard.press("ArrowRight");
      await expect.poll(() => wrapper.evaluate((el) => el.scrollLeft)).toBeGreaterThan(0);
    }
  });
}
