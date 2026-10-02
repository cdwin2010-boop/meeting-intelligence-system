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

  test("수정 요청 팝업: 업무 행에서 열기 → 실패 시 팝업 안 서버 문구·입력 유지 → 성공 시 닫힘·목록 표시", async ({ page }) => {
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
    const panel = page.getByRole("region", { name: "수정 요청" });
    await expect(panel.getByText("남긴 수정 요청이 없습니다.")).toBeVisible();
    // 화면 아래 패널에는 작성 입력칸이 없다
    await expect(panel.getByLabel("코멘트")).toHaveCount(0);

    const scrollBefore = await page.evaluate(() => window.scrollY);
    const opener = page.getByRole("button", { name: "정리 자료 공유 수정 요청" });
    await opener.click();
    const dialog = page.getByRole("dialog", { name: "수정 요청" });
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("정리 자료 공유");
    await expect(dialog.getByRole("button", { name: "취소" })).toBeFocused();
    expect(await page.evaluate(() => window.scrollY)).toBe(scrollBefore);

    await dialog.getByLabel("코멘트").fill("기한을 다음 주로 바꿔 주세요");
    await dialog.getByRole("button", { name: "수정 요청 남기기" }).click();
    await expect(dialog.getByRole("alert")).toContainText("수정 요청을 남기지 못했습니다 · 권한이 없습니다");
    await expect(dialog.getByLabel("코멘트")).toHaveValue("기한을 다음 주로 바꿔 주세요");
    await expect(dialog).toBeVisible();

    fail = false;
    await dialog.getByRole("button", { name: "수정 요청 남기기" }).click();
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused(); // 연 버튼으로 포커스 복귀
    expect(created).toEqual([{ comment: "기한을 다음 주로 바꿔 주세요", itemId: 103 }]);
    await expect(panel.getByText("수정 요청을 남겼습니다")).toBeVisible();
    const list = panel.getByRole("list", { name: "수정 요청 목록" });
    await expect(list.getByText("기한을 다음 주로 바꿔 주세요")).toBeVisible();
    await expect(list.getByText("정리 자료 공유")).toBeVisible();
    await expect(list.getByText("해결 대기")).toBeVisible();
    // 수정 요청은 확정 상태를 바꾸지 않는다
    await expect(page.getByRole("button", { name: "회의록 확정" })).toBeVisible();
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
    const panel = page.getByRole("region", { name: "수정 요청" });
    await panel.getByRole("button", { name: "회의록 전체 수정 요청" }).click();
    const dialog = page.getByRole("dialog", { name: "수정 요청" });
    await expect(dialog).toContainText("회의록 전체");
    await dialog.getByLabel("코멘트").fill("요약을 보완해 주세요");
    await dialog.getByRole("button", { name: "수정 요청 남기기" }).click();
    await expect(dialog).toBeHidden();
    expect(created).toEqual([{ comment: "요약을 보완해 주세요", itemId: null }]);
    const list = panel.getByRole("list", { name: "수정 요청 목록" });
    await expect(list.getByText("요약을 보완해 주세요")).toBeVisible();
    await expect(list.getByText("회의록 전체")).toBeVisible();
  });

  test("수정 요청 팝업: 취소·Esc·배경 클릭으로 닫힘, 요청 없음, 포커스 복귀", async ({ page }) => {
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
    await dialog.getByRole("button", { name: "취소" }).click();
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
});
