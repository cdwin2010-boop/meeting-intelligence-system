/*
 * v2 유사 업무 검색·대체·대체 요청 E2E(작업 71-1 파트 B).
 * 실제 백엔드 없이 page.route 로 응답한다(데이터는 모두 가상). 버튼 노출은 서버 allowedActions(가짜 서버 계산기)로만 정해진다.
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withAllowed, type AllowOptions } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const MANAGER = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const json = (route: Route, status: number, body: unknown) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const item = (id: number, title: string, status: string, extra: Record<string, unknown> = {}) => ({
  id, title, assignee: { id: 9, name: "이서연" }, dueDate: "2026-10-09", dueUndetermined: false, status,
  confirmKind: status === "pending" ? null : "manager", evidenceStartSec: null, evidenceQuote: null, needsCompletion: false, missingFields: [], ...extra,
});

const similar = (itemId: number, title: string, score: number, reasons: string[], meetingId: number, over: Record<string, unknown> = {}) => ({
  itemId, title, assignee: { id: 9, name: "이서연" }, dueDate: "2026-10-05", status: "confirmed", score, reasons,
  meeting: { id: meetingId, title: `지난 회의 ${meetingId}`, heldAt: "2026-09-20T01:00:00Z" }, ...over,
});

const baseDetail = (items: unknown[] = [item(101, "견적서 송부", "pending"), item(102, "설비 점검", "confirmed")]) => ({
  id: 41, title: "주간 생산 현안 회의", heldAt: "2026-10-01T01:00:00Z", summary: "", decisions: [],
  status: "confirmed", confirmKind: "manager", confirmedBy: null, confirmedAt: null,
  firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: null, registeredBy: { id: 7, name: "한팀장" },
  origin: "audio_minutes", participants: [], recentEvents: [], phase: "active", actionItems: items,
});

interface Setup {
  detail?: ReturnType<typeof baseDetail>;
  options?: AllowOptions;
  similarResponder?: (itemId: number, attempt: number) => { status: number; body: unknown } | Promise<{ status: number; body: unknown }>;
  supersedeResponder?: (attempt: number) => { status: number; body: unknown };
  changeRequests?: unknown[];
  resolveResponder?: () => { status: number; body: unknown };
  supersedeDelayMs?: number;
}
interface Calls {
  similar: number[];
  supersede: unknown[];
  createRequest: unknown[];
  resolve: number;
  detailLoads: number;
}

async function open(page: Page, setup: Setup = {}, path = "/v2/meetings/41"): Promise<Calls> {
  const calls: Calls = { similar: [], supersede: [], createRequest: [], resolve: 0, detailLoads: 0 };
  const data = setup.detail ?? baseDetail();
  const requests: unknown[] = setup.changeRequests ? [...setup.changeRequests] : [];
  let similarAttempts = 0;
  let supersedeAttempts = 0;
  await page.route("**/api/**", (route) => json(route, 200, []));
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, MANAGER) : json(route, 401, { detail: "x" }),
  );
  await page.route(/\/api\/meetings\/41$/, (route) => {
    calls.detailLoads += 1;
    return json(route, 200, withAllowed(data as never, MANAGER, setup.options ?? {}));
  });
  await page.route(/\/api\/meetings\/41\/transcript$/, (route) => json(route, 404, { detail: "전사문이 없습니다" }));
  await page.route(/\/api\/meetings\/41\/speakers$/, (route) => json(route, 200, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route("**/api/accounts", (route) => json(route, 200, []));
  await page.route(/\/api\/meetings\/41\/change-requests$/, (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      calls.createRequest.push(body);
      requests.push({
        requestId: 900 + requests.length, requester: { id: 7, name: "한팀장" }, comment: body.comment, itemId: body.itemId,
        kind: body.kind ?? "edit", supersedesItemId: body.supersedesItemId ?? null, createdAt: "2026-10-02T01:00:00Z", resolution: null,
      });
      return json(route, 201, { requestId: 900 + requests.length });
    }
    return json(route, 200, requests);
  });
  await page.route(/\/api\/meetings\/41\/change-requests\/\d+\/resolve$/, (route) => {
    calls.resolve += 1;
    const { status, body } = setup.resolveResponder?.() ?? { status: 200, body: {} };
    return json(route, status, body);
  });
  await page.route(/\/api\/action-items\/\d+\/similar$/, async (route) => {
    const id = Number(/action-items\/(\d+)\/similar/.exec(route.request().url())![1]);
    calls.similar.push(id);
    similarAttempts += 1;
    const { status, body } = await (setup.similarResponder?.(id, similarAttempts) ?? { status: 200, body: [] });
    return json(route, status, body);
  });
  await page.route(/\/api\/action-items\/\d+\/supersede$/, async (route) => {
    calls.supersede.push(route.request().postDataJSON());
    supersedeAttempts += 1;
    if (setup.supersedeDelayMs) await new Promise((resolve) => setTimeout(resolve, setup.supersedeDelayMs));
    const { status, body } = setup.supersedeResponder?.(supersedeAttempts) ?? { status: 200, body: { oldItemId: 301, newItemId: 102, projectId: 5, changed: true } };
    if (status < 300) {
      const target = data.actionItems.find((it) => (it as { id: number }).id === 102) as Record<string, unknown>;
      target.supersedes = [{ itemId: 301, meetingId: 30, meetingTitle: "지난 회의 30" }];
    }
    return json(route, status, body);
  });
  await page.goto("/v2/login");
  await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1, name: "주간 생산 현안 회의" })).toBeVisible();
  return calls;
}

