/*
 * v2 화면 권한 제어 E2E: 버튼 노출은 직급·상태를 직접 비교하지 않고 서버가 내려준 allowedActions 로만 정한다.
 * 실제 백엔드 없이 page.route 로 가로챈다. 상세 응답의 allowedActions 는 e2e/helpers/allowed-actions.ts 가 서버 표대로 계산해 붙인다.
 */
import { expect, test, type Page, type Route } from "@playwright/test";

import { onJson, withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const LEAD = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const EXEC = { id: 8, name: "최임원", rank: "executive", tenantId: 1 };
const STAFF = { id: 9, name: "이서연", rank: "staff", tenantId: 1 };

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const item = (id: number, title: string, status: string, assigneeId = 9) => ({
  id, title, assignee: { id: assigneeId, name: "이서연" }, dueDate: "2026-10-09", dueUndetermined: false, status,
  confirmKind: status === "pending" ? null : "manager", evidenceStartSec: null, evidenceQuote: null, needsCompletion: false, missingFields: [], origin: "ai",
});

type Detail = Record<string, unknown> & { actionItems: ReturnType<typeof item>[] };

const baseDetail = (): Detail => ({
  id: 41, title: "주간 생산 현안 회의", heldAt: "2026-10-01T01:00:00Z", summary: "", decisions: [],
  status: "confirmed", confirmKind: "manager", confirmedBy: null, confirmedAt: null,
  firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: null, registeredBy: { id: 7, name: "한팀장" },
  origin: "audio_minutes", participants: [{ id: 9, name: "이서연" }], guestParticipants: [], recentEvents: [], phase: "active",
  minutes: { purpose: "목적", discussion: "논의", decisions: "결정", risks: "내용없음", nextAgenda: "안건", engine: "gemini", updatedBy: null, updatedAt: null },
  actionItems: [item(101, "견적서 송부", "pending"), item(102, "설비 점검", "confirmed")],
});

type Account = typeof LEAD;

interface Opts {
  /** 허용 동작을 서버처럼 계산해 붙일지(false 면 allowedActions 없는 옛 응답) */
  allowed?: boolean;
  lead?: boolean;
  history?: unknown[];
  changeRequests?: unknown[];
}

async function openDetail(page: Page, account: Account, detail: Detail, opts: Opts = {}) {
  const { allowed = true, lead = true } = opts;
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, account) : json(route, 401, { detail: "인증 필요" }),
  );
  await page.route(/\/api\/meetings\/41$/, (route) => json(route, 200, allowed ? withAllowed(detail, account, { lead }) : detail));
  await page.route(/\/api\/meetings\/41\/transcript$/, (route) => json(route, 404, { detail: "전사문이 없습니다" }));
  await page.route(/\/api\/meetings\/41\/speakers$/, (route) => json(route, 200, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route(/\/api\/meetings\/41\/change-requests$/, (route) => json(route, 200, opts.changeRequests ?? []));
  await page.route(/\/api\/meetings\/41\/audio-url$/, (route) => json(route, 404, { detail: "음성 파일이 없습니다" }));
  await page.route(/\/api\/meetings\/41\/history$/, (route) => json(route, 200, opts.history ?? []));
  await page.route("**/api/accounts", (route) => json(route, 200, []));
  await page.route(/\/api\/action-items\/(\d+)\/confirm$/, (route) => {
    const id = Number(/action-items\/(\d+)\//.exec(route.request().url())![1]);
    const target = detail.actionItems.find((it) => it.id === id)!;
    Object.assign(target, { status: "confirmed", confirmKind: "manager" });
    return json(route, 200, target); // 응답의 업무에는 allowedActions 가 없다(서버도 상세 밖에서는 null)
  });
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto("/v2/meetings/41");
  await expect(page.getByRole("heading", { level: 1, name: detail.title as string })).toBeVisible();
}

const more = (page: Page) => page.getByRole("button", { name: "회의록 더보기" });
const menuItems = (page: Page) => page.getByRole("menu", { name: "회의록 동작" }).getByRole("menuitem");
const btn = (page: Page, name: string | RegExp) => page.getByRole("button", { name });
const row = (page: Page, title: string) => page.getByRole("table", { name: "업무 원장" }).locator("tbody tr").filter({ hasText: title });

async function menuTexts(page: Page): Promise<string[]> {
  if ((await more(page).count()) === 0) return [];
  await more(page).click();
  const texts = await menuItems(page).allInnerTexts();
  await page.keyboard.press("Escape");
  return texts;
}

test.describe("역할별 버튼 표시(서버 허용 동작)", () => {
  for (const [label, account, lead] of [["총괄 관리자", LEAD, true], ["지시자", EXEC, true]] as const) {
    test(`${label}: 쓰기 버튼 전부`, async ({ page }) => {
      await openDetail(page, account, baseDetail(), { lead });
      for (const name of ["항목 수정", "업무 추가", "화자 지정", "회의록 전체 수정 요청"]) await expect(btn(page, name)).toBeVisible();
      await expect(btn(page, "견적서 송부 업무 확정")).toBeVisible();
      await expect(btn(page, "견적서 송부 담당자 변경")).toBeVisible();
      await expect(btn(page, "견적서 송부 기한 변경")).toBeVisible();
      await expect(btn(page, "견적서 송부 업무 삭제")).toBeVisible();
      await expect(btn(page, "설비 점검 업무 종결")).toBeVisible();
      await expect(btn(page, "견적서 송부 업무 종결")).toHaveCount(0); // 확정 전 업무는 종결 없음
      expect(await menuTexts(page)).toEqual(["엑셀 다운로드", "변경 이력", "수정 회의록 업로드", "보류", "직권 종료", "삭제"]);
    });
  }

  test("총괄이 아닌 관리자: 읽기·수정 요청·해결만, 쓰기 버튼 없음", async ({ page }) => {
    await openDetail(page, LEAD, baseDetail(), { lead: false, changeRequests: [{ requestId: 5, requester: { id: 9, name: "이서연" }, comment: "고쳐 주세요", itemId: null, createdAt: "2026-10-02T01:00:00Z", resolution: null }] });
    for (const name of ["항목 수정", "업무 추가", "화자 지정"]) await expect(btn(page, name)).toHaveCount(0);
    await expect(btn(page, "회의록 전체 수정 요청")).toBeVisible();
    await expect(btn(page, "견적서 송부 수정 요청")).toBeVisible();
    for (const name of [/업무 확정/, /업무 종결/, /업무 삭제/, /담당자/, /기한 (설정|변경)/]) await expect(btn(page, name)).toHaveCount(0);
    expect(await menuTexts(page)).toEqual(["엑셀 다운로드", "변경 이력"]);
    // 수정 요청 해결은 manager 이상(서버 허용 동작 resolve_change_request)
    await btn(page, "회의록 전체 수정 요청").click();
    await expect(page.getByRole("dialog").getByRole("button", { name: "답변·해결" })).toBeVisible();
  });

  test("담당자: 수정 요청과 읽기만, 해결 버튼도 없음", async ({ page }) => {
    await openDetail(page, STAFF, baseDetail(), { changeRequests: [{ requestId: 5, requester: { id: 9, name: "이서연" }, comment: "고쳐 주세요", itemId: null, createdAt: "2026-10-02T01:00:00Z", resolution: null }] });
    for (const name of ["항목 수정", "업무 추가", "화자 지정", "회의록 확정"]) await expect(btn(page, name)).toHaveCount(0);
    for (const name of [/업무 확정/, /업무 종결/, /업무 삭제/, /담당자/, /기한 (설정|변경)/]) await expect(btn(page, name)).toHaveCount(0);
    await expect(btn(page, "설비 점검 수정 요청")).toBeVisible();
    expect(await menuTexts(page)).toEqual(["엑셀 다운로드", "변경 이력"]);
    await btn(page, "회의록 전체 수정 요청").click();
    await expect(page.getByRole("dialog")).toContainText("고쳐 주세요");
    await expect(page.getByRole("dialog").getByRole("button", { name: "답변·해결" })).toHaveCount(0);
    // 읽기 전용 표시는 그대로
    await page.keyboard.press("Escape");
    await expect(row(page, "견적서 송부")).toContainText("2026-10-09");
    await expect(row(page, "견적서 송부")).toContainText("이서연");
  });
});

test.describe("상태별 버튼 표시(서버 허용 동작)", () => {
  test("확정 대기: 회의록 확정 버튼, 확정 뒤에는 없음", async ({ page }) => {
    await openDetail(page, LEAD, { ...baseDetail(), status: "awaiting_confirmation", confirmKind: null });
    await expect(btn(page, "회의록 확정")).toBeVisible();
  });

  test("확정: 회의록 확정 버튼 없음", async ({ page }) => {
    await openDetail(page, LEAD, baseDetail());
    await expect(btn(page, "회의록 확정")).toHaveCount(0);
  });

  test("보류: 재개 버튼, 쓰기·업로드 없음, 업무 동작 없음(수정 요청만)", async ({ page }) => {
    await openDetail(page, LEAD, { ...baseDetail(), phase: "on_hold", onHold: true });
    await expect(btn(page, "재개")).toBeVisible();
    for (const name of ["항목 수정", "업무 추가", "화자 지정"]) await expect(btn(page, name)).toHaveCount(0);
    for (const name of [/업무 확정/, /업무 종결/, /업무 삭제/, /담당자/, /기한 (설정|변경)/]) await expect(btn(page, name)).toHaveCount(0);
    await expect(btn(page, "견적서 송부 수정 요청")).toBeVisible();
    expect(await menuTexts(page)).toEqual(["엑셀 다운로드", "변경 이력", "삭제"]);
  });

  test("종료: 쓰기·재개 없음, 삭제만 남음", async ({ page }) => {
    await openDetail(page, LEAD, { ...baseDetail(), phase: "ended" });
    await expect(btn(page, "재개")).toHaveCount(0);
    await expect(btn(page, "항목 수정")).toHaveCount(0);
    expect(await menuTexts(page)).toEqual(["엑셀 다운로드", "변경 이력", "삭제"]);
  });

  test("삭제: 다운로드·이력뿐", async ({ page }) => {
    await openDetail(page, LEAD, { ...baseDetail(), phase: "deleted" });
    await expect(btn(page, "항목 수정")).toHaveCount(0);
    expect(await menuTexts(page)).toEqual(["엑셀 다운로드", "변경 이력"]);
  });
});

test.describe("allowedActions 가 없는 응답(안전한 쪽)", () => {
  test("모든 동작 버튼이 보이지 않고 읽기 표시는 그대로", async ({ page }) => {
    await openDetail(page, LEAD, { ...baseDetail(), status: "awaiting_confirmation" }, { allowed: false });
    for (const name of ["항목 수정", "업무 추가", "화자 지정", "회의록 전체 수정 요청", "회의록 확정", "재개"]) await expect(btn(page, name)).toHaveCount(0);
    for (const name of [/업무 확정/, /업무 종결/, /업무 삭제/, /담당자/, /기한 (설정|변경)/, /수정 요청/]) await expect(btn(page, name)).toHaveCount(0);
    await expect(more(page)).toHaveCount(0);
    await expect(row(page, "견적서 송부")).toContainText("2026-10-09"); // 읽기 전용 표시
    await expect(row(page, "견적서 송부")).toContainText("이서연");
    await expect(page.getByRole("region", { name: "회의 개요" })).toContainText("목적");
  });

  test("업무의 allowedActions 가 null 이면 그 업무 행에 동작 버튼이 없다", async ({ page }) => {
    const detail = baseDetail();
    await openDetail(page, LEAD, detail);
    await page.route(/\/api\/meetings\/41$/, (route) =>
      json(route, 200, { ...withAllowed(detail, LEAD), actionItems: detail.actionItems.map((it) => ({ ...it, allowedActions: null })) }),
    );
    await page.reload();
    await expect(btn(page, "항목 수정")).toBeVisible(); // 회의록 단위는 그대로
    await expect(btn(page, /업무 확정|업무 종결|업무 삭제|담당자|(송부|점검) 수정 요청/)).toHaveCount(0);
  });
});

test.describe("업무 동작 직후 행의 버튼은 서버 기준", () => {
  test("확정 직후 확정 버튼이 사라지고 종결 버튼이 보인다(응답의 업무에는 allowedActions 가 없다)", async ({ page }) => {
    await openDetail(page, LEAD, baseDetail());
    await expect(btn(page, "견적서 송부 업무 확정")).toBeVisible();
    await expect(btn(page, "견적서 송부 업무 종결")).toHaveCount(0);
    await btn(page, "견적서 송부 업무 확정").click();
    await expect(btn(page, "견적서 송부 업무 종결")).toBeVisible(); // 상세를 다시 받아 서버 기준으로 바뀐다
    await expect(btn(page, "견적서 송부 업무 확정")).toHaveCount(0);
    await expect(btn(page, "견적서 송부 담당자 변경")).toBeVisible(); // 행의 다른 동작은 사라지지 않는다
    await expect(btn(page, "견적서 송부 업무 삭제")).toBeVisible();
    await expect(page.getByText("견적서 송부: 확정했습니다")).toBeVisible();
  });
});

test.describe("목록 탭은 availablePhases 를 따른다", () => {
  const rowOf = (id: number) => ({
    id, title: `회의 ${id}`, heldAt: "2026-10-01T01:00:00Z", registeredBy: { id: 7, name: "한팀장" }, origin: "audio_minutes",
    status: "confirmed", confirmKind: "manager", itemCount: 1, needsCompletionCount: 0, autoConfirmAt: null, phase: "active",
  });

  async function openList(page: Page, account: Account, body: Record<string, unknown>) {
    await page.route("**/api/auth/me", (route) =>
      route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, account) : json(route, 401, { detail: "인증 필요" }),
    );
    await page.route("**/api/meetings?*", (route) => json(route, 200, { items: [rowOf(1)], total: 1, page: 1, size: 20, ...body }));
    await page.goto("/v2/login");
    await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
    await page.goto("/v2/meetings");
  }
  const tabs = (page: Page) => page.getByRole("tablist", { name: "회의록 단계" }).getByRole("tab");

  test("관리자 이상 4개 탭", async ({ page }) => {
    await openList(page, LEAD, { availablePhases: ["active", "ended", "on_hold", "deleted"] });
    await expect(tabs(page)).toHaveText(["진행중", "종료", "보류", "삭제"]);
  });

  test("직급이 아니라 응답을 따른다: 담당자 계정이어도 응답이 4개면 4개, 관리자 계정이어도 응답이 2개면 2개", async ({ page }) => {
    await openList(page, STAFF, { availablePhases: ["active", "ended", "on_hold", "deleted"] });
    await expect(tabs(page)).toHaveText(["진행중", "종료", "보류", "삭제"]);
    await page.unrouteAll();
    await openList(page, LEAD, { availablePhases: ["active", "ended"] });
    await expect(tabs(page)).toHaveText(["진행중", "종료"]);
  });

  test("응답에 없으면 진행중·종료만(안전한 쪽)", async ({ page }) => {
    await openList(page, LEAD, {});
    await expect(page.getByRole("table", { name: "회의록 목록" })).toBeVisible();
    await expect(tabs(page)).toHaveText(["진행중", "종료"]);
  });
});

test.describe("변경 이력 표시 보강", () => {
  const entry = (id: number, extra: Record<string, unknown>) => ({
    id, targetType: "action_item", targetId: 101, kind: "업무 수정", kindCode: "item.updated", batchId: null,
    before: { title: "이전 이름" }, after: { title: "새 이름" }, beforeMissing: false, viewImpact: null,
    changedBy: { id: 7, name: "한팀장" }, changedAt: "2026-10-05T03:00:00Z", ...extra,
  });

  test("'업무 수정' 구분, '변경 전 기록 없음', 열람 영향(유지·열람 불가)을 글자로 표시", async ({ page }) => {
    await openDetail(page, LEAD, baseDetail(), {
      history: [
        entry(3, { kind: "직권 수정", kindCode: "participants.overridden", targetType: "meeting", targetId: 41,
          before: { participants: ["이서연(es)", "정빠짐(out)", "한유지(keep)"] }, after: { participants: ["이서연(es)"] },
          viewImpact: [
            { accountId: 5, name: "정빠짐", loginId: "out", viewImpact: "열람 불가(참석자에서 빠져 볼 수 없게 됨)" },
            { accountId: 6, name: "한유지", loginId: "keep", viewImpact: "열람 유지(담당 업무)" },
          ] }),
        entry(2, { beforeMissing: true, before: {}, after: { title: "옛 수정" } }),
        entry(1, {}),
      ],
    });
    await more(page).click();
    await menuItems(page).filter({ hasText: "변경 이력" }).click();
    const box = page.getByRole("dialog");
    await expect(box.getByText("업무 수정", { exact: true })).toHaveCount(2);
    await expect(box).toContainText("변경 전 이전 이름");
    await expect(box).toContainText("변경 후 새 이름");
    await expect(box).toContainText("변경 전 변경 전 기록 없음");
    await expect(box).toContainText("변경 후 옛 수정");
    const impact = box.getByLabel("열람 영향");
    await expect(impact).toContainText("정빠짐 · 열람 불가(참석자에서 빠져 볼 수 없게 됨)");
    await expect(impact).toContainText("한유지 · 열람 유지(담당 업무)");
  });
});
