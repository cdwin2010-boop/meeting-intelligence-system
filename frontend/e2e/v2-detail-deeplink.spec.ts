/*
 * 회의록 상세 E2E(작업 68-2): 헤더 정보·5개 항목 가독성, 업무 근거 → 전사문 딥링크(대응 규칙, 하이라이트, 포커스, 예외), 두 테마, 근거 칸 레이아웃.
 * 실제 백엔드 없이 page.route 로 가로챈다(데이터는 모두 가상).
 */
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

const DETAIL = {
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
  await page.route("**/api/**", (route) => json(route, []));
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

// ================= 헤더 정보·5개 항목 =================
test.describe("상세 헤더 정보·5개 항목 가독성", () => {
  test("헤더 정보: 라벨과 값 구조, 라벨은 작고 보조 색, 값은 크고 굵고 본문 색, 5개 항목과 구분선", async ({ page }) => {
    await open(page);
    const overview = page.getByRole("region", { name: "회의 개요" });
    for (const [label, value] of [["회의 일시", "2026"], ["등록자", "김대리"], ["참석자", "김대리, 한팀장, 외부 참석자(미등록)"]]) {
      const dt = overview.locator("dl > div > dt", { hasText: label }).first();
      const dd = dt.locator("xpath=following-sibling::dd[1]");
      await expect(dd).toContainText(value);
      const s = await page.evaluate(([a, b]) => {
        const ta = getComputedStyle(a as Element), tb = getComputedStyle(b as Element);
        return { dtSize: parseFloat(ta.fontSize), ddSize: parseFloat(tb.fontSize), dtWeight: Number(ta.fontWeight), ddWeight: Number(tb.fontWeight), dtColor: ta.color, ddColor: tb.color };
      }, [await dt.elementHandle(), await dd.elementHandle()]);
      expect(s.ddSize).toBeGreaterThan(s.dtSize);
      expect(s.ddWeight).toBeGreaterThanOrEqual(600);
      expect(s.ddWeight).toBeGreaterThan(s.dtWeight);
      expect(s.ddColor).not.toBe(s.dtColor);
    }
    // 헤더 정보 블록과 5개 항목 사이 구분선
    const border = await page.getByRole("region", { name: "회의록 항목" }).evaluate((el) => getComputedStyle(el.parentElement!).borderTopWidth);
    expect(border).toBe("1px");
  });

  test("5개 항목: 제목이 본문보다 크고 굵고, 1px 경계 카드와 간격, 내용없음은 보조 색 일반체, 제목 계층 유지", async ({ page }) => {
    await open(page);
    const minutes = page.getByRole("region", { name: "회의록 항목" });
    await expect(minutes.getByRole("heading", { level: 2, name: "회의록 항목" })).toBeVisible();
    const labels = ["목적", "주요 논의사항", "결정사항", "리스크", "다음 안건"];
    const boxes = [];
    for (const label of labels) {
      const dt = minutes.locator("dt", { hasText: label }).first();
      await expect(dt).toBeVisible();
      const info = await dt.evaluate((el) => {
        const card = el.parentElement!;
        const dd = el.nextElementSibling!;
        const cs = getComputedStyle(el), ds = getComputedStyle(dd), cc = getComputedStyle(card);
        const r = card.getBoundingClientRect();
        return { tSize: parseFloat(cs.fontSize), dSize: parseFloat(ds.fontSize), tWeight: Number(cs.fontWeight), border: cc.borderTopWidth, line: parseFloat(ds.lineHeight) / parseFloat(ds.fontSize), r: { l: r.left, t: r.top, b: r.bottom } };
      });
      expect(info.tSize).toBeGreaterThan(info.dSize);
      expect(info.tWeight).toBeGreaterThanOrEqual(600);
      expect(info.border).toBe("1px");
      expect(info.line).toBeGreaterThanOrEqual(1.5);
      boxes.push(info.r);
    }
    // 같은 줄이 아닌 카드끼리 세로 간격 16px 이상
    const rows = [...new Set(boxes.map((b) => Math.round(b.t)))].sort((a, b) => a - b);
    for (let i = 1; i < rows.length; i += 1) {
      const above = boxes.filter((b) => Math.round(b.t) === rows[i - 1]).reduce((m, b) => Math.max(m, b.b), 0);
      expect(rows[i] - above).toBeGreaterThanOrEqual(16);
    }
    // 내용없음: 보조 색 일반체(기울임 아님), 제목은 그대로 보임
    const empty = minutes.locator("dt", { hasText: "리스크" }).locator("xpath=following-sibling::dd[1]//span");
    await expect(empty).toHaveText("내용없음");
    const style = await empty.evaluate((el) => ({ italic: getComputedStyle(el).fontStyle, color: getComputedStyle(el).color, body: getComputedStyle(el.closest("dd")!.parentElement!.querySelector("dt")!).color }));
    expect(style.italic).toBe("normal");
    expect(style.color).not.toBe(style.body);
    // 편집 동작은 그대로: 항목 수정 → 팝업 → 취소
    await minutes.getByRole("button", { name: "항목 수정" }).click();
    await expect(page.getByRole("dialog", { name: "회의록 항목 수정" })).toBeVisible();
    await page.getByRole("button", { name: "취소" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });
});

// ================= 딥링크 =================
test.describe("업무 근거 → 전사문 딥링크", () => {
  test.use({ viewport: { width: 1280, height: 900 } });

  test("접근성 이름이 업무마다 구분되고, 시각 버튼은 그대로 있다", async ({ page }) => {
    await open(page);
    const names = await page.locator('table[aria-label="업무 원장"] button[aria-label^="근거 위치 보기"]').evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")));
    expect(new Set(names).size).toBe(5);
    expect(names).toContain("근거 위치 보기: 업무 A 00:50:00");
    expect(names).toContain("근거 위치 보기: 업무 C");
    await expect(page.getByRole("button", { name: "00:50:00부터 재생" })).toBeVisible();
  });

  test("누르면 전사문이 열리고 정확히 일치하는 구간이 가시 영역 안에서 하이라이트·포커스된다", async ({ page }) => {
    await open(page);
    await expect(panel(page).getByRole("button", { expanded: false })).toBeVisible();
    await deepLink(page, "업무 A", "00:50:00").click();
    await expect(panel(page).getByRole("button", { expanded: true })).toBeVisible();
    await expect(row(page, 299)).toBeFocused();
    await expect.poll(() => rowInView(page, 299)).toBe(true);
    const target = row(page, 299);
    await expect(target).toContainText("근거 위치");
    const styles = await page.evaluate(() => {
      const t = document.querySelector('[data-segment-index="299"]')!, o = document.querySelector('[data-segment-index="298"]')!;
      const a = getComputedStyle(t), b = getComputedStyle(o);
      return { lineWidth: a.borderLeftWidth, otherLine: b.borderLeftWidth, lineColor: a.borderLeftColor, otherLineColor: b.borderLeftColor, bg: a.backgroundColor, otherBg: b.backgroundColor };
    });
    expect(styles.lineWidth).toBe("4px");
    expect(styles.lineColor).not.toBe(styles.otherLineColor);
    expect(styles.bg).not.toBe(styles.otherBg);
    expect(await page.locator("[data-segment-index]", { hasText: "근거 위치" }).count()).toBe(1);
  });

  test("정확히 일치하지 않는 근거 시각은 이전의 가장 가까운 구간, 다음 업무의 딥링크에서 하이라이트가 옮겨진다", async ({ page }) => {
    await open(page);
    await deepLink(page, "업무 A", "00:50:00").click();
    await expect(row(page, 299)).toContainText("근거 위치");
    await deepLink(page, "업무 B", "00:58:24").click(); // 3504.5 초 → 3500 초(구간 349)
    await expect(row(page, 349)).toBeFocused();
    await expect.poll(() => rowInView(page, 349)).toBe(true);
    await expect(row(page, 349)).toContainText("근거 위치");
    await expect(row(page, 299)).not.toContainText("근거 위치");
    // 마지막 구간
    await deepLink(page, "업무 E", "01:40:00").click();
    await expect(row(page, 599)).toBeFocused();
    await expect.poll(() => rowInView(page, 599)).toBe(true);
  });

  test("닫으면 하이라이트가 사라지고 포커스가 클릭한 버튼으로 돌아온다", async ({ page }) => {
    await open(page);
    const button = deepLink(page, "업무 A", "00:50:00");
    await button.click();
    await expect(row(page, 299)).toBeFocused();
    await panel(page).getByRole("button", { expanded: true }).click();
    await expect(button).toBeFocused();
    await expect(page.locator("[data-segment-index]")).toHaveCount(0);
    await panel(page).getByRole("button", { expanded: false }).click(); // 다시 펼쳐도 하이라이트 없음
    await expect(row(page, 0)).toBeVisible();
    await expect(page.locator("[data-segment-index]", { hasText: "근거 위치" })).toHaveCount(0);
  });

  test("키보드(Enter·Space)로 동작한다", async ({ page }) => {
    await open(page);
    const a = deepLink(page, "업무 A", "00:50:00");
    await a.focus();
    await page.keyboard.press("Enter");
    await expect(row(page, 299)).toBeFocused();
    const b = deepLink(page, "업무 B", "00:58:24");
    await b.focus();
    await page.keyboard.press("Space");
    await expect(row(page, 349)).toBeFocused();
  });

  test("근거 시각이 없으면 비활성이고 이유가 title 에 있다", async ({ page }) => {
    await open(page);
    const c = page.getByRole("button", { name: "근거 위치 보기: 업무 C" });
    await expect(c).toBeDisabled();
    await expect(c).toHaveAttribute("title", /근거 시각이 없어/);
  });

  test("대응 구간이 없으면 안내(role=status)와 함께 창은 열린다", async ({ page }) => {
    await open(page);
    await deepLink(page, "업무 D", "00:00:05").click(); // 5 초: 첫 구간(10 초)보다 앞
    await expect(panel(page).getByRole("button", { expanded: true })).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "근거 시각에 해당하는 전사문 위치를 찾지 못했습니다" })).toBeVisible();
    await expect(page.locator("[data-segment-index]", { hasText: "근거 위치" })).toHaveCount(0);
    // 다른 업무의 딥링크가 성공하면 안내가 사라진다
    await deepLink(page, "업무 A", "00:50:00").click();
    await expect(page.getByRole("status").filter({ hasText: "근거 시각에 해당하는 전사문 위치를 찾지 못했습니다" })).toHaveCount(0);
  });

  test("전사문 조회가 실패하면 기존 오류 표시를 그대로 쓴다", async ({ page }) => {
    await open(page, { transcript: (route) => json(route, { detail: "서버 오류" }, 500) });
    await deepLink(page, "업무 A", "00:50:00").click();
    await expect(page.getByRole("alert").filter({ hasText: "전사문을 불러오지 못했습니다" })).toBeVisible();
    await expect(page.getByRole("button", { name: "다시 시도" })).toBeVisible();
    await expect(page.getByText("근거 시각에 해당하는 전사문 위치를 찾지 못했습니다")).toHaveCount(0);
  });

  test("prefers-reduced-motion 이면 즉시 이동한다(스크롤 애니메이션 없음)", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await open(page);
    await deepLink(page, "업무 E", "01:40:00").click();
    await expect(row(page, 599)).toBeFocused();
    expect(await rowInView(page, 599)).toBe(true); // 폴링 없이 바로 가시 영역 안
  });
});

