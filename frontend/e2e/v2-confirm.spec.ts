/*
 * v2 회의록 상세 · 회의록 확정 / 업무 확정 / 수정 요청 E2E. 실제 백엔드 없이 page.route 로 v2 API 를 가로채 가짜로 응답한다.
 * (/api/auth/me, /api/meetings/{id}, /transcript, /speakers, /api/accounts, /confirm, /change-requests,
 *  POST /api/action-items/{id}/confirm) 데이터는 모두 가상이다.
 */
import { expect, test, type Page, type Route } from "@playwright/test";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const ACCOUNT = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const READY_ITEM = {
  id: 103, title: "정리 자료 공유", assignee: { id: 9, name: "이서연" }, dueDate: "2026-10-09", dueUndetermined: false,
  status: "pending", confirmKind: null, evidenceStartSec: null, evidenceQuote: null, needsCompletion: false, missingFields: [],
};
const NEEDS_ITEM = {
  id: 104, title: "견적 재검토", assignee: null, dueDate: null, dueUndetermined: true,
  status: "pending", confirmKind: null, evidenceStartSec: null, evidenceQuote: null, needsCompletion: true, missingFields: ["assignee"],
};

const DETAIL = {
  id: 41,
  title: "주간 생산 현안 회의",
  heldAt: "2026-10-01T01:00:00Z",
  summary: "",
  decisions: [],
  status: "awaiting_confirmation",
  confirmKind: null,
  confirmedBy: null,
  confirmedAt: null,
  firstCreatedAt: "2026-10-01T02:00:00Z",
  autoConfirmAt: "2026-10-06T02:00:00Z",
  registeredBy: { id: 7, name: "한팀장" },
  origin: "audio_minutes",
  participants: [],
  actionItems: [READY_ITEM, NEEDS_ITEM],
  recentEvents: [],
};

interface Mocks {
  meetingConfirm?: (route: Route) => unknown;
  itemConfirm?: (route: Route, itemId: number) => unknown;
  requestsGet?: (route: Route) => unknown;
  requestsPost?: (route: Route) => unknown;
}

