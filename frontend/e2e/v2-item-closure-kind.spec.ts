/*
 * v2 업무 종결 구분 E2E(작업 70-1): 종결 팝업의 "종결 구분" 필수 라디오, 제출 본문의 closureKind, 종결 업무 칩 라벨(완료·직권 종료·종결).
 * 실제 백엔드 없이 page.route 로 응답한다(데이터는 모두 가상).
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const MANAGER = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const json = (route: Route, status: number, body: unknown) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const item = (id: number, title: string, status: string, closureKind?: string | null) => ({
  id, title, assignee: { id: 9, name: "이서연" }, dueDate: "2026-10-09", dueUndetermined: false, status,
  confirmKind: "manager", evidenceStartSec: null, evidenceQuote: null, needsCompletion: false, missingFields: [],
  ...(closureKind === undefined ? {} : { closureKind }),
});

const detail = () => ({
  id: 41, title: "주간 생산 현안 회의", heldAt: "2026-10-01T01:00:00Z", summary: "", decisions: [],
  status: "confirmed", confirmKind: "manager", confirmedBy: null, confirmedAt: null,
  firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: null, registeredBy: { id: 7, name: "한팀장" },
  origin: "audio_minutes", participants: [], recentEvents: [], phase: "active",
  actionItems: [
    item(102, "설비 점검", "confirmed", null),
    item(103, "정상 종결 건", "closed", "completed"),
    item(104, "직권 종결 건", "closed", "forced"),
    item(105, "구분 없는 종결 건", "closed", null),
    item(106, "필드 없는 종결 건", "closed"),
  ],
});

async function open(page: Page, theme?: "light" | "dark") {
  const closeBodies: unknown[] = [];
  const data = detail();
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, MANAGER) : json(route, 401, { detail: "x" }),
  );
  await page.route(/\/api\/meetings\/41$/, (route) => json(route, 200, withAllowed(data, MANAGER)));
  await page.route(/\/api\/meetings\/41\/transcript$/, (route) => json(route, 404, { detail: "전사문이 없습니다" }));
  await page.route(/\/api\/meetings\/41\/speakers$/, (route) => json(route, 200, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route(/\/api\/meetings\/41\/change-requests$/, (route) => json(route, 200, []));
  await page.route("**/api/accounts", (route) => json(route, 200, []));
  await page.route(/\/api\/action-items\/102\/close$/, (route) => {
    const body = route.request().postDataJSON() as { closureKind?: string };
    closeBodies.push(body);
    const target = data.actionItems[0] as Record<string, unknown>;
    target.status = "closed";
    target.closureKind = body.closureKind ?? null;
    return json(route, 200, target);
  });
  await page.goto("/v2/login");
  await page.evaluate(([k, t, th]) => {
    window.sessionStorage.setItem(k, t);
    if (th) window.localStorage.setItem("mi.v2.theme", th);
  }, [TOKEN_KEY, FAKE_TOKEN, theme ?? ""]);
  await page.goto("/v2/meetings/41");
  await expect(page.getByRole("heading", { level: 1, name: "주간 생산 현안 회의" })).toBeVisible();
  return closeBodies;
}

const row = (page: Page, title: string) => page.getByRole("table", { name: "업무 원장" }).locator("tbody tr").filter({ hasText: title });
const dialog = (page: Page) => page.getByRole("alertdialog");
const kindGroup = (page: Page) => dialog(page).getByRole("group", { name: /종결 구분/ });
const kindLabel = (page: Page, label: string) => kindGroup(page).locator("label").filter({ hasText: label });

async function openClose(page: Page) {
  await row(page, "설비 점검").getByRole("button", { name: "설비 점검 업무 종결" }).click();
  await expect(dialog(page)).toContainText("업무 종결");
}