// ================= 두 테마 =================
for (const theme of ["light", "dark"] as const) {
  test(`하이라이트 줄이 ${theme} 테마에서 다른 배경·선을 갖고 글자 대비 4.5:1 이상, 콘솔 오류 없음`, async ({ page }) => {
    const problems: string[] = [];
    page.on("console", (m) => { if (m.type() === "error") problems.push(m.text()); });
    page.on("pageerror", (e) => problems.push(e.message));
    await page.addInitScript(([k, v]) => window.localStorage.setItem(k, v), [THEME_KEY, theme]);
    await open(page);
    await deepLink(page, "업무 A", "00:50:00").click();
    await expect(row(page, 299)).toBeFocused();
    const result = await page.evaluate(() => {
      const parse = (c: string) => (c.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number);
      const lum = ([r, g, b]: number[]) => [r, g, b].map((v) => v / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)).reduce((acc, c, i) => acc + c * [0.2126, 0.7152, 0.0722][i], 0);
      const ratio = (a: number[], b: number[]) => { const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };
      const t = document.querySelector('[data-segment-index="299"]')!, o = document.querySelector('[data-segment-index="298"]')!;
      const bg = parse(getComputedStyle(t).backgroundColor);
      const body = parse(getComputedStyle(t.querySelector("p.leading-6")!).color);
      const muted = parse(getComputedStyle(t.querySelector("p.text-xs")!).color);
      const line = parse(getComputedStyle(t).borderLeftColor);
      return { sameBg: getComputedStyle(t).backgroundColor === getComputedStyle(o).backgroundColor, body: ratio(body, bg), muted: ratio(muted, bg), line: ratio(line, bg) };
    });
    expect(result.sameBg).toBe(false);
    expect(result.body).toBeGreaterThanOrEqual(4.5);
    expect(result.muted).toBeGreaterThanOrEqual(4.5);
    expect(result.line).toBeGreaterThanOrEqual(3);
    expect(problems).toEqual([]);
  });
}

