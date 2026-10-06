/*
 * v2 회의록 상세 · 화자 지정 팝업 E2E(참석자 줄의 "화자 지정" 버튼). 실제 백엔드 없이 page.route 로 v2 API 를 가로채 가짜로 응답한다.
 * (/api/auth/me, /api/meetings/{id}, /transcript, /speakers, /api/accounts) 데이터는 모두 가상이다.
 */
import { expect, test, type Page, type Route } from "@playwright/test";
import { withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const ACCOUNT = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const ACCOUNTS = [
  { id: 2, name: "최임원", rank: "executive" },
  { id: 9, name: "이서연", rank: "staff" },
];

const LABELS = ["화자1", "화자2"];
const RAW_TEXT = "[00:00:03] 화자1: 이번 주까지 정리해 주세요.\n[00:00:10] 화자2: 네, 제가 하겠습니다.";

const detail = (assignee: { id: number; name: string } | null) => ({
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
  actionItems: [
    {
      id: 103, title: "정리 자료 공유", assignee, dueDate: null, dueUndetermined: true, status: "pending",
      confirmKind: null, evidenceStartSec: 10, evidenceQuote: null,
      needsCompletion: assignee === null, missingFields: assignee === null ? ["assignee"] : [],
    },
  ],
  recentEvents: [],
});

const mapping = (label: string, target: { accountId: number; accountName: string } | { name: string }) =>
  "accountId" in target
    ? { label, accountId: target.accountId, accountName: target.accountName, name: null, displayName: target.accountName, unregistered: false }
    : { label, accountId: null, accountName: null, name: target.name, displayName: `${target.name}(미등록)`, unregistered: true };

interface Mocks {
  speakersGet?: (route: Route) => unknown;
  speakersPut?: (route: Route) => unknown;
  accounts?: (route: Route) => unknown;
  detail?: () => unknown;
  transcript?: () => unknown;
}

async function openDetail(page: Page, mocks: Mocks) {
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}`
      ? json(route, 200, ACCOUNT)
      : json(route, 401, { detail: "인증이 필요합니다" }),
  );
  await page.route(/\/api\/meetings\/41$/, (route) => json(route, 200, withAllowed((mocks.detail ?? (() => detail(null)))() as never, ACCOUNT)));
  await page.route(/\/api\/meetings\/41\/transcript$/, (route) =>
    json(route, 200, (mocks.transcript ?? (() => ({ fullText: RAW_TEXT, segments: null, sttProvider: "fake", displayText: RAW_TEXT, speakers: [] })))()),
  );
  await page.route(/\/api\/meetings\/41\/speakers$/, (route) => {
    if (route.request().method() === "PUT") return (mocks.speakersPut ?? ((r: Route) => json(r, 500, { detail: "없음" })))(route) as Promise<void>;
    return (mocks.speakersGet ?? ((r: Route) => json(r, 200, { labels: LABELS, speakers: [], autoAssignedItemIds: [] })))(route) as Promise<void>;
  });
  await page.route(/\/api\/meetings\/41\/change-requests$/, (route) => json(route, 200, []));
  await page.route("**/api/accounts", (route) => (mocks.accounts ?? ((r: Route) => json(r, 200, ACCOUNTS)))(route) as Promise<void>);
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto("/v2/meetings/41");
  await expect(page.getByRole("heading", { level: 1, name: "주간 생산 현안 회의" })).toBeVisible();
}

const panel = (page: Page) => page.getByRole("dialog", { name: "화자 지정" });
const openButton = (page: Page) => page.getByRole("button", { name: "화자 지정" });
const speakerSelect = (page: Page, label: string) => panel(page).getByLabel(label, { exact: true });
const nameInput = (page: Page, label: string) => panel(page).getByLabel(`${label} 미등록 이름`);
const saveButton = (page: Page) => panel(page).getByRole("button", { name: "화자 저장" });
const ledgerRow = (page: Page) => page.getByRole("table", { name: "업무 원장" }).locator("tbody tr").first();
const transcriptRegion = (page: Page) => page.getByRole("region", { name: "전사문" });
const summary = (page: Page) => page.getByLabel("지정된 화자");

/** "화자 지정" 팝업을 열고 화자 정보가 다 불러와질 때까지 기다린다 */
async function openSpeakers(page: Page) {
  await openButton(page).click();
  await expect(panel(page)).toBeVisible();
  await expect(panel(page).getByText("화자 정보를 불러오는 중…")).toHaveCount(0);
}

test.describe("v2 화자 지정 팝업", () => {
  test("저장 성공 → 팝업 닫힘, 전사문(displayText)·업무 원장·화자 요약 갱신, 요청 본문은 계정/이름 하나씩", async ({ page }) => {
    let saved = false;
    let putBody: unknown = null;
    await openDetail(page, {
      detail: () => detail(saved ? { id: 9, name: "이서연" } : null),
      transcript: () =>
        saved
          ? {
              fullText: RAW_TEXT, segments: null, sttProvider: "fake",
              displayText: "[00:00:03] 협력사 홍길동(미등록): 이번 주까지 정리해 주세요.\n[00:00:10] 이서연: 네, 제가 하겠습니다.",
              speakers: [mapping("화자1", { name: "협력사 홍길동" }), mapping("화자2", { accountId: 9, accountName: "이서연" })],
            }
          : { fullText: RAW_TEXT, segments: null, sttProvider: "fake", displayText: RAW_TEXT, speakers: [] },
      speakersPut: (route) => {
        putBody = route.request().postDataJSON();
        saved = true;
        return json(route, 200, {
          labels: LABELS,
          speakers: [mapping("화자1", { name: "협력사 홍길동" }), mapping("화자2", { accountId: 9, accountName: "이서연" })],
          autoAssignedItemIds: [103],
        });
      },
    });

    // 본문에는 화자 확정 영역이 없고, 지정된 화자가 없으면 요약도 없다
    await expect(page.getByRole("region", { name: "화자 확정" })).toHaveCount(0);
    await expect(summary(page)).toHaveCount(0);
    // 전사문을 먼저 펼쳐 원래 표기를 확인
    await page.getByRole("button", { name: /전사문/ }).click();
    await expect(transcriptRegion(page)).toContainText("화자2: 네, 제가 하겠습니다.");
    await expect(ledgerRow(page)).toContainText("보완 필요");

    await openSpeakers(page);
    await expect(speakerSelect(page, "화자1")).toHaveValue("none");
    await speakerSelect(page, "화자1").selectOption({ label: "계정 없음 · 이름 직접 입력" });
    await nameInput(page, "화자1").fill("협력사 홍길동");
    await speakerSelect(page, "화자2").selectOption({ label: "이서연 · 담당자" });
    await saveButton(page).click();

    await expect(panel(page)).toBeHidden();
    await expect(openButton(page)).toBeFocused(); // 연 버튼으로 포커스 복귀
    await expect(page.getByText("화자를 저장했습니다 · 담당자 자동 지정 1건")).toBeVisible();
    expect(putBody).toEqual({ speakers: [{ label: "화자1", name: "협력사 홍길동" }, { label: "화자2", accountId: 9 }] });
    await expect(summary(page)).toHaveText("화자 협력사 홍길동(미등록), 이서연");
    // 업무 원장: 새로 조회한 담당자
    await expect(ledgerRow(page)).toContainText("이서연");
    await expect(ledgerRow(page)).not.toContainText("보완 필요");
    // 전사문: displayText 로 갱신
    await expect(transcriptRegion(page)).toContainText("이서연: 네, 제가 하겠습니다.");
    await expect(transcriptRegion(page)).toContainText("협력사 홍길동(미등록): 이번 주까지");
  });

  test("팝업 열림(취소에 먼저 포커스, 스크롤 고정) + 기존 매핑 불러오기 + 미등록 이름은 \"(미등록)\" 표시", async ({ page }) => {
    const speakers = [mapping("화자1", { name: "외부 김자문" }), mapping("화자2", { accountId: 2, accountName: "최임원" })];
    await openDetail(page, {
      speakersGet: (route) => json(route, 200, { labels: LABELS, speakers, autoAssignedItemIds: [] }),
      transcript: () => ({
        fullText: RAW_TEXT, segments: null, sttProvider: "fake", speakers,
        displayText: "[00:00:03] 외부 김자문(미등록): 이번 주까지 정리해 주세요.\n[00:00:10] 최임원: 네, 제가 하겠습니다.",
      }),
    });
    // 참석자 줄의 화자 요약
    await expect(summary(page)).toHaveText("화자 외부 김자문(미등록), 최임원");

    const scrollBefore = await page.evaluate(() => window.scrollY);
    await openSpeakers(page);
    await expect(panel(page).getByRole("button", { name: "취소" })).toBeFocused();
    expect(await page.evaluate(() => window.scrollY)).toBe(scrollBefore);
    await expect(speakerSelect(page, "화자1")).toHaveValue("name");
    await expect(nameInput(page, "화자1")).toHaveValue("외부 김자문");
    await expect(nameInput(page, "화자1")).toHaveAttribute("maxlength", "30");
    await expect(panel(page)).toContainText("(미등록)");
    await expect(speakerSelect(page, "화자2")).toHaveValue("account:2");
    await panel(page).getByRole("button", { name: "취소" }).click();
    await expect(panel(page)).toBeHidden();

    await page.getByRole("button", { name: /전사문/ }).click();
    await expect(transcriptRegion(page)).toContainText("외부 김자문(미등록): 이번 주까지");
    await expect(transcriptRegion(page)).toContainText("최임원: 네, 제가 하겠습니다.");
  });

  test("Esc·배경 클릭으로 닫힘, 저장 요청 없음, 포커스 복귀, 다시 열면 저장된 값으로", async ({ page }) => {
    let puts = 0;
    await openDetail(page, {
      speakersPut: (route) => {
        puts += 1;
        return json(route, 500, { detail: "없음" });
      },
    });
    await openSpeakers(page);
    await speakerSelect(page, "화자1").selectOption({ label: "최임원 · 지시자" });
    await page.keyboard.press("Escape");
    await expect(panel(page)).toBeHidden();
    await expect(openButton(page)).toBeFocused();

    await openSpeakers(page);
    await expect(speakerSelect(page, "화자1")).toHaveValue("none"); // 저장하지 않은 선택은 남지 않는다
    await page.mouse.click(5, 5); // 배경
    await expect(panel(page)).toBeHidden();
    await expect(openButton(page)).toBeFocused();
    expect(puts).toBe(0);
  });

  test("400 오류 → 팝업 안 서버 문구 표시, 입력값 유지", async ({ page }) => {
    await openDetail(page, {
      speakersPut: (route) => json(route, 400, { detail: "같은 고객사의 활성 계정만 지정할 수 있습니다" }),
    });
    await openSpeakers(page);
    await speakerSelect(page, "화자1").selectOption({ label: "계정 없음 · 이름 직접 입력" });
    await nameInput(page, "화자1").fill("홍길동");
    await speakerSelect(page, "화자2").selectOption({ label: "최임원 · 지시자" });
    await saveButton(page).click();
    await expect(panel(page).getByRole("alert")).toContainText("같은 고객사의 활성 계정만 지정할 수 있습니다");
    await expect(nameInput(page, "화자1")).toHaveValue("홍길동");
    await expect(speakerSelect(page, "화자2")).toHaveValue("account:2");
    await expect(saveButton(page)).toBeEnabled();
  });

  test("403 오류(저장) → 팝업 안 서버 문구 표시, 입력값 유지, 팝업 열린 채", async ({ page }) => {
    await openDetail(page, {
      speakersPut: (route) => json(route, 403, { detail: "권한이 없습니다" }),
    });
    await openSpeakers(page);
    await speakerSelect(page, "화자2").selectOption({ label: "이서연 · 담당자" });
    await saveButton(page).click();
    await expect(panel(page).getByRole("alert")).toContainText("권한이 없습니다");
    await expect(speakerSelect(page, "화자2")).toHaveValue("account:9");
    await expect(panel(page)).toBeVisible();
  });

  test("조회 403 → 팝업에 권한 없음 안내만, 입력란 없음·저장 불가, 요약 없음", async ({ page }) => {
    await openDetail(page, { speakersGet: (route) => json(route, 403, { detail: "권한이 없습니다" }) });
    await expect(summary(page)).toHaveCount(0);
    await openSpeakers(page);
    await expect(panel(page)).toContainText("화자를 지정할 권한이 없습니다");
    await expect(panel(page).getByRole("combobox")).toHaveCount(0);
    await expect(saveButton(page)).toBeDisabled();
  });

  test("계정 목록 오류 → 오류·다시 시도, 이름 직접 입력은 저장 가능", async ({ page }) => {
    let putBody: unknown = null;
    await openDetail(page, {
      accounts: (route) => json(route, 500, { detail: "서버 오류" }),
      speakersPut: (route) => {
        putBody = route.request().postDataJSON();
        return json(route, 200, { labels: LABELS, speakers: [mapping("화자1", { name: "홍길동" })], autoAssignedItemIds: [] });
      },
    });
    await openSpeakers(page);
    const alert = panel(page).getByRole("alert").filter({ hasText: "계정 목록을 불러오지 못했습니다" });
    await expect(alert).toContainText("서버 오류");
    await expect(alert.getByRole("button", { name: "계정 목록 다시 불러오기" })).toBeVisible();
    await speakerSelect(page, "화자1").selectOption({ label: "계정 없음 · 이름 직접 입력" });
    await nameInput(page, "화자1").fill("홍길동");
    await saveButton(page).click();
    await expect(panel(page)).toBeHidden();
    await expect(page.getByText("화자를 저장했습니다", { exact: true })).toBeVisible();
    await expect(summary(page)).toHaveText("화자 홍길동(미등록)");
    expect(putBody).toEqual({ speakers: [{ label: "화자1", name: "홍길동" }] });
  });
});