async function openDetail(page: Page, mocks: Mocks) {
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}`
      ? json(route, 200, ACCOUNT)
      : json(route, 401, { detail: "인증이 필요합니다" }),
  );
  await page.route(/\/api\/meetings\/41$/, (route) => json(route, 200, DETAIL));
  await page.route(/\/api\/meetings\/41\/transcript$/, (route) => json(route, 404, { detail: "전사문이 없습니다" }));
  await page.route(/\/api\/meetings\/41\/speakers$/, (route) => json(route, 200, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route("**/api/accounts", (route) => json(route, 200, []));
  await page.route(/\/api\/meetings\/41\/confirm$/, (route) =>
    (mocks.meetingConfirm ?? ((r: Route) => json(r, 500, { detail: "없음" })))(route) as Promise<void>,
  );
  await page.route(/\/api\/action-items\/(\d+)\/confirm$/, (route) => {
    const itemId = Number(/action-items\/(\d+)\//.exec(route.request().url())?.[1]);
    return (mocks.itemConfirm ?? ((r: Route) => json(r, 500, { detail: "없음" })))(route, itemId) as Promise<void>;
  });
  await page.route(/\/api\/meetings\/41\/change-requests$/, (route) =>
    route.request().method() === "POST"
      ? ((mocks.requestsPost ?? ((r: Route) => json(r, 500, { detail: "없음" })))(route) as Promise<void>)
      : ((mocks.requestsGet ?? ((r: Route) => json(r, 200, [])))(route) as Promise<void>),
  );
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto("/v2/meetings/41");
  await expect(page.getByRole("heading", { level: 1, name: "주간 생산 현안 회의" })).toBeVisible();
}

const ledger = (page: Page) => page.getByRole("region", { name: "업무 원장" });

test.describe("v2 회의록·업무 확정과 수정 요청", () => {
  test("회의록 확정: 확인 대화상자(취소에 먼저 포커스) → 확정 → 상태 갱신, 버튼 사라짐", async ({ page }) => {
    let calls = 0;
    await openDetail(page, {
      meetingConfirm: (route) => {
        calls += 1;
        expect(route.request().method()).toBe("POST");
        expect(new URL(route.request().url()).search).toBe(""); // 업무는 함께 확정하지 않음
        return json(route, 200, {
          id: 41, status: "confirmed", confirmKind: "manager", confirmedBy: { id: 7, name: "한팀장" },
          confirmedAt: "2026-10-02T03:00:00Z", confirmedItemIds: [], skippedItemIds: [],
        });
      },
    });
    const header = page.locator("header").filter({ has: page.getByRole("heading", { level: 1 }) });
    await expect(header.getByText("확정 대기")).toBeVisible();
    await page.getByRole("button", { name: "회의록 확정" }).click();

    const dialog = page.getByRole("dialog", { name: "회의록을 확정할까요?" });
    await expect(dialog.getByRole("button", { name: "취소" })).toBeFocused();
    await dialog.getByRole("button", { name: "회의록 확정" }).click();

    await expect(dialog).toBeHidden();
    expect(calls).toBe(1);
    await expect(header.getByText("확정 완료")).toBeVisible();
    await expect(page.getByRole("button", { name: "회의록 확정" })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "회의 개요" }).getByText("관리자 확정 · 한팀장")).toBeVisible();
    // 업무는 그대로 확정 전
    await expect(page.getByRole("button", { name: "정리 자료 공유 업무 확정" })).toBeVisible();
  });

  test("업무 확정 성공 → 행 상태 확정됨, 확정 버튼 사라짐", async ({ page }) => {
    const confirmed: number[] = [];
    await openDetail(page, {
      itemConfirm: (route, itemId) => {
        confirmed.push(itemId);
        return json(route, 200, { ...READY_ITEM, status: "confirmed", confirmKind: "manager" });
      },
    });
    await page.getByRole("button", { name: "정리 자료 공유 업무 확정" }).click();
    await expect(ledger(page).getByText("정리 자료 공유: 확정했습니다")).toBeVisible();
    expect(confirmed).toEqual([103]);
    const row = ledger(page).getByRole("row").filter({ hasText: "정리 자료 공유" });
    await expect(row.getByText("확정됨")).toBeVisible();
    await expect(page.getByRole("button", { name: "정리 자료 공유 업무 확정" })).toHaveCount(0);
  });

  test("보완 필요 업무 확정 → 서버 409 문구 그대로 표시, 행은 그대로", async ({ page }) => {
    await openDetail(page, {
      itemConfirm: (route) =>
        json(route, 409, { detail: { message: "보완 필요 항목이 있어 확정할 수 없습니다", missingFields: ["assignee"] } }),
    });
    await page.getByRole("button", { name: "견적 재검토 업무 확정" }).click();
    await expect(ledger(page).getByRole("alert")).toContainText("견적 재검토: 확정하지 못했습니다 · 보완 필요 항목이 있어 확정할 수 없습니다");
    const row = ledger(page).getByRole("row").filter({ hasText: "견적 재검토" });
    await expect(row.getByText("보완 필요", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "견적 재검토 업무 확정" })).toBeEnabled();
  });

  test("403 → 회의록 확정·업무 확정 모두 서버 문구 표시", async ({ page }) => {
    await openDetail(page, {
      meetingConfirm: (route) => json(route, 403, { detail: "회의록을 총괄하는 관리자 또는 지시자만 확정할 수 있습니다" }),
      itemConfirm: (route) => json(route, 403, { detail: "이 업무를 수정·확정할 권한이 없습니다" }),
    });
    await page.getByRole("button", { name: "회의록 확정" }).click();
    const dialog = page.getByRole("dialog", { name: "회의록을 확정할까요?" });
    await dialog.getByRole("button", { name: "회의록 확정" }).click();
    await expect(dialog.getByRole("alert")).toContainText("회의록을 총괄하는 관리자 또는 지시자만 확정할 수 있습니다");
    await dialog.getByRole("button", { name: "취소" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByRole("button", { name: "회의록 확정" })).toBeFocused(); // 여는 버튼으로 포커스 복귀

    await page.getByRole("button", { name: "정리 자료 공유 업무 확정" }).click();
    await expect(ledger(page).getByRole("alert")).toContainText("이 업무를 수정·확정할 권한이 없습니다");
  });

  test("수정 요청 팝업: 업무 행에서 열기 → 실패 시 팝업 안 서버 문구·입력 유지 → 성공 시 팝업 내역에 추가", async ({ page }) => {
    const created: unknown[] = [];
    let fail = true;
    await openDetail(page, {
      requestsPost: (route) => {
        if (fail) return json(route, 403, { detail: "권한이 없습니다" });
        created.push(route.request().postDataJSON());
        return json(route, 201, { requestId: 501 });
      },
      requestsGet: (route) =>
        json(route, 200, created.length === 0 ? [] : [
          { requestId: 501, requester: { id: 7, name: "한팀장" }, comment: "기한을 다음 주로 바꿔 주세요", itemId: 103,
            createdAt: "2026-10-02T03:00:00Z", resolution: null },
        ]),
    });
    // 본문에는 수정 요청 영역·작성 입력칸이 없다
    await expect(page.getByRole("region", { name: "수정 요청", exact: true })).toHaveCount(0);
    await expect(page.getByLabel("코멘트")).toHaveCount(0);

    const scrollBefore = await page.evaluate(() => window.scrollY);
    const opener = page.getByRole("button", { name: "정리 자료 공유 수정 요청" });
    await opener.click();
    const dialog = page.getByRole("dialog", { name: "수정 요청" });
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("정리 자료 공유");
    await expect(dialog.getByRole("button", { name: "닫기" })).toBeFocused();
    expect(await page.evaluate(() => window.scrollY)).toBe(scrollBefore);
    const history = dialog.getByRole("region", { name: "수정 요청 내역" });
    await expect(history.getByText("남긴 수정 요청이 없습니다.")).toBeVisible();

    await dialog.getByLabel("코멘트").fill("기한을 다음 주로 바꿔 주세요");
    await dialog.getByRole("button", { name: "수정 요청 남기기" }).click();
    await expect(dialog.getByRole("alert")).toContainText("수정 요청을 남기지 못했습니다 · 권한이 없습니다");
    await expect(dialog.getByLabel("코멘트")).toHaveValue("기한을 다음 주로 바꿔 주세요");
    await expect(dialog).toBeVisible();

    fail = false;
    await dialog.getByRole("button", { name: "수정 요청 남기기" }).click();
    await expect(dialog.getByText("수정 요청을 남겼습니다")).toBeVisible();
    // 팝업은 열린 채로 하단 내역에 바로 추가되고 입력은 비워진다
    await expect(dialog).toBeVisible();
    expect(created).toEqual([{ comment: "기한을 다음 주로 바꿔 주세요", itemId: 103 }]);
    const entries = history.getByRole("list", { name: "이 대상의 수정 요청 내역" });
    await expect(entries.getByText("기한을 다음 주로 바꿔 주세요")).toBeVisible();
    await expect(entries.getByText("한팀장")).toBeVisible();
    await expect(entries.getByText("해결 대기")).toBeVisible();
    await expect(dialog.getByLabel("코멘트")).toHaveValue("");

    await dialog.getByRole("button", { name: "닫기" }).click();
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused(); // 연 버튼으로 포커스 복귀
    // 다시 열어도 받아 둔 목록에 새 요청이 남아 있다
    await opener.click();
    await expect(entries.getByText("기한을 다음 주로 바꿔 주세요")).toBeVisible();
    await page.keyboard.press("Escape");
    // 수정 요청은 확정 상태를 바꾸지 않는다
    await expect(page.getByRole("button", { name: "회의록 확정" })).toBeVisible();
  });

  test("수정 요청 팝업 내역: 연 대상의 요청만 표시, 없으면 안내, 열 때 목록을 다시 요청하지 않음", async ({ page }) => {
    let gets = 0;
    await openDetail(page, {
      requestsGet: (route) => {
        gets += 1;
        return json(route, 200, [
          { requestId: 601, requester: { id: 7, name: "한팀장" }, comment: "공유 범위를 넓혀 주세요", itemId: 103,
            createdAt: "2026-10-02T03:00:00Z", resolution: null },
          { requestId: 602, requester: { id: 9, name: "이서연" }, comment: "요약 문장 수정", itemId: null,
            createdAt: "2026-10-02T04:00:00Z", resolution: { decision: "accepted", reason: null, resolvedBy: null, resolvedAt: "2026-10-02T05:00:00Z" } },
        ]);
      },
    });
    const dialog = page.getByRole("dialog", { name: "수정 요청" });
    const entries = dialog.getByRole("list", { name: "이 대상의 수정 요청 내역" });

    await page.getByRole("button", { name: "정리 자료 공유 수정 요청" }).click();
    await expect(entries.getByRole("listitem")).toHaveCount(1);
    await expect(entries.getByText("공유 범위를 넓혀 주세요")).toBeVisible();
    await expect(entries.getByText("해결 대기")).toBeVisible();
    // 첫 팝업에서 목록이 보인 뒤부터는 다시 열어도 목록 요청이 늘지 않는다(개발 모드 StrictMode 의 첫 중복 요청은 제외)
    const loaded = gets;
    await page.keyboard.press("Escape");

    await page.getByRole("button", { name: "회의록 전체 수정 요청" }).click();
    await expect(entries.getByRole("listitem")).toHaveCount(1);
    await expect(entries.getByText("요약 문장 수정")).toBeVisible();
    await expect(entries.getByText("이서연")).toBeVisible();
    await expect(entries.getByText("수락됨")).toBeVisible();
    await page.keyboard.press("Escape");

    await page.getByRole("button", { name: "견적 재검토 수정 요청" }).click();
    await expect(dialog.getByRole("region", { name: "수정 요청 내역" }).getByText("남긴 수정 요청이 없습니다.")).toBeVisible();
    await expect(entries).toHaveCount(0);
    expect(gets).toBe(loaded);
  });

  test("수정 요청 팝업: 회의록 전체 대상으로 남기기(itemId null)", async ({ page }) => {
    const created: unknown[] = [];
    await openDetail(page, {
      requestsPost: (route) => {
        created.push(route.request().postDataJSON());
        return json(route, 201, { requestId: 502 });
      },
      requestsGet: (route) =>
        json(route, 200, created.length === 0 ? [] : [
          { requestId: 502, requester: { id: 7, name: "한팀장" }, comment: "요약을 보완해 주세요", itemId: null,
            createdAt: "2026-10-02T03:00:00Z", resolution: null },
        ]),
    });
    await page.getByRole("button", { name: "회의록 전체 수정 요청" }).click();
    const dialog = page.getByRole("dialog", { name: "수정 요청" });
    await expect(dialog).toContainText("회의록 전체");
    await dialog.getByLabel("코멘트").fill("요약을 보완해 주세요");
    await dialog.getByRole("button", { name: "수정 요청 남기기" }).click();
    await expect(dialog.getByRole("list", { name: "이 대상의 수정 요청 내역" }).getByText("요약을 보완해 주세요")).toBeVisible();
    expect(created).toEqual([{ comment: "요약을 보완해 주세요", itemId: null }]);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
  });

  test("수정 요청 팝업: 닫기·Esc·배경 클릭으로 닫힘, 요청 없음, 포커스 복귀", async ({ page }) => {
    let posts = 0;
    await openDetail(page, {
      requestsPost: (route) => {
        posts += 1;
        return json(route, 201, { requestId: 503 });
      },
    });
    const opener = page.getByRole("button", { name: "견적 재검토 수정 요청" });
    const dialog = page.getByRole("dialog", { name: "수정 요청" });

    await opener.click();
    await dialog.getByLabel("코멘트").fill("임시 메모");
    await dialog.getByRole("button", { name: "닫기" }).click();
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();

    // 다시 열면 입력은 비어 있다
    await opener.click();
    await expect(dialog.getByLabel("코멘트")).toHaveValue("");
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();

    await opener.click();
    await expect(dialog).toBeVisible();
    await page.mouse.click(5, 5); // 배경
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
    expect(posts).toBe(0);
  });

  test.describe("수정 요청 팝업 내역 쪽 나누기(10건씩, 화면에서 나눔)", () => {
    const makeRequests = (count: number, itemId: number | null = 103, startId = 700) =>
      Array.from({ length: count }, (_, i) => ({
        requestId: startId + i, requester: { id: 7, name: "한팀장" }, comment: `요청 ${i + 1}번`, itemId,
        createdAt: "2026-10-02T03:00:00Z", resolution: null,
      }));

    test("10건 이하 → 쪽 번호 없음", async ({ page }) => {
      await openDetail(page, { requestsGet: (route) => json(route, 200, makeRequests(10)) });
      await page.getByRole("button", { name: "정리 자료 공유 수정 요청" }).click();
      const dialog = page.getByRole("dialog", { name: "수정 요청" });
      await expect(dialog.getByRole("list", { name: "이 대상의 수정 요청 내역" }).getByRole("listitem")).toHaveCount(10);
      await expect(dialog.getByRole("navigation", { name: "수정 요청 내역 쪽" })).toHaveCount(0);
    });

    test("25건 → 첫 쪽 10건만, 2쪽·다음·이전 이동, 다시 열면 첫 쪽", async ({ page }) => {
      // 다른 대상의 요청은 건수에 들어가지 않는다
      await openDetail(page, { requestsGet: (route) => json(route, 200, [...makeRequests(25), ...makeRequests(3, null, 900)]) });
      const opener = page.getByRole("button", { name: "정리 자료 공유 수정 요청" });
      await opener.click();
      const dialog = page.getByRole("dialog", { name: "수정 요청" });
      const entries = dialog.getByRole("list", { name: "이 대상의 수정 요청 내역" });
      const pager = dialog.getByRole("navigation", { name: "수정 요청 내역 쪽" });
      await expect(entries.getByRole("listitem")).toHaveCount(10);
      await expect(entries.getByRole("listitem").first()).toContainText("요청 1번");
      await expect(entries.getByRole("listitem").last()).toContainText("요청 10번");
      await expect(pager).toContainText("1–10 / 25");
      await expect(pager.locator('[aria-current="page"]')).toHaveText("1");
      await expect(pager.getByRole("button", { name: "이전" })).toBeDisabled();

      await pager.getByRole("button", { name: "2쪽" }).click();
      await expect(entries.getByRole("listitem").first()).toContainText("요청 11번");
      await expect(entries.getByRole("listitem")).toHaveCount(10);
      await expect(pager.locator('[aria-current="page"]')).toHaveText("2");
      await expect(pager).toContainText("11–20 / 25");

      await pager.getByRole("button", { name: "다음" }).click();
      await expect(entries.getByRole("listitem")).toHaveCount(5);
      await expect(entries.getByRole("listitem").last()).toContainText("요청 25번");
      await expect(pager.getByRole("button", { name: "다음" })).toBeDisabled();
      await pager.getByRole("button", { name: "이전" }).click();
      await expect(pager.locator('[aria-current="page"]')).toHaveText("2");

      await page.keyboard.press("Escape");
      await opener.click();
      await expect(pager.locator('[aria-current="page"]')).toHaveText("1");
      await expect(entries.getByRole("listitem").first()).toContainText("요청 1번");
    });

    test("새 요청 후 팝업 열린 채 갱신 → 새 요청이 있는 마지막 쪽 표시", async ({ page }) => {
      const requests = makeRequests(10);
      await openDetail(page, {
        requestsGet: (route) => json(route, 200, requests),
        requestsPost: (route) => {
          const body = route.request().postDataJSON() as { comment: string; itemId: number | null };
          requests.push({ ...makeRequests(1, body.itemId, 800)[0], comment: body.comment });
          return json(route, 201, { requestId: 800 });
        },
      });
      await page.getByRole("button", { name: "정리 자료 공유 수정 요청" }).click();
      const dialog = page.getByRole("dialog", { name: "수정 요청" });
      const entries = dialog.getByRole("list", { name: "이 대상의 수정 요청 내역" });
      const pager = dialog.getByRole("navigation", { name: "수정 요청 내역 쪽" });
      await expect(pager).toHaveCount(0);

      await dialog.getByLabel("코멘트").fill("열한 번째 요청");
      await dialog.getByRole("button", { name: "수정 요청 남기기" }).click();
      await expect(dialog).toBeVisible();
      await expect(pager).toContainText("11–11 / 11");
      await expect(pager.locator('[aria-current="page"]')).toHaveText("2");
      await expect(entries.getByRole("listitem")).toHaveCount(1);
      await expect(entries.getByText("열한 번째 요청")).toBeVisible();

      await pager.getByRole("button", { name: "1쪽" }).click();
      await expect(entries.getByRole("listitem")).toHaveCount(10);
    });
  });

  test.describe("수정 요청 답변·해결", () => {
    const pending = { requestId: 801, requester: { id: 9, name: "이서연" }, comment: "기한을 하루 늦춰 주세요", itemId: 103,
      createdAt: "2026-10-02T03:00:00Z", resolution: null };
    const RESOLVE_URL = /\/api\/meetings\/41\/change-requests\/(\d+)\/resolve$/;

    async function openHistory(page: Page) {
      await page.getByRole("button", { name: "정리 자료 공유 수정 요청" }).click();
      const dialog = page.getByRole("dialog", { name: "수정 요청" });
      const entry = dialog.getByRole("list", { name: "이 대상의 수정 요청 내역" }).getByRole("listitem").first();
      await expect(entry).toContainText("해결 대기");
      return entry;
    }

    test("답변 적고 수락 → 처리자·처리 시각·답변 표시, 요청 주소·본문 확인", async ({ page }) => {
      let body: unknown = null;
      let url = "";
      await page.route(RESOLVE_URL, (route) => {
        url = route.request().url();
        body = route.request().postDataJSON();
        return json(route, 200, {
          ...pending,
          resolution: { decision: "accepted", reason: "다음 주 월요일로 옮겼습니다", resolvedBy: { id: 7, name: "한팀장" }, resolvedAt: "2026-10-02T06:00:00Z" },
        });
      });
      await openDetail(page, { requestsGet: (route) => json(route, 200, [pending]) });
      const entry = await openHistory(page);

      await entry.getByRole("button", { name: "답변·해결" }).click();
      const form = entry.getByRole("group", { name: "답변·해결" });
      await form.getByLabel("답변").fill("다음 주 월요일로 옮겼습니다");
      await form.getByRole("button", { name: "수락" }).click();

      await expect(entry).toContainText("수락됨");
      await expect(entry).not.toContainText("해결 대기");
      const info = entry.getByLabel("처리 정보");
      await expect(info).toContainText("한팀장");
      await expect(info).toContainText("다음 주 월요일로 옮겼습니다");
      await expect(info.locator(".font-mn-mono")).not.toBeEmpty();
      await expect(entry.getByRole("button", { name: "답변·해결" })).toHaveCount(0);
      expect(url).toMatch(/\/change-requests\/801\/resolve$/);
      expect(body).toEqual({ decision: "accepted", reason: "다음 주 월요일로 옮겼습니다" });
    });

    test("403 → 서버 문구 표시, 답변 유지, 다시 반려(답변 없음) 가능", async ({ page }) => {
      const bodies: unknown[] = [];
      let fail = true;
      await page.route(RESOLVE_URL, (route) => {
        bodies.push(route.request().postDataJSON());
        if (fail) return json(route, 403, { detail: "권한이 없습니다" });
        return json(route, 200, {
          ...pending,
          resolution: { decision: "rejected", reason: null, resolvedBy: { id: 2, name: "최임원" }, resolvedAt: "2026-10-02T06:00:00Z" },
        });
      });
      await openDetail(page, { requestsGet: (route) => json(route, 200, [pending]) });
      const entry = await openHistory(page);

      await entry.getByRole("button", { name: "답변·해결" }).click();
      const form = entry.getByRole("group", { name: "답변·해결" });
      await form.getByLabel("답변").fill("이번에는 어렵습니다");
      await form.getByRole("button", { name: "반려" }).click();
      await expect(form.getByRole("alert")).toContainText("처리하지 못했습니다 · 권한이 없습니다");
      await expect(form.getByLabel("답변")).toHaveValue("이번에는 어렵습니다");
      await expect(entry).toContainText("해결 대기");

      fail = false;
      await form.getByLabel("답변").fill("");
      await form.getByRole("button", { name: "반려" }).click();
      await expect(entry).toContainText("반려됨");
      await expect(entry.getByLabel("처리 정보")).toContainText("최임원");
      expect(bodies).toEqual([
        { decision: "rejected", reason: "이번에는 어렵습니다" },
        { decision: "rejected", reason: null },
      ]);
    });

    test("상단 회의록 전체 버튼으로 팝업 열림, 본문에 수정 요청 영역 없음", async ({ page }) => {
      await openDetail(page, {});
      await expect(page.getByRole("region", { name: "수정 요청", exact: true })).toHaveCount(0);
      const header = page.locator("header").filter({ has: page.getByRole("heading", { level: 1 }) });
      const opener = header.getByRole("button", { name: "회의록 전체 수정 요청" });
      await expect(opener).toBeVisible();
      await opener.click();
      const dialog = page.getByRole("dialog", { name: "수정 요청" });
      await expect(dialog).toContainText("회의록 전체");
      await expect(dialog).toContainText("기록만 남기며 확정 일정은 멈추지 않습니다");
      await page.keyboard.press("Escape");
      await expect(opener).toBeFocused();
    });
  });
});