// ================= 근거 칸 레이아웃(67-1 규칙 유지) =================
const LAYOUT_DETAIL = {
  ...DETAIL,
  actionItems: [item({ id: 101, title: LONG_TITLE, assignee: { id: 3, name: "아주긴이름을가진담당자홍길동" }, evidenceStartSec: 760, evidenceQuote: LONG_QUOTE })],
};
for (const width of [1280, 1024, 768, 375]) {
  test(`근거 칸의 새 버튼이 칸 안에서 줄바꿈되고 겹치지 않는다 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await open(page, { detail: LAYOUT_DETAIL });
    const m = await page.evaluate(() => {
      const cell = document.querySelector('table[aria-label="업무 원장"] tbody tr td:nth-child(6)') as HTMLElement;
      const rect = (e: Element) => { const r = e.getBoundingClientRect(); return { l: r.left, r: r.right, t: r.top, b: r.bottom }; };
      return { cell: rect(cell), overflow: cell.scrollWidth > cell.clientWidth + 1, buttons: Array.from(cell.querySelectorAll("button")).map(rect), page: document.documentElement.scrollWidth > document.documentElement.clientWidth };
    });
    expect(m.buttons).toHaveLength(2);
    expect(m.overflow).toBe(false);
    expect(m.page).toBe(false);
    for (const b of m.buttons) {
      expect(b.l).toBeGreaterThanOrEqual(m.cell.l - 0.5);
      expect(b.r).toBeLessThanOrEqual(m.cell.r + 0.5);
      expect(b.t).toBeGreaterThanOrEqual(m.cell.t - 0.5);
      expect(b.b).toBeLessThanOrEqual(m.cell.b + 0.5);
    }
    const [a, b] = m.buttons;
    expect(a.r <= b.l + 0.5 || b.r <= a.l + 0.5 || a.b <= b.t + 0.5 || b.b <= a.t + 0.5).toBe(true); // 겹치지 않음
  });
}
