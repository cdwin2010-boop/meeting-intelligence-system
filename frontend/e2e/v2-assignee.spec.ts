/*
 * v2 회의록 상세 · 업무 담당자 지정/변경 E2E. 실제 백엔드 없이 page.route 로 v2 API 를 가로채 가짜로 응답한다.
 * (/api/auth/me, /api/meetings/{id}, /transcript, /speakers, /api/accounts, PATCH /api/action-items/{id}) 데이터는 모두 가상이다.
 */
import { expect, test, type Page, type Route } from "@playwright/test";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const ACCOUNT = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const ACCOUNTS = [
  { id: 2, name: "최임원", rank: "executive" },
  { id: 9, name: "이서연", rank: "staff" },
];

type Ref = { id: number; name: string } | null;

const item = (assignee: Ref, status = "pending") => ({
  id: 103, title: "정리 자료 공유", assignee, dueDate: "2026-10-09", dueUndetermined: false, status,
  confirmKind: status === "confirmed" ? "manager" : null, evidenceStartSec: null, evidenceQuote: null,
  needsCompletion: assignee === null, missingFields: assignee === null ? ["assignee"] : [],
});

const detail = (actionItem: ReturnType<typeof item>) => ({
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
  autoConfirmAt: null,
  registeredBy: { id: 7, name: "한팀장" },
  origin: "audio_minutes",
  participants: [],
  actionItems: [actionItem],
  recentEvents: [],
});

interface Mocks {
  initial: ReturnType<typeof item>;
  patch: (route: Route) => unknown;
  accounts?: (route: Route) => unknown;
}