const row = (page: Page, title: string) => page.getByRole("table", { name: "업무 원장" }).locator("tbody tr").filter({ hasText: title });
const searchButton = (page: Page, title: string) => page.getByRole("button", { name: `유사 업무 검색: ${title}` });
const searchDialog = (page: Page) => page.getByRole("dialog", { name: "유사 업무 검색" });
const reasonDialog = (page: Page) => page.getByRole("alertdialog");
const candidates = (page: Page) => searchDialog(page).getByRole("list", { name: "유사 업무 후보" });

const FOUND = (_id: number) => ({
  status: 200,
  body: [
    similar(302, "설비 점검 일정 협의", 55, ["업무명 유사"], 31),
    similar(301, "설비 점검", 92, ["업무명 유사", "담당자 동일", "기한 근접"], 30),
    similar(303, "점검표 작성", 60, ["담당자 동일"], 32),
  ],
});

test.describe("허용 동작에 따른 버튼 노출", () => {
  test("동작이 없으면(대체 옵션 없음) 유사 업무 검색 버튼이 없다", async ({ page }) => {
    await open(page);
    await expect(page.getByRole("button", { name: /유사 업무 검색/ })).toHaveCount(0);
  });

  test("참여자(request): 검색과 대체 요청만, 직권 대체는 없다. 접근성 이름은 업무마다 다르다", async ({ page }) => {
    await open(page, { options: { supersede: "request" }, similarResponder: FOUND });
    await expect(searchButton(page, "설비 점검")).toBeVisible();
    await expect(searchButton(page, "견적서 송부")).toBeVisible();
    await searchButton(page, "설비 점검").click();
    await expect(candidates(page).getByRole("button", { name: /^대체 요청:/ })).toHaveCount(3);
    await expect(candidates(page).getByRole("button", { name: /직권 대체/ })).toHaveCount(0);
  });

  test("권한자(direct): 직권 대체까지 보인다", async ({ page }) => {
    await open(page, { options: { supersede: "direct" }, similarResponder: FOUND });
    await searchButton(page, "설비 점검").click();
    await expect(candidates(page).getByRole("button", { name: /^대체 요청:/ })).toHaveCount(3);
    await expect(candidates(page).getByRole("button", { name: /^직권 대체:/ })).toHaveCount(3);
  });

  test("대체된 업무에는 쓰기 동작이 없다(수정 요청만)", async ({ page }) => {
    const detail = baseDetail([item(101, "견적서 송부", "confirmed", { supersededBy: { itemId: 500, meetingId: 33, meetingTitle: "오늘 회의" } })]);
    await open(page, { detail, options: { supersede: "direct" } });
    const r = row(page, "견적서 송부");
    await expect(r.getByRole("button", { name: /유사 업무 검색|업무 종결|업무 삭제|업무 확정|담당자/ })).toHaveCount(0);
    await expect(r.getByRole("button", { name: "견적서 송부 수정 요청" })).toBeVisible();
  });
});