test.describe("종결 팝업의 종결 구분", () => {
  test("fieldset·legend 라디오 2개, 기본 선택 없음, ●/○ 글리프", async ({ page }) => {
    await open(page);
    await openClose(page);
    await expect(kindGroup(page).locator("legend")).toContainText("종결 구분");
    await expect(kindGroup(page).locator("legend")).toContainText("(필수)");
    const radios = kindGroup(page).getByRole("radio");
    await expect(radios).toHaveCount(2);
    await expect(kindGroup(page).getByRole("radio", { name: "정상 완료" })).not.toBeChecked();
    await expect(kindGroup(page).getByRole("radio", { name: "직권 종료" })).not.toBeChecked();
    await expect(kindLabel(page, "정상 완료")).toContainText("○");
    await kindLabel(page, "정상 완료").click();
    await expect(kindGroup(page).getByRole("radio", { name: "정상 완료" })).toBeChecked();
    await expect(kindLabel(page, "정상 완료")).toContainText("●");
    await expect(kindLabel(page, "직권 종료")).toContainText("○");
  });

  test("구분을 고르지 않고 제출하면 서버를 부르지 않고 알림 + 첫 선택지 포커스", async ({ page }) => {
    const bodies = await open(page);
    await openClose(page);
    await dialog(page).getByLabel("사유").fill("점검 완료");
    await dialog(page).getByRole("button", { name: "업무 종결하기" }).click();
    await expect(dialog(page).getByRole("alert")).toContainText("종결 구분을 선택해 주세요");
    await expect(kindGroup(page).getByRole("radio", { name: "정상 완료" })).toBeFocused();
    expect(bodies).toEqual([]);
    await expect(dialog(page)).toBeVisible();
    // 고르면 알림이 사라지고 제출된다
    await kindLabel(page, "정상 완료").click();
    await expect(dialog(page).getByRole("alert")).toHaveCount(0);
  });

  test("사유 필수는 그대로: 사유가 비면 확인 버튼 비활성", async ({ page }) => {
    await open(page);
    await openClose(page);
    await kindLabel(page, "직권 종료").click();
    await expect(dialog(page).getByRole("button", { name: "업무 종결하기" })).toBeDisabled();
    await dialog(page).getByLabel("사유").fill("   ");
    await expect(dialog(page).getByRole("button", { name: "업무 종결하기" })).toBeDisabled();
  });

  for (const [label, code] of [["정상 완료", "completed"], ["직권 종료", "forced"]] as const) {
    test(`${label} 선택 → 본문에 closureKind=${code}, 칩이 바뀐다`, async ({ page }) => {
      const bodies = await open(page);
      await openClose(page);
      await dialog(page).getByLabel("사유").fill("  점검 끝  ");
      await kindLabel(page, label).click();
      await dialog(page).getByRole("button", { name: "업무 종결하기" }).click();
      await expect(dialog(page)).toBeHidden();
      expect(bodies).toEqual([{ reason: "점검 끝", closureKind: code }]);
    });
  }
});

test("종결 업무 상태 칩: 완료 / 직권 종료 / 종결(구분 없음·필드 없음)", async ({ page }) => {
  await open(page);
  await expect(row(page, "정상 종결 건")).toContainText("완료");
  await expect(row(page, "직권 종결 건")).toContainText("직권 종료");
  await expect(row(page, "구분 없는 종결 건")).toContainText("종결");
  await expect(row(page, "구분 없는 종결 건")).not.toContainText("완료");
  await expect(row(page, "필드 없는 종결 건")).toContainText("종결");
  await expect(row(page, "설비 점검")).toContainText("확정됨");
});

for (const theme of ["light", "dark"] as const) {
  test(`${theme} 테마: 종결 팝업·칩에서 콘솔 오류 없음, 선택 안내 대비 4.5:1 이상`, async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    page.on("pageerror", (e) => errors.push(e.message));
    await open(page, theme);
    await openClose(page);
    await dialog(page).getByLabel("사유").fill("점검 완료");
    await dialog(page).getByRole("button", { name: "업무 종결하기" }).click();
    const alert = dialog(page).getByRole("alert");
    await expect(alert).toContainText("종결 구분을 선택해 주세요");
    const ratio = await alert.evaluate((el) => {
      const parse = (c: string) => (c.match(/[\d.]+/g) ?? []).slice(0, 4).map(Number);
      const lum = ([r, g, b]: number[]) => {
        const f = (v: number) => ((v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
      };
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
    });
    expect(ratio).toBeGreaterThanOrEqual(4.5);
    await expect(row(page, "정상 종결 건")).toContainText("완료");
    expect(errors).toEqual([]);
  });
}
