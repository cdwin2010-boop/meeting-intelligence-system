/*
 * v2 회의록 상세 · 전사문 원본 보기 E2E. 실제 백엔드 없이 page.route 로 v2 API 를 가로채 가짜로 응답한다.
 * (/api/auth/me, /api/meetings/{id}, /transcript, /speakers, /change-requests, /api/accounts) 데이터는 모두 가상이다.
 * fullText·segments 는 원본, displayText·speakers 는 이름 적용본.
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
  await page.route(/\/api\/meetings\/41$/, (route) => json(route, 200, (mocks.detail ?? (() => detail(null)))()));
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

const transcriptRegion = (page: Page) => page.getByRole("region", { name: "전사문" });
const openTranscript = (page: Page) => page.getByRole("button", { name: /전사문/ }).click();

const APPLIED_TEXT = "[00:00:03] 외부 김자문(미등록): 이번 주까지 정리해 주세요.\n[00:00:10] 최임원: 네, 제가 하겠습니다.";
const SPEAKERS = [mapping("화자1", { name: "외부 김자문" }), mapping("화자2", { accountId: 2, accountName: "최임원" })];

test.describe("v2 전사문 원본 보기", () => {
  test("지정된 화자 없음 → 원본만 표시, 전환 버튼 없음", async ({ page }) => {
    await openDetail(page, {});
    await openTranscript(page);
    const region = transcriptRegion(page);
    await expect(region).toContainText("화자2: 네, 제가 하겠습니다.");
    await expect(region).toContainText("현재 보기 원본");
    await expect(region.getByRole("button", { name: "원본 보기" })).toHaveCount(0);
    await expect(region.getByRole("button", { name: "이름 적용본 보기" })).toHaveCount(0);
  });

  test("지정된 화자 있음 → 기본 이름 적용본, 원본 보기 ↔ 이름 적용본 보기 전환", async ({ page }) => {
    await openDetail(page, {
      speakersGet: (route) => json(route, 200, { labels: LABELS, speakers: SPEAKERS, autoAssignedItemIds: [] }),
      transcript: () => ({ fullText: RAW_TEXT, segments: null, sttProvider: "fake", displayText: APPLIED_TEXT, speakers: SPEAKERS }),
    });
    await openTranscript(page);
    const region = transcriptRegion(page);
    await expect(region).toContainText("현재 보기 이름 적용본");
    await expect(region).toContainText("최임원: 네, 제가 하겠습니다.");

    await region.getByRole("button", { name: "원본 보기" }).click();
    await expect(region).toContainText("현재 보기 원본");
    await expect(region).toContainText("화자2: 네, 제가 하겠습니다.");
    await expect(region).not.toContainText("최임원");

    await region.getByRole("button", { name: "이름 적용본 보기" }).click();
    await expect(region).toContainText("현재 보기 이름 적용본");
    await expect(region).toContainText("최임원: 네, 제가 하겠습니다.");
    await expect(region.getByRole("button", { name: "원본 보기" })).toBeVisible();
  });

  test("구간(segments) 전사문도 원본은 화자 표기, 적용본은 표시 이름", async ({ page }) => {
    const segments = [
      { speaker: "화자1", start_sec: 3, end_sec: 8, text: "이번 주까지 정리해 주세요." },
      { speaker: "화자2", start_sec: 10, end_sec: 12, text: "네, 제가 하겠습니다." },
    ];
    await openDetail(page, {
      speakersGet: (route) => json(route, 200, { labels: LABELS, speakers: SPEAKERS, autoAssignedItemIds: [] }),
      transcript: () => ({ fullText: RAW_TEXT, segments, sttProvider: "fake", displayText: APPLIED_TEXT, speakers: SPEAKERS }),
    });
    await openTranscript(page);
    const region = transcriptRegion(page);
    await expect(region).toContainText("외부 김자문(미등록)");
    await region.getByRole("button", { name: "원본 보기" }).click();
    await expect(region).toContainText("화자1");
    await expect(region).not.toContainText("외부 김자문");
  });

  test("원본 보기 중 화자 저장 → 이름 적용본으로 갱신·복귀", async ({ page }) => {
    let speakers: unknown[] = [mapping("화자2", { accountId: 2, accountName: "최임원" })];
    let displayText = "[00:00:03] 화자1: 이번 주까지 정리해 주세요.\n[00:00:10] 최임원: 네, 제가 하겠습니다.";
    await openDetail(page, {
      speakersGet: (route) => json(route, 200, { labels: LABELS, speakers, autoAssignedItemIds: [] }),
      transcript: () => ({ fullText: RAW_TEXT, segments: null, sttProvider: "fake", displayText, speakers }),
      speakersPut: (route) => {
        speakers = SPEAKERS;
        displayText = APPLIED_TEXT;
        return json(route, 200, { labels: LABELS, speakers: SPEAKERS, autoAssignedItemIds: [] });
      },
    });
    await openTranscript(page);
    const region = transcriptRegion(page);
    await region.getByRole("button", { name: "원본 보기" }).click();
    await expect(region).toContainText("현재 보기 원본");

    await page.getByRole("button", { name: "화자 지정" }).click();
    const speakerPanel = page.getByRole("dialog", { name: "화자 지정" });
    await speakerPanel.getByLabel("화자1", { exact: true }).selectOption({ label: "계정 없음 · 이름 직접 입력" });
    await speakerPanel.getByLabel("화자1 미등록 이름").fill("외부 김자문");
    await speakerPanel.getByRole("button", { name: "화자 저장" }).click();

    await expect(region).toContainText("현재 보기 이름 적용본");
    await expect(region).toContainText("외부 김자문(미등록): 이번 주까지");
    await expect(region.getByRole("button", { name: "원본 보기" })).toBeVisible();
  });
});