async function openDetail(page: Page, mocks: Mocks) {
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}`
      ? json(route, 200, ACCOUNT)
      : json(route, 401, { detail: "인증이 필요합니다" }),
  );
  await page.route(/\/api\/meetings\/41$/, (route) => json(route, 200, detail(mocks.initial)));
  await page.route(/\/api\/meetings\/41\/transcript$/, (route) => json(route, 404, { detail: "전사문이 없습니다" }));
  await page.route(/\/api\/meetings\/41\/speakers$/, (route) => json(route, 200, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route("**/api/accounts", (route) => (mocks.accounts ?? ((r: Route) => json(r, 200, ACCOUNTS)))(route) as Promise<void>);
  await page.route(/\/api\/action-items\/103$/, (route) => mocks.patch(route) as Promise<void>);
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto("/v2/meetings/41");
  await expect(page.getByRole("heading", { level: 1, name: "주간 생산 현안 회의" })).toBeVisible();
}

const ledger = (page: Page) => page.getByRole("table", { name: "업무 원장" });

test.describe("v2 업무 담당자 지정", () => {
  test("지정 성공 → PATCH assigneeId 전송, 원장 담당자 갱신·담당자 필요 사라짐", async ({ page }) => {
    const bodies: unknown[] = [];
    await openDetail(page, {
      initial: item(null),
      patch: (route) => {
        expect(route.request().method()).toBe("PATCH");
        bodies.push(route.request().postDataJSON());
        return json(route, 200, item({ id: 2, name: "최임원" }));
      },
    });
    await expect(ledger(page).getByText("담당자 필요")).toBeVisible();

    await page.getByRole("button", { name: "정리 자료 공유 담당자 지정" }).click();
    const dialog = page.getByRole("dialog", { name: "담당자 지정" });
    await expect(dialog.getByText("현재 담당자 없음")).toBeVisible();
    const save = dialog.getByRole("button", { name: "담당자 저장" });
    await expect(save).toBeDisabled();
    await dialog.getByLabel("담당자").selectOption("2");
    await save.click();

    await expect(dialog).toBeHidden();
    expect(bodies).toEqual([{ assigneeId: 2 }]);
    await expect(ledger(page).getByRole("cell", { name: /최임원/ })).toBeVisible();
    await expect(ledger(page).getByText("담당자 필요")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "정리 자료 공유 담당자 변경" })).toBeVisible();
  });

  test("변경 → 현재 담당자 선택돼 있고, 다른 계정으로 바꾸면 원장 갱신", async ({ page }) => {
    const bodies: unknown[] = [];
    await openDetail(page, {
      initial: item({ id: 2, name: "최임원" }, "confirmed"),
      patch: (route) => {
        bodies.push(route.request().postDataJSON());
        return json(route, 200, item({ id: 9, name: "이서연" }, "confirmed"));
      },
    });
    await page.getByRole("button", { name: "정리 자료 공유 담당자 변경" }).click();
    const dialog = page.getByRole("dialog", { name: "담당자 변경" });
    const select = dialog.getByLabel("담당자");
    await expect(select).toHaveValue("2");
    // 같은 사람 그대로면 저장할 수 없음
    await expect(dialog.getByRole("button", { name: "담당자 저장" })).toBeDisabled();
    await select.selectOption("9");
    await dialog.getByRole("button", { name: "담당자 저장" }).click();

    await expect(dialog).toBeHidden();
    expect(bodies).toEqual([{ assigneeId: 9 }]);
    await expect(ledger(page).getByRole("cell", { name: /이서연/ })).toBeVisible();
  });

  test("403 → 서버 문구 표시, 선택값 유지, 원장 그대로", async ({ page }) => {
    await openDetail(page, {
      initial: item(null),
      patch: (route) => json(route, 403, { detail: "이 업무를 수정·확정할 권한이 없습니다" }),
    });
    await page.getByRole("button", { name: "정리 자료 공유 담당자 지정" }).click();
    const dialog = page.getByRole("dialog", { name: "담당자 지정" });
    await dialog.getByLabel("담당자").selectOption("9");
    await dialog.getByRole("button", { name: "담당자 저장" }).click();

    await expect(dialog.getByRole("alert")).toContainText("이 업무를 수정·확정할 권한이 없습니다");
    await expect(dialog.getByLabel("담당자")).toHaveValue("9");
    await dialog.getByRole("button", { name: "취소" }).click();
    await expect(dialog).toBeHidden();
    await expect(ledger(page).getByText("담당자 필요")).toBeVisible();
  });

  test("400 → 서버 문구 표시(확정 업무 빈칸 형식 포함), 선택값 유지", async ({ page }) => {
    let calls = 0;
    await openDetail(page, {
      initial: item({ id: 2, name: "최임원" }, "confirmed"),
      patch: (route) => {
        calls += 1;
        return calls === 1
          ? json(route, 400, { detail: "같은 고객사의 활성 계정만 담당자로 지정할 수 있습니다" })
          : json(route, 400, { detail: { message: "확정된 업무는 필수 항목을 비울 수 없습니다", missingFields: ["assignee"] } });
      },
    });
    await page.getByRole("button", { name: "정리 자료 공유 담당자 변경" }).click();
    const dialog = page.getByRole("dialog", { name: "담당자 변경" });
    await dialog.getByLabel("담당자").selectOption("9");
    await dialog.getByRole("button", { name: "담당자 저장" }).click();
    await expect(dialog.getByRole("alert")).toContainText("같은 고객사의 활성 계정만 담당자로 지정할 수 있습니다");
    await expect(dialog.getByLabel("담당자")).toHaveValue("9");

    await dialog.getByRole("button", { name: "담당자 저장" }).click();
    await expect(dialog.getByRole("alert")).toContainText("확정된 업무는 필수 항목을 비울 수 없습니다");
    await expect(dialog.getByLabel("담당자")).toHaveValue("9");
    await expect(ledger(page).getByRole("cell", { name: /최임원/ })).toBeVisible();
  });

  test("계정 목록 오류 → 오류·다시 시도 표시, 다시 시도로 목록 표시", async ({ page }) => {
    let fail = true;
    await openDetail(page, {
      initial: item(null),
      patch: (route) => json(route, 500, { detail: "없음" }),
      accounts: (route) => (fail ? json(route, 500, { detail: "계정 목록 서버 오류" }) : json(route, 200, ACCOUNTS)),
    });
    await page.getByRole("button", { name: "정리 자료 공유 담당자 지정" }).click();
    const dialog = page.getByRole("dialog", { name: "담당자 지정" });
    await expect(dialog.getByRole("alert")).toContainText("계정 목록을 불러오지 못했습니다 · 계정 목록 서버 오류");
    await expect(dialog.getByRole("button", { name: "담당자 저장" })).toBeDisabled();

    fail = false;
    await dialog.getByRole("button", { name: "다시 시도" }).click();
    await expect(dialog.getByLabel("담당자")).toBeVisible();
    await expect(dialog.getByRole("alert")).toHaveCount(0);
  });
});