test.describe("유사 업무 검색 팝업", () => {
  test("로딩 → 점수 높은 순 카드, 점수·막대·이유·회의록 링크", async ({ page }) => {
    const calls = await open(page, {
      options: { supersede: "request" },
      similarResponder: async () => {
        await new Promise((resolve) => setTimeout(resolve, 600));
        return FOUND(0);
      },
    });
    await searchButton(page, "설비 점검").click();
    await expect(searchDialog(page).getByText("유사 업무를 찾는 중…")).toBeVisible();
    await expect(candidates(page)).toBeVisible();
    expect(calls.similar).toEqual([102]);
    const cards = candidates(page).locator("> li");
    await expect(cards).toHaveCount(3);
    await expect(cards.nth(0)).toContainText("설비 점검");
    await expect(cards.nth(0)).toContainText("유사도 92점");
    await expect(cards.nth(1)).toContainText("점검표 작성");
    await expect(cards.nth(1)).toContainText("유사도 60점");
    await expect(cards.nth(2)).toContainText("유사도 55점");
    const first = cards.nth(0);
    await expect(first.getByRole("list", { name: "유사 이유" }).locator("li")).toHaveText(["업무명 유사", "담당자 동일", "기한 근접"]);
    await expect(first).toContainText("이서연");
    await expect(first).toContainText("2026-10-05");
    await expect(first).toContainText("확정됨");
    const width = await first.locator("span[aria-hidden='true'] > span").evaluate((el) => (el as HTMLElement).style.width);
    expect(width).toBe("92%");
    const link = first.getByRole("link", { name: "지난 회의 30", exact: true });
    await expect(link).toHaveAttribute("href", "/v2/meetings/30");
    await expect(link).not.toHaveAttribute("target", "_blank");
    await expect(first.getByRole("link", { name: "회의록 열기: 지난 회의 30" })).toHaveAttribute("href", "/v2/meetings/30");
    await expect(first.getByRole("link", { name: "회의록 열기: 지난 회의 30" })).toContainText("회의록에서 직접 처리하려면 회의록 열기");
  });

  test("빈 결과 안내", async ({ page }) => {
    await open(page, { options: { supersede: "request" }, similarResponder: () => ({ status: 200, body: [] }) });
    await searchButton(page, "설비 점검").click();
    await expect(searchDialog(page)).toContainText("비슷한 과거 업무가 없습니다");
  });

  test("오류 → 알림과 다시 시도", async ({ page }) => {
    const calls = await open(page, {
      options: { supersede: "request" },
      similarResponder: (_id, attempt) => (attempt === 1 ? { status: 500, body: { detail: "검색 서버 오류" } } : FOUND(0)),
    });
    await searchButton(page, "설비 점검").click();
    const alert = searchDialog(page).getByRole("alert");
    await expect(alert).toContainText("검색 서버 오류");
    await alert.getByRole("button", { name: "다시 시도" }).click();
    await expect(candidates(page)).toBeVisible();
    expect(calls.similar).toEqual([102, 102]);
  });

  test("Esc 로 닫으면 연 버튼으로 포커스가 돌아온다", async ({ page }) => {
    await open(page, { options: { supersede: "request" }, similarResponder: FOUND });
    await searchButton(page, "설비 점검").click();
    await expect(candidates(page)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(searchDialog(page)).toBeHidden();
    await expect(searchButton(page, "설비 점검")).toBeFocused();
  });
});

test.describe("대체 요청", () => {
  test("사유(코멘트) 필수, 제출 본문에 kind·supersedesItemId, 성공하면 닫히고 결과를 알린다", async ({ page }) => {
    const calls = await open(page, { options: { supersede: "request" }, similarResponder: FOUND });
    await searchButton(page, "설비 점검").click();
    await candidates(page).getByRole("button", { name: "대체 요청: 설비 점검", exact: true }).click();
    const box = reasonDialog(page);
    await expect(box).toContainText("대체 요청");
    const confirm = box.getByRole("button", { name: "대체 요청 올리기" });
    await expect(confirm).toBeDisabled(); // 사유 필수
    await box.getByLabel("사유").fill("   ");
    await expect(confirm).toBeDisabled();
    await box.getByLabel("사유").fill("오늘 회의에서 같은 일로 정리됨");
    await confirm.click();
    await expect(searchDialog(page)).toBeHidden();
    await expect(reasonDialog(page)).toBeHidden();
    expect(calls.createRequest).toEqual([{ comment: "오늘 회의에서 같은 일로 정리됨", itemId: 102, kind: "supersede", supersedesItemId: 301 }]);
    await expect(page.getByText("설비 점검: 대체 요청을 올렸습니다")).toBeVisible();
  });

  test("서버 거부(403) 메시지는 팝업 안 알림으로, 입력은 유지된다", async ({ page }) => {
    // 첫 요청만 거부
    let first = true;
    await open(page, { options: { supersede: "request" }, similarResponder: FOUND });
    await page.route(/\/api\/meetings\/41\/change-requests$/, (route) => {
      if (route.request().method() === "POST" && first) {
        first = false;
        return json(route, 403, { detail: "이 프로젝트의 참여자만 대체 요청을 올릴 수 있습니다" });
      }
      return route.fallback();
    });
    await searchButton(page, "설비 점검").click();
    await candidates(page).getByRole("button", { name: "대체 요청: 설비 점검", exact: true }).click();
    const box = reasonDialog(page);
    await box.getByLabel("사유").fill("중복 업무입니다");
    await box.getByRole("button", { name: "대체 요청 올리기" }).click();
    await expect(box.getByRole("alert")).toContainText("이 프로젝트의 참여자만 대체 요청을 올릴 수 있습니다");
    await expect(box.getByLabel("사유")).toHaveValue("중복 업무입니다");
    await expect(searchDialog(page)).toBeVisible();
  });
});

test.describe("직권 대체", () => {
  test("사유 필수, 제출 본문(supersedesItemId, reason), 성공 후 원장 갱신과 대체 정보 표시", async ({ page }) => {
    const calls = await open(page, { options: { supersede: "direct" }, similarResponder: FOUND });
    const before = calls.detailLoads;
    await searchButton(page, "설비 점검").click();
    await candidates(page).getByRole("button", { name: "직권 대체: 설비 점검", exact: true }).click();
    const box = reasonDialog(page);
    const confirm = box.getByRole("button", { name: "직권 대체" });
    await expect(confirm).toBeDisabled();
    await box.getByLabel("사유").fill("오늘 회의 결정으로 대체");
    await confirm.click();
    await expect(searchDialog(page)).toBeHidden();
    expect(calls.supersede).toEqual([{ supersedesItemId: 301, reason: "오늘 회의 결정으로 대체" }]);
    await expect(page.getByText("설비 점검: 직권 대체했습니다")).toBeVisible();
    expect(calls.detailLoads).toBeGreaterThan(before); // 업무 원장을 다시 받았다
    await expect(row(page, "설비 점검")).toContainText("대체한 과거 업무 1건");
    await expect(row(page, "설비 점검").getByRole("link", { name: "대체한 과거 업무 보기: 지난 회의 30" })).toHaveAttribute("href", "/v2/meetings/30");
    await expect(searchButton(page, "설비 점검")).toBeFocused();
  });

  test("서버 오류(409)는 팝업 안에 보이고 입력이 유지되며, 고친 뒤 다시 보낼 수 있다. 중복 클릭은 한 번만 보낸다", async ({ page }) => {
    const calls = await open(page, {
      options: { supersede: "direct" },
      similarResponder: FOUND,
      supersedeDelayMs: 300,
      supersedeResponder: (attempt) => (attempt === 1 ? { status: 409, body: { detail: "이미 다른 업무가 대체했습니다" } } : { status: 200, body: { oldItemId: 301, newItemId: 102, projectId: 5, changed: true } }),
    });
    await searchButton(page, "설비 점검").click();
    await candidates(page).getByRole("button", { name: "직권 대체: 설비 점검", exact: true }).click();
    const box = reasonDialog(page);
    await box.getByLabel("사유").fill("정리");
    const confirm = box.getByRole("button", { name: "직권 대체" });
    await confirm.dblclick();
    await expect(box.getByRole("alert")).toContainText("이미 다른 업무가 대체했습니다");
    expect(calls.supersede).toHaveLength(1);
    await expect(box.getByLabel("사유")).toHaveValue("정리");
    await box.getByRole("button", { name: "직권 대체" }).click();
    await expect(searchDialog(page)).toBeHidden();
    expect(calls.supersede).toHaveLength(2);
  });
});

test.describe("업무 원장의 대체 정보", () => {
  test("대체됨 칩(취소선), 대체한 업무 링크, 제목 null 은 링크 없는 안내, 대체한 과거 업무 N건", async ({ page }) => {
    const detail = baseDetail([
      item(101, "견적서 송부", "confirmed", { supersededBy: { itemId: 500, meetingId: 33, meetingTitle: "오늘 회의" } }),
      item(102, "설비 점검", "confirmed", { supersededBy: { itemId: 501, meetingId: 34, meetingTitle: null } }),
      item(103, "자재 발주", "confirmed", {
        supersedes: [{ itemId: 301, meetingId: 30, meetingTitle: "지난 회의 30" }, { itemId: 302, meetingId: 31, meetingTitle: null }],
      }),
    ]);
    await open(page, { detail });
    const a = row(page, "견적서 송부");
    await expect(a.locator(".mn-chip-superseded")).toHaveText("대체됨");
    expect(await a.locator(".mn-chip-superseded").evaluate((el) => getComputedStyle(el).textDecorationLine)).toContain("line-through");
    await expect(a.getByRole("link", { name: "오늘 회의" })).toHaveAttribute("href", "/v2/meetings/33");
    await expect(a).toContainText("대체한 업무:");
    const b = row(page, "설비 점검");
    await expect(b).toContainText("대체한 업무: 열람 권한이 없는 회의록");
    await expect(b.getByRole("link")).toHaveCount(0);
    const c = row(page, "자재 발주");
    await expect(c).toContainText("대체한 과거 업무 2건");
    await expect(c.getByRole("link", { name: "대체한 과거 업무 보기: 지난 회의 30" })).toHaveAttribute("href", "/v2/meetings/30");
    await expect(c).toContainText("열람 권한이 없는 회의록");
    await expect(c.locator(".mn-chip-superseded")).toHaveCount(0);
  });
});

test.describe("수정 요청 화면", () => {
  const requests = () => [
    { requestId: 1, requester: { id: 9, name: "이서연" }, comment: "기한을 고쳐 주세요", itemId: 102, createdAt: "2026-10-02T01:00:00Z", resolution: null },
    { requestId: 2, requester: { id: 9, name: "이서연" }, comment: "같은 일입니다", itemId: 102, kind: "supersede", supersedesItemId: 301, createdAt: "2026-10-02T02:00:00Z", resolution: null },
  ];

  test("대체 요청은 '대체 요청' 라벨과 대체될 업무를 보이고, 수정 요청 표시는 그대로", async ({ page }) => {
    await open(page, { changeRequests: requests() });
    await row(page, "설비 점검").getByRole("button", { name: "설비 점검 수정 요청" }).click();
    const list = page.getByRole("dialog", { name: "수정 요청" }).getByRole("list", { name: "이 대상의 수정 요청 내역" });
    const entries = list.locator("> li");
    await expect(entries).toHaveCount(2);
    await expect(entries.nth(0)).not.toContainText("대체 요청");
    await expect(entries.nth(0)).not.toContainText("대체될 업무");
    await expect(entries.nth(0)).toContainText("해결 대기");
    await expect(entries.nth(1)).toContainText("대체 요청");
    await expect(entries.nth(1)).toContainText("대체될 업무 #301");
    await expect(entries.nth(1)).toContainText("같은 일입니다");
  });

  test("대체 요청 수락이 403 으로 거부되면 메시지를 알림으로 보이고 요청은 대기로 남는다", async ({ page }) => {
    const calls = await open(page, {
      changeRequests: requests(),
      resolveResponder: () => ({ status: 403, body: { detail: "대체를 실행할 권한이 없습니다" } }),
    });
    await row(page, "설비 점검").getByRole("button", { name: "설비 점검 수정 요청" }).click();
    const entry = page.getByRole("dialog", { name: "수정 요청" }).getByRole("list", { name: "이 대상의 수정 요청 내역" }).locator("> li").nth(1);
    await entry.getByRole("button", { name: "답변·해결" }).click();
    await entry.getByRole("button", { name: "수락" }).click();
    await expect(entry.getByRole("alert")).toContainText("대체를 실행할 권한이 없습니다");
    expect(calls.resolve).toBe(1);
    await expect(entry).toContainText("해결 대기");
    await expect(entry.getByRole("group", { name: "답변·해결" })).toBeVisible();
  });

  test("할 일 화면의 수정 요청 대기에서도 대체 요청이 구분된다", async ({ page }) => {
    await open(page);
    await page.route("**/api/me/todos", (route) =>
      json(route, 200, {
        awaitingConfirmMeetings: { total: 0, items: [] }, needsCompletionItems: { total: 0, items: [] }, myItems: { total: 0, items: [] },
        unreadAutoConfirmed: { total: 0, items: [] },
        pendingChangeRequests: {
          total: 2,
          items: [
            { requestId: 1, meetingId: 41, meetingTitle: "주간 생산 현안 회의", itemId: 102, itemTitle: "설비 점검", requester: { id: 9, name: "이서연" }, createdAt: "2026-10-02T01:00:00Z", commentPreview: "기한을 고쳐 주세요" },
            { requestId: 2, meetingId: 41, meetingTitle: "주간 생산 현안 회의", itemId: 102, itemTitle: "설비 점검", kind: "supersede", supersedesItemId: 301, requester: { id: 9, name: "이서연" }, createdAt: "2026-10-02T02:00:00Z", commentPreview: "같은 일입니다" },
          ],
        },
      }),
    );
    await page.route("**/api/me/notices", (route) => json(route, 200, []));
    await page.goto("/v2");
    const links = page.getByRole("link").filter({ hasText: "주간 생산 현안 회의" });
    await expect(links).toHaveCount(2);
    await expect(links.nth(0)).not.toContainText("대체 요청");
    await expect(links.nth(1)).toContainText("대체 요청");
    await expect(links.nth(1)).toContainText("대체될 업무 #301");
  });
});

test.describe("반응형", () => {
  for (const width of [1280, 768, 375]) {
    test(`${width}px: 팝업과 원장에 가로 넘침이 없다`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      const detail = baseDetail([
        item(102, "설비 점검", "confirmed", { supersedes: [{ itemId: 301, meetingId: 30, meetingTitle: "지난 회의 30" }] }),
      ]);
      await open(page, { detail, options: { supersede: "direct" }, similarResponder: FOUND });
      await searchButton(page, "설비 점검").click();
      await expect(candidates(page)).toBeVisible();
      const overflow = await page.evaluate(() => {
        const dialog = document.querySelector("[role='dialog']") as HTMLElement;
        return { page: document.documentElement.scrollWidth - document.documentElement.clientWidth, dialog: dialog.scrollWidth - dialog.clientWidth };
      });
      expect(overflow.page).toBeLessThanOrEqual(0);
      expect(overflow.dialog).toBeLessThanOrEqual(0);
    });
  }
});

for (const theme of ["light", "dark"] as const) {
  test(`${theme} 테마: 콘솔 오류 없음, 칩·점수 글자 대비 4.5:1 이상`, async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    page.on("pageerror", (e) => errors.push(e.message));
    await page.addInitScript((t) => window.localStorage.setItem("mi.v2.theme", t), theme);
    const detail = baseDetail([
      item(101, "견적서 송부", "confirmed", { supersededBy: { itemId: 500, meetingId: 33, meetingTitle: "오늘 회의" } }),
      item(102, "설비 점검", "confirmed"),
    ]);
    await open(page, { detail, options: { supersede: "direct" }, similarResponder: FOUND });
    await searchButton(page, "설비 점검").click();
    await expect(candidates(page)).toBeVisible();
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
      return [...document.querySelectorAll(".mn-chip, [role='dialog'] dt, [role='dialog'] dd, [role='dialog'] .font-mn-mono")].map(ratio);
    });
    expect(ratios.length).toBeGreaterThan(5);
    for (const value of ratios) expect(value).toBeGreaterThanOrEqual(4.5);
    expect(errors).toEqual([]);
  });
}
