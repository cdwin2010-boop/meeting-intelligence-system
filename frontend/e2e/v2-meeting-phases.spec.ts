/*
 * v2 회의록 단계(진행중·종료·보류·삭제) 탭과 상태 표시 E2E. 실제 백엔드 없이 page.route 로 v2 API 를 가로채 가짜로 응답한다.
 * (/api/auth/me, /api/meetings?phase=…, /api/meetings/{id} 와 상세 하위 자원) 데이터는 모두 가상이다.
 */
import { expect, test, type Page, type Route } from "@playwright/test";
import { withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const MANAGER = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const STAFF = { id: 9, name: "이서연", rank: "staff", tenantId: 1 };

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const row = (id: number, title: string, phase: string) => ({
  id, title, heldAt: "2026-10-01T01:00:00Z", registeredBy: { id: 3, name: "김대리" }, origin: "audio_minutes",
  status: "confirmed", confirmKind: "manager", itemCount: 2, needsCompletionCount: 0, autoConfirmAt: null, phase,
});

const ALL_PHASES = ["active", "ended", "on_hold", "deleted"];
// 서버는 목록 응답 최상위에 조회 가능한 단계(availablePhases)를 준다(관리자 4개, 담당자 진행중·종료)
const pageOf = (items: unknown[], total = items.length, page = 1, availablePhases: string[] = ALL_PHASES) => ({ items, total, page, size: 20, availablePhases });

async function login(page: Page, account: typeof MANAGER) {
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}`
      ? json(route, 200, account)
      : json(route, 401, { detail: "인증이 필요합니다" }),
  );
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
}

/** 목록 요청 주소(쿼리)를 모두 기록하고, handler 가 단계별 응답을 정한다 */
async function openList(page: Page, account: typeof MANAGER, handler: (params: URLSearchParams, route: Route) => unknown) {
  const requests: URLSearchParams[] = [];
  await login(page, account);
  await page.route("**/api/meetings?*", (route) => {
    const params = new URL(route.request().url()).searchParams;
    requests.push(params);
    return handler(params, route) as Promise<void>;
  });
  await page.goto("/v2/meetings");
  return requests;
}

const tabs = (page: Page) => page.getByRole("tablist", { name: "회의록 단계" }).getByRole("tab");
const rows = (page: Page) => page.getByRole("table", { name: "회의록 목록" }).locator("tbody tr");

test.describe("v2 회의록 단계 탭", () => {
  test("관리자: 4개 탭, 기본 진행중, 탭 전환 시 그 단계로 조회·단계 라벨 표시", async ({ page }) => {
    const byPhase: Record<string, unknown> = {
      active: pageOf([row(41, "주간 생산 현안 회의", "active")]),
      ended: pageOf([row(42, "3호기 납기 조정 회의", "ended")]),
      on_hold: pageOf([row(43, "신규 라인 증설", "on_hold")]),
      deleted: pageOf([row(44, "잘못 올린 회의", "deleted")]),
    };
    const requests = await openList(page, MANAGER, (params, route) => json(route, 200, byPhase[params.get("phase") ?? ""]));
    await expect(tabs(page)).toHaveText(["진행중", "종료", "보류", "삭제"]);
    await expect(tabs(page).filter({ hasText: "진행중" })).toHaveAttribute("aria-selected", "true");
    await expect(rows(page).first()).toContainText("주간 생산 현안 회의");
    await expect(rows(page).first()).toContainText("진행중");
    expect(requests.at(-1)?.get("phase")).toBe("active");

    const expected: [string, string, string][] = [
      ["종료", "ended", "3호기 납기 조정 회의"],
      ["보류", "on_hold", "신규 라인 증설"],
      ["삭제", "deleted", "잘못 올린 회의"],
    ];
    for (const [label, phase, title] of expected) {
      await tabs(page).filter({ hasText: label }).click();
      await expect(tabs(page).filter({ hasText: label })).toHaveAttribute("aria-selected", "true");
      await expect(rows(page).first()).toContainText(title);
      await expect(rows(page).first().locator("td").nth(4)).toHaveText(label);
      expect(requests.at(-1)?.get("phase")).toBe(phase);
      expect(requests.at(-1)?.get("page")).toBe("1");
    }
  });

  test("탭을 바꾸면 1쪽으로 돌아간다", async ({ page }) => {
    const many = Array.from({ length: 20 }, (_, i) => row(100 + i, `회의 ${i + 1}`, "active"));
    const requests = await openList(page, MANAGER, (params, route) =>
      json(route, 200, params.get("phase") === "active" ? pageOf(many, 25, Number(params.get("page"))) : pageOf([])),
    );
    await expect(rows(page)).toHaveCount(20);
    await page.getByRole("button", { name: "다음" }).click();
    await expect.poll(() => requests.at(-1)?.get("page")).toBe("2");
    await tabs(page).filter({ hasText: "종료" }).click();
    await expect.poll(() => requests.at(-1)?.get("phase")).toBe("ended");
    expect(requests.at(-1)?.get("page")).toBe("1");
  });

  test("담당자: 진행중·종료 탭만", async ({ page }) => {
    const requests = await openList(page, STAFF, (params, route) => json(route, 200, pageOf([row(41, "주간 회의", params.get("phase") ?? "")], 1, 1, ["active", "ended"])));
    await expect(tabs(page)).toHaveText(["진행중", "종료"]);
    await tabs(page).filter({ hasText: "종료" }).click();
    await expect.poll(() => requests.at(-1)?.get("phase")).toBe("ended");
    expect(requests.every((p) => p.get("phase") === "active" || p.get("phase") === "ended")).toBe(true);
  });

  test("탭별 빈 목록 안내", async ({ page }) => {
    await openList(page, MANAGER, (_params, route) => json(route, 200, pageOf([])));
    const empty: [string, string][] = [
      ["진행중", "등록된 회의록이 없습니다."],
      ["종료", "종료된 회의록이 없습니다."],
      ["보류", "보류된 회의록이 없습니다."],
      ["삭제", "삭제된 회의록이 없습니다."],
    ];
    for (const [label, text] of empty) {
      await tabs(page).filter({ hasText: label }).click();
      await expect(page.getByRole("status")).toHaveText(text);
    }
  });

  test("서버가 거부(403)하면 서버 문구와 다시 시도", async ({ page }) => {
    await openList(page, MANAGER, (params, route) =>
      params.get("phase") === "deleted"
        ? json(route, 403, { detail: "보류·삭제된 회의록은 관리자 이상만 볼 수 있습니다" })
        : json(route, 200, pageOf([])),
    );
    await tabs(page).filter({ hasText: "삭제" }).click();
    const alert = page.getByRole("alert").filter({ hasText: "회의록을 불러오지 못했습니다" });
    await expect(alert).toContainText("보류·삭제된 회의록은 관리자 이상만 볼 수 있습니다");
    await expect(alert.getByRole("button", { name: "다시 시도" })).toBeVisible();
  });
});

// ---------------- 상세 ----------------
const DETAIL_BASE = {
  id: 41, title: "주간 생산 현안 회의", heldAt: "2026-10-01T01:00:00Z", summary: "", decisions: [],
  status: "awaiting_confirmation", confirmKind: null, confirmedBy: null, confirmedAt: null,
  firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: "2026-10-06T02:00:00Z",
  registeredBy: { id: 7, name: "한팀장" }, origin: "audio_minutes", participants: [], actionItems: [], recentEvents: [],
};

async function openDetail(page: Page, detail: Record<string, unknown>, extra?: (page: Page) => Promise<void>) {
  await login(page, MANAGER);
  await page.route(/\/api\/meetings\/41$/, (route) => json(route, 200, withAllowed({ ...DETAIL_BASE, ...detail }, MANAGER)));
  await page.route(/\/api\/meetings\/41\/transcript$/, (route) => json(route, 404, { detail: "전사문이 없습니다" }));
  await page.route(/\/api\/meetings\/41\/speakers$/, (route) => json(route, 200, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route(/\/api\/meetings\/41\/change-requests$/, (route) => json(route, 200, []));
  await page.route("**/api/accounts", (route) => json(route, 200, []));
  if (extra) await extra(page);
  await page.goto("/v2/meetings/41");
  await expect(page.getByRole("heading", { level: 1, name: "주간 생산 현안 회의" })).toBeVisible();
}

const phaseLabel = (page: Page) => page.getByLabel("회의록 단계");
const records = (page: Page) => page.getByRole("region", { name: "처리 기록" });

test.describe("v2 회의록 상세 단계 표시", () => {
  test("진행중·기록 없음(필드 없는 응답 포함) → 진행중 라벨, 처리 기록 없음", async ({ page }) => {
    await openDetail(page, {});
    await expect(phaseLabel(page)).toHaveText("진행중");
    await expect(records(page)).toHaveCount(0);
  });

  test("관리자 직권 종료 → 종료 구분·처리자·시각·사유", async ({ page }) => {
    await openDetail(page, {
      status: "confirmed", phase: "ended", endKind: "manager", endedBy: { id: 2, name: "최임원" },
      endedAt: "2026-10-02T06:00:00Z", endReason: "프로젝트 취소",
    });
    await expect(phaseLabel(page)).toHaveText("종료 · 관리자 직권 종료");
    const item = records(page).getByRole("listitem");
    await expect(item).toHaveCount(1);
    await expect(item).toContainText("종료 · 관리자 직권 종료");
    await expect(item).toContainText("최임원");
    await expect(item).toContainText("2026-10-02 15:00");
    await expect(item).toContainText("사유 프로젝트 취소");
  });

  test("자동 종료 → 처리자 '자동', 사유 없음", async ({ page }) => {
    await openDetail(page, { phase: "ended", endKind: "auto", endedBy: null, endedAt: "2026-10-02T06:00:00Z", endReason: null });
    await expect(phaseLabel(page)).toHaveText("종료 · 자동 종료");
    const item = records(page).getByRole("listitem");
    await expect(item).toContainText("종료 · 자동 종료");
    await expect(item).toContainText("자동");
    await expect(item).not.toContainText("사유");
  });

  test("보류 → 보류 라벨과 보류 사유, 재개 기록이 있으면 함께", async ({ page }) => {
    await openDetail(page, {
      phase: "on_hold", onHold: true, onHoldBy: { id: 7, name: "한팀장" }, onHoldAt: "2026-10-02T01:00:00Z",
      onHoldReason: "예산 재검토", resumedBy: { id: 2, name: "최임원" }, resumedAt: "2026-09-30T01:00:00Z",
    });
    await expect(phaseLabel(page)).toHaveText("보류");
    const items = records(page).getByRole("listitem");
    await expect(items).toHaveCount(2);
    await expect(items.nth(0)).toContainText("보류");
    await expect(items.nth(0)).toContainText("한팀장");
    await expect(items.nth(0)).toContainText("사유 예산 재검토");
    await expect(items.nth(1)).toContainText("재개");
    await expect(items.nth(1)).toContainText("최임원");
  });

  test("삭제 → 삭제 라벨·사유, 쓰기 거부(409)는 서버 문구 그대로", async ({ page }) => {
    await openDetail(
      page,
      { phase: "deleted", deletedBy: { id: 7, name: "한팀장" }, deletedAt: "2026-10-02T03:00:00Z", deleteReason: "잘못 올린 파일" },
      async (p) => {
        await p.route(/\/api\/meetings\/41\/confirm$/, (route) => json(route, 409, { detail: "삭제된 회의록입니다. 수정할 수 없습니다" }));
      },
    );
    await expect(phaseLabel(page)).toHaveText("삭제");
    await expect(records(page)).toContainText("사유 잘못 올린 파일");
    // 허용 동작(allowedActions)에 확정이 없는 삭제된 회의록은 확정 버튼이 보이지 않는다
    await expect(page.getByRole("button", { name: "회의록 확정" })).toHaveCount(0);
    // 서버가 (표시 목록과 달리) 허용 동작에 확정을 주었는데 거부하는 경우: 버튼은 서버 목록대로, 거부되면 서버 문구를 보여 준다
    await page.route(/\/api\/meetings\/41$/, (route) =>
      json(route, 200, { ...DETAIL_BASE, phase: "deleted", deletedAt: "2026-10-02T03:00:00Z", allowedActions: ["confirm_meeting"], actionItems: [] }),
    );
    await page.reload();
    await page.getByRole("button", { name: "회의록 확정" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "회의록 확정" }).click();
    await expect(dialog.getByRole("alert")).toContainText("삭제된 회의록입니다. 수정할 수 없습니다");
  });
});
