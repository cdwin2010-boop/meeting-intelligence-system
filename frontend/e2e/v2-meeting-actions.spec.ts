/*
 * v2 회의록 상세 · 보류·재개·직권 종료·삭제와 업무 종결·삭제(사유 입력 확인 팝업) E2E.
 * 실제 백엔드 없이 page.route 로 v2 API 를 가로채 가짜로 응답한다(상세는 동작에 따라 바뀌는 가짜 상태). 데이터는 모두 가상이다.
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const MANAGER = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const STAFF = { id: 9, name: "이서연", rank: "staff", tenantId: 1 };

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const item = (id: number, title: string, status: string) => ({
  id, title, assignee: { id: 9, name: "이서연" }, dueDate: "2026-10-09", dueUndetermined: false, status,
  confirmKind: status === "pending" ? null : "manager", evidenceStartSec: null, evidenceQuote: null, needsCompletion: false, missingFields: [],
});

type Detail = Record<string, unknown> & { actionItems: ReturnType<typeof item>[] };

const baseDetail = (): Detail => ({
  id: 41, title: "주간 생산 현안 회의", heldAt: "2026-10-01T01:00:00Z", summary: "", decisions: [],
  status: "confirmed", confirmKind: "manager", confirmedBy: null, confirmedAt: null,
  firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: null, registeredBy: { id: 7, name: "한팀장" },
  origin: "audio_minutes", participants: [], recentEvents: [], phase: "active",
  actionItems: [item(101, "견적서 송부", "pending"), item(102, "설비 점검", "confirmed"), item(103, "자료 정리", "closed")],
});

interface Calls {
  bodies: Record<string, unknown[]>;
}

/** 상세 상태(detail)를 바꾸는 가짜 서버. fail[동작] 이 있으면 그 응답으로 거부한다 */
async function openDetail(page: Page, account: typeof MANAGER, detail: Detail, fail: Record<string, [number, string]> = {}) {
  const calls: Calls = { bodies: {} };
  const record = (key: string, route: Route) => {
    const raw = route.request().postData();
    (calls.bodies[key] ??= []).push(raw ? JSON.parse(raw) : null);
  };
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, account) : json(route, 401, { detail: "인증 필요" }),
  );
  await page.route(/\/api\/meetings\/41$/, (route) => json(route, 200, withAllowed(detail, account)));
  await page.route(/\/api\/meetings\/41\/transcript$/, (route) => json(route, 404, { detail: "전사문이 없습니다" }));
  await page.route(/\/api\/meetings\/41\/speakers$/, (route) => json(route, 200, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route(/\/api\/meetings\/41\/change-requests$/, (route) => json(route, 200, []));
  await page.route("**/api/accounts", (route) => json(route, 200, []));
  await page.route(/\/api\/meetings\/41\/(hold|resume|end|delete)$/, (route) => {
    const action = /\/(hold|resume|end|delete)$/.exec(route.request().url())![1];
    record(action, route);
    if (fail[action]) {
      const [status, message] = fail[action];
      delete fail[action];
      return json(route, status, { detail: message });
    }
    if (action === "hold") Object.assign(detail, { phase: "on_hold", onHold: true, onHoldBy: MANAGER, onHoldAt: "2026-10-02T01:00:00Z" });
    if (action === "resume") Object.assign(detail, { phase: "active", onHold: false, resumedBy: MANAGER, resumedAt: "2026-10-02T02:00:00Z" });
    if (action === "end") {
      Object.assign(detail, { phase: "ended", endKind: "manager", endedBy: MANAGER, endedAt: "2026-10-02T03:00:00Z" });
      detail.actionItems = detail.actionItems.map((it) => (it.status === "closed" ? it : { ...it, status: "closed" }));
    }
    if (action === "delete") Object.assign(detail, { phase: "deleted", deletedBy: MANAGER, deletedAt: "2026-10-02T04:00:00Z" });
    return json(route, 200, { id: 41, phase: detail.phase });
  });
  await page.route(/\/api\/action-items\/\d+\/(close|delete)$/, (route) => {
    const [, id, action] = /\/action-items\/(\d+)\/(close|delete)$/.exec(route.request().url())!;
    record(`item-${action}`, route);
    const key = `item-${action}`;
    if (fail[key]) {
      const [status, message] = fail[key];
      delete fail[key];
      return json(route, status, { detail: message });
    }
    const target = detail.actionItems.find((it) => it.id === Number(id))!;
    if (action === "close") target.status = "closed";
    else detail.actionItems = detail.actionItems.filter((it) => it.id !== Number(id)); // 삭제된 업무는 상세에서 빠진다
    return json(route, 200, { ...target, status: action === "close" ? "closed" : "deleted" });
  });
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto("/v2/meetings/41");
  await expect(page.getByRole("heading", { level: 1, name: "주간 생산 현안 회의" })).toBeVisible();
  return calls;
}

const more = (page: Page) => page.getByRole("button", { name: "회의록 더보기" });
const menuItems = (page: Page) => page.getByRole("menu", { name: "회의록 동작" }).getByRole("menuitem");
const dialog = (page: Page) => page.getByRole("alertdialog");
const phaseLabel = (page: Page) => page.getByLabel("회의록 단계");
const row = (page: Page, title: string) => page.getByRole("table", { name: "업무 원장" }).locator("tbody tr").filter({ hasText: title });

async function chooseFromMenu(page: Page, label: string) {
  await more(page).click();
  await menuItems(page).filter({ hasText: label }).click();
  await expect(dialog(page)).toBeVisible();
}

test.describe("v2 회의록 동작과 사유 팝업", () => {
  test("사유 팝업: 빈·공백 사유는 비활성, 서버 거부는 문구 표시·입력 유지, 성공하면 닫힘·갱신·포커스 복귀", async ({ page }) => {
    const calls = await openDetail(page, MANAGER, baseDetail(), { hold: [403, "회의록을 총괄하는 관리자 또는 지시자만 보류·재개할 수 있습니다"] });
    // 키보드로 메뉴 열기·이동·선택
    await more(page).focus();
    await page.keyboard.press("Enter");
    await expect(menuItems(page)).toHaveText(["엑셀 다운로드", "변경 이력", "수정 회의록 업로드", "보류", "직권 종료", "삭제"]);
    await expect(menuItems(page).first()).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(more(page)).toBeFocused();
    await page.keyboard.press("Enter");
    for (let i = 0; i < 3; i += 1) await page.keyboard.press("ArrowDown"); // 엑셀 다운로드·변경 이력·업로드 다음의 보류
    await page.keyboard.press("Enter");

    const box = dialog(page);
    await expect(box).toContainText("회의록 보류");
    await expect(box).toContainText("주간 생산 현안 회의");
    await expect(box.getByRole("button", { name: "취소" })).toBeFocused();
    const confirm = box.getByRole("button", { name: "보류하기" });
    await expect(confirm).toBeDisabled();
    await box.getByLabel("사유").fill("   ");
    await expect(confirm).toBeDisabled();
    await box.getByLabel("사유").fill("  예산 재검토  ");
    await expect(box.getByText("10 / 2000")).toBeVisible();
    await confirm.click();
    await expect(box.getByRole("alert")).toContainText("회의록 보류하지 못했습니다 · 회의록을 총괄하는 관리자 또는 지시자만 보류·재개할 수 있습니다");
    await expect(box.getByLabel("사유")).toHaveValue("  예산 재검토  ");

    await confirm.click();
    await expect(box).toBeHidden();
    await expect(phaseLabel(page)).toHaveText("보류");
    await expect(more(page)).toBeFocused();
    expect(calls.bodies.hold).toEqual([{ reason: "예산 재검토" }, { reason: "예산 재검토" }]);
  });

  test("보류 → 재개(사유 없음, 기한 안내) → 진행중", async ({ page }) => {
    const detail = { ...baseDetail(), phase: "on_hold", onHold: true, onHoldBy: MANAGER, onHoldAt: "2026-10-02T01:00:00Z" };
    const calls = await openDetail(page, MANAGER, detail);
    await more(page).click();
    await expect(menuItems(page)).toHaveText(["엑셀 다운로드", "변경 이력", "삭제"]);
    await page.keyboard.press("Escape");
    const resume = page.getByRole("button", { name: "재개", exact: true });
    await resume.click();
    const box = dialog(page);
    await expect(box).toContainText("업무 기한이 모두 비워집니다. 날짜를 다시 설정해야 합니다.");
    await expect(box.getByLabel("사유")).toHaveCount(0);
    await expect(box.getByRole("button", { name: "취소" })).toBeFocused();
    await box.getByRole("button", { name: "재개하기" }).click();
    await expect(box).toBeHidden();
    await expect(phaseLabel(page)).toHaveText("진행중");
    await expect(page.getByRole("button", { name: "재개", exact: true })).toHaveCount(0);
    expect(calls.bodies.resume).toEqual([null]); // 사유를 보내지 않는다
  });

  test("직권 종료: 확정 전 업무도 종결 안내 → 종료, 메뉴에는 삭제만", async ({ page }) => {
    const calls = await openDetail(page, MANAGER, baseDetail());
    await chooseFromMenu(page, "직권 종료");
    const box = dialog(page);
    await expect(box).toContainText("확정 전 업무도 함께 종결됩니다");
    await box.getByLabel("사유").fill("프로젝트 취소");
    await box.getByRole("button", { name: "직권 종료하기" }).click();
    await expect(box).toBeHidden();
    await expect(phaseLabel(page)).toHaveText("종료 · 관리자 직권 종료");
    await expect(row(page, "견적서 송부")).toContainText("종결");
    expect(calls.bodies.end).toEqual([{ reason: "프로젝트 취소" }]);
    await more(page).click();
    await expect(menuItems(page)).toHaveText(["엑셀 다운로드", "변경 이력", "삭제"]);
  });

  test("회의록 삭제: 상세에 남아 '삭제'로 표시, 동작 없음", async ({ page }) => {
    const calls = await openDetail(page, MANAGER, baseDetail());
    await chooseFromMenu(page, "삭제");
    const box = dialog(page);
    await expect(box).toContainText("회의록 삭제");
    await box.getByLabel("사유").fill("잘못 올린 파일");
    await box.getByRole("button", { name: "삭제하기" }).click();
    await expect(box).toBeHidden();
    await expect(page).toHaveURL(/\/v2\/meetings\/41$/);
    await expect(phaseLabel(page)).toHaveText("삭제");
    await more(page).click();
    await expect(menuItems(page)).toHaveText(["엑셀 다운로드", "변경 이력"]); // 삭제됨: 서버 허용 동작은 다운로드·이력뿐
    await page.keyboard.press("Escape");
    expect(calls.bodies.delete).toEqual([{ reason: "잘못 올린 파일" }]);
  });

  test("업무 종결: 확정된 업무에만 버튼, 성공하면 '종결' 표시", async ({ page }) => {
    const calls = await openDetail(page, MANAGER, baseDetail());
    await expect(row(page, "견적서 송부").getByRole("button", { name: /업무 종결/ })).toHaveCount(0); // 확정 전
    await expect(row(page, "자료 정리").getByRole("button", { name: /업무 종결/ })).toHaveCount(0); // 이미 종결
    await expect(row(page, "자료 정리")).toContainText("종결");
    const opener = row(page, "설비 점검").getByRole("button", { name: "설비 점검 업무 종결" });
    await opener.click();
    const box = dialog(page);
    await expect(box).toContainText("업무 종결");
    await expect(box).toContainText("설비 점검");
    await box.getByLabel("사유").fill("점검 완료");
    await box.getByRole("group", { name: "종결 구분" }).locator("label").filter({ hasText: "정상 완료" }).click(); // 70-1: 종결 구분 선택
    await box.getByRole("button", { name: "업무 종결하기" }).click();
    await expect(box).toBeHidden();
    await expect(row(page, "설비 점검")).toContainText("종결");
    await expect(row(page, "설비 점검").getByRole("button", { name: /업무 종결/ })).toHaveCount(0);
    expect(calls.bodies["item-close"]).toEqual([{ reason: "점검 완료", closureKind: "completed" }]);
  });

  test("업무 삭제: 모든 업무에 버튼, 팝업에 업무명, 성공하면 원장에서 사라짐", async ({ page }) => {
    const calls = await openDetail(page, MANAGER, baseDetail(), { "item-delete": [409, "보류 중인 회의록입니다. 재개한 뒤에 다시 시도하세요"] });
    for (const title of ["견적서 송부", "설비 점검", "자료 정리"]) {
      await expect(row(page, title).getByRole("button", { name: `${title} 업무 삭제` })).toBeVisible();
    }
    await row(page, "견적서 송부").getByRole("button", { name: "견적서 송부 업무 삭제" }).click();
    const box = dialog(page);
    await expect(box).toContainText("업무 삭제");
    await expect(box).toContainText("견적서 송부");
    await box.getByLabel("사유").fill("중복 등록");
    await box.getByRole("button", { name: "삭제하기" }).click();
    await expect(box.getByRole("alert")).toContainText("보류 중인 회의록입니다. 재개한 뒤에 다시 시도하세요");
    await expect(box.getByLabel("사유")).toHaveValue("중복 등록");
    await box.getByRole("button", { name: "삭제하기" }).click();
    await expect(box).toBeHidden();
    await expect(row(page, "견적서 송부")).toHaveCount(0);
    expect(calls.bodies["item-delete"]).toEqual([{ reason: "중복 등록" }, { reason: "중복 등록" }]);
  });

  test("담당자에게는 회의록·업무 관리자 동작 버튼이 없다", async ({ page }) => {
    await openDetail(page, STAFF, baseDetail());
    await more(page).click(); // 담당자에게도 열람 가능자용 메뉴(엑셀 다운로드·변경 이력)는 보이고, 관리자용 항목은 없다
    await expect(menuItems(page)).toHaveText(["엑셀 다운로드", "변경 이력"]);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: /업무 종결|업무 삭제|업무 추가|항목 수정/ })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "재개", exact: true })).toHaveCount(0);
  });

  test("상태별 동작: 처리 중은 없음, 처리 실패는 삭제만, 삭제됨은 없음", async ({ page }) => {
    const detail = baseDetail();
    await openDetail(page, MANAGER, Object.assign(detail, { status: "failed" }));
    await more(page).click();
    await expect(menuItems(page)).toHaveText(["엑셀 다운로드", "변경 이력", "수정 회의록 업로드", "삭제"]);

    Object.assign(detail, { status: "processing" });
    await page.reload();
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await more(page).click();
    await expect(menuItems(page)).toHaveText(["엑셀 다운로드", "변경 이력", "수정 회의록 업로드"]); // 처리 중: 위험 동작 없음
    await page.keyboard.press("Escape");

    Object.assign(detail, { status: "confirmed", phase: "deleted", deletedAt: "2026-10-02T04:00:00Z" });
    await page.reload();
    await expect(phaseLabel(page)).toHaveText("삭제");
    await more(page).click();
    await expect(menuItems(page)).toHaveText(["엑셀 다운로드", "변경 이력"]); // 삭제됨: 서버 허용 동작은 다운로드·이력뿐
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "재개", exact: true })).toHaveCount(0);
  });
});
