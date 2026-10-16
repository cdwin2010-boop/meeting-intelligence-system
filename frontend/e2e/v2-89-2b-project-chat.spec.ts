/*
 * v2 프로젝트 상세 · 질의응답 서랍 E2E (작업 89-2b, 계약 docs/contracts/project-chat-v1.json).
 * 실제 백엔드 없이 page.route 로 프로젝트·chat(SSE)·회의록 상세를 가로챈다. 데이터는 모두 가상이다.
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withAllowed } from "./helpers/allowed-actions";
import { withProjectActions } from "./helpers/project-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const KIM = { id: 8, name: "김담당", rank: "staff", tenantId: 1 };
const FIXED = "본 회의록에서 확인할 수 없는 내용입니다";
const json = (route: Route, body: unknown, status = 200, headers: Record<string, string> = {}) =>
  route.fulfill({ status, contentType: "application/json", headers, body: JSON.stringify(body) });

const PROJECT = {
  id: 5, name: "설비 개선 과제", description: "", status: "active", departmentId: 1, departmentName: "개발팀", registeredBy: { id: 8, name: "김담당" },
  lead: null, approver: null, myRole: "member", memberCount: 1, createdAt: "2026-10-01T01:00:00Z", decidedAt: null,
  members: [{ accountId: 8, name: "김담당", rank: "staff", role: "member" }],
};
const MEETING = {
  id: 41, title: "1차 킥오프", heldAt: "2026-10-01T01:00:00Z", summary: "", decisions: [], status: "confirmed", confirmKind: "manager", confirmedBy: null,
  confirmedAt: null, firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: null, registeredBy: { id: 7, name: "한팀장" }, origin: "audio_minutes",
  participants: [], recentEvents: [], phase: "active", hasAudio: false, actionItems: [],
};
const TRANSCRIPT = {
  fullText: "x", sttProvider: "fake", speakers: [],
  segments: [
    { speaker: "화자1", start_sec: 3, end_sec: 6, text: "안녕하세요" },
    { speaker: "화자2", start_sec: 75, end_sec: 80, text: "견적서는 금요일까지 보내겠습니다" },
  ],
};

const sse = (events: [string, unknown][]) => events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join("");
const META = { requestId: "abc123def456", contextMode: "summary", model: "stub", confirmedPendingIncluded: false };
const CITES = [
  { refId: "S1", utteranceId: 1, speaker: "화자2", startSec: 75, label: "1차 킥오프·화자2/01:15", meetingId: 41, meetingTitle: "1차 킥오프" },
  { refId: "S2", utteranceId: null, speaker: "", startSec: null, label: "2차 점검·회의록·결정사항", meetingId: 42, meetingTitle: "2차 점검" },
];
function answerBody(opts: { items?: unknown[]; pending?: boolean; noAnswer?: boolean } = {}) {
  const noAnswer = opts.noAnswer === true;
  return sse([
    ["meta", { ...META, confirmedPendingIncluded: opts.pending === true }],
    ["delta", { text: noAnswer ? FIXED : "견적서는 금요일까지입니다. [1차 킥오프·화자2/01:15]" }],
    ["citations", { items: noAnswer ? [] : (opts.items ?? CITES) }],
    ["done", { grounded: !noAnswer, noAnswer, elapsedMs: 12 }],
  ]);
}

interface Chat {
  bodies: { question: string; history: unknown[]; quickPrompt: string | null }[];
  meetingChatCalls: number;
}
type Handler = (route: Route, body: Chat["bodies"][number]) => Promise<void> | void;

async function openProject(page: Page, handler: Handler, options: { askable?: boolean } = {}): Promise<Chat> {
  const chat: Chat = { bodies: [], meetingChatCalls: 0 };
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, KIM) : json(route, { detail: "x" }, 401),
  );
  await page.route(/\/api\/projects\/5$/, (route) => json(route, withProjectActions(PROJECT, KIM, null, options.askable !== false)));
  await page.route(/\/api\/projects\/5\/chat$/, async (route) => {
    const body = route.request().postDataJSON();
    chat.bodies.push(body);
    await handler(route, body);
  });
  await page.route(/\/api\/meetings\/\d+\/chat$/, (route) => {
    chat.meetingChatCalls += 1;
    return json(route, { detail: "x" }, 500);
  });
  await page.route(/\/api\/meetings\/41$/, (route) => json(route, withAllowed(MEETING, KIM)));
  await page.route(/\/api\/meetings\/41\/transcript$/, (route) => json(route, TRANSCRIPT));
  await page.route(/\/api\/meetings\/41\/speakers$/, (route) => json(route, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route(/\/api\/meetings\/41\/change-requests$/, (route) => json(route, []));
  await page.route("**/api/accounts", (route) => json(route, []));
  await page.goto("/v2/login");
  await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto("/v2/projects/5");
  await expect(page.getByRole("heading", { level: 1, name: "설비 개선 과제" })).toBeVisible();
  return chat;
}

const ok = (opts?: Parameters<typeof answerBody>[0]): Handler => (route) =>
  route.fulfill({ status: 200, contentType: "text/event-stream", body: answerBody(opts) });
const opener = (page: Page) => page.getByRole("button", { name: "질문하기" });
const panel = (page: Page) => page.getByRole("dialog", { name: "프로젝트 질의응답" });
const input = (page: Page) => panel(page).getByRole("textbox", { name: "질문" });
async function ask(page: Page, text: string) {
  await input(page).fill(text);
  await panel(page).getByRole("button", { name: "보내기" }).click();
}

test.describe("v2 프로젝트 질의응답 서랍", () => {
  test("ask_project 가 있으면 버튼이 보이고, 열면 프로젝트명 범위·안내가 질문창 위에 있다", async ({ page }) => {
    await openProject(page, ok());
    await opener(page).click();
    await expect(panel(page)).toBeVisible();
    await expect(panel(page).getByText("범위: 프로젝트 설비 개선 과제")).toBeVisible();
    await expect(panel(page).getByText("이 프로젝트에 속한 회의록 안의 내용만 답변합니다.", { exact: false })).toBeVisible();
    await expect(input(page)).toBeFocused();
    const box = (await panel(page).boundingBox())!;
    expect(box.x + box.width).toBeGreaterThan(1270); // PC: 오른쪽 서랍
    await page.keyboard.press("Escape");
    await expect(panel(page)).toBeHidden();
    await expect(opener(page)).toBeFocused();
  });

  test("ask_project 가 없으면 버튼이 없다(화면이 직급을 다시 판단하지 않는다)", async ({ page }) => {
    await openProject(page, ok(), { askable: false });
    await expect(opener(page)).toHaveCount(0);
  });

  test("칩 3개는 프로젝트 chat 으로 quickPrompt 전송(회의록 chat 은 호출하지 않는다)", async ({ page }) => {
    const chat = await openProject(page, ok());
    await opener(page).click();
    const chips = panel(page).getByRole("list", { name: "추천 질문" }).getByRole("button");
    await expect(chips).toHaveText(["결정사항", "내 업무", "일정"]);
    for (let i = 0; i < 3; i += 1) {
      await chips.nth(i).click();
      await expect(panel(page).getByTestId("chat-answer")).toHaveCount(i + 1);
    }
    expect(chat.bodies.map((b) => b.quickPrompt)).toEqual(["decisions", "my_tasks", "schedule"]);
    expect(chat.meetingChatCalls).toBe(0);
  });

  test("\"근거 확인 중\" 표시 뒤 답변과 회의록 제목이 붙은 근거, 확정 전 내용 배지", async ({ page }) => {
    await openProject(page, async (route) => {
      await new Promise((r) => setTimeout(r, 600));
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: answerBody({ pending: true }) });
    });
    await opener(page).click();
    await ask(page, "견적서 일정은?");
    await expect(panel(page).getByTestId("chat-pending")).toContainText("근거 확인 중");
    await expect(input(page)).toBeDisabled();
    await expect(panel(page).getByTestId("chat-answer")).toHaveCount(0);
    const answer = panel(page).getByTestId("chat-answer");
    await expect(answer).toContainText("견적서는 금요일까지입니다.");
    await expect(answer.getByTestId("chat-citation")).toHaveText(["1차 킥오프·화자2/01:15", "2차 점검·회의록·결정사항"]);
    await expect(panel(page).getByTestId("chat-pending-badge")).toBeVisible();
  });

  test("근거는 해당 회의록으로 이동하는 링크이고, 시각이 있으면 전사문의 그 위치가 열린다", async ({ page }) => {
    await openProject(page, ok());
    await opener(page).click();
    await ask(page, "질문");
    const links = panel(page).getByTestId("chat-citation");
    await expect(links.nth(0)).toHaveAttribute("href", "/v2/meetings/41?evidence=75");
    await expect(links.nth(1)).toHaveAttribute("href", "/v2/meetings/42"); // 시각 없는 요약 근거는 회의록만 연다
    await links.nth(0).click();
    await expect(page).toHaveURL(/\/v2\/meetings\/41\?evidence=75$/);
    await expect(page.getByRole("heading", { level: 1, name: "1차 킥오프" })).toBeVisible();
    await expect(page.locator('[data-segment-index="1"]').getByText("근거 위치")).toBeAttached(); // 75초 발화로 이동·표시
    await expect(page.getByTestId("evidence-approx-notice")).toHaveCount(0);
  });

  test("noAnswer 는 고정 문구만, 근거 목록 없음", async ({ page }) => {
    await openProject(page, ok({ noAnswer: true }));
    await opener(page).click();
    await ask(page, "프로젝트와 무관한 질문");
    await expect(panel(page).getByTestId("chat-answer")).toHaveText(FIXED);
    await expect(panel(page).getByRole("list", { name: "근거 목록" })).toHaveCount(0);
  });

  const ERRORS: { name: string; handler: Handler; cause: string; action: string }[] = [
    {
      name: "503 gpu_busy(Retry-After)",
      handler: (route) => json(route, { detail: { code: "gpu_busy", message: "x" } }, 503, { "Retry-After": "9", "Access-Control-Expose-Headers": "Retry-After" }),
      cause: "음성 변환이 진행 중입니다.", action: "잠시 후 다시 시도해 주세요. (약 9초 후)",
    },
    {
      name: "503 llm_unavailable(Gemini 모드 포함)",
      handler: (route) => json(route, { detail: { code: "llm_unavailable", message: "x" } }, 503),
      cause: "답변 엔진을 사용할 수 없습니다.", action: "관리자에게 문의하세요.",
    },
    { name: "연결 끊김", handler: (route) => route.abort("connectionreset"), cause: "응답이 지연되고 있습니다.", action: "다시 시도해 주세요." },
    {
      name: "스트림 error(시간 초과)",
      handler: (route) => route.fulfill({ status: 200, contentType: "text/event-stream", body: sse([["meta", META], ["error", { code: "llm_timeout", message: "x" }]]) }),
      cause: "응답이 지연되고 있습니다.", action: "다시 시도해 주세요.",
    },
    { name: "409", handler: (route) => json(route, { detail: "서버 문구" }, 409), cause: "정리된 회의록이 아직 없습니다.", action: "처리가 끝난 뒤 다시 질문해 주세요." },
    {
      name: "422",
      handler: (route) => json(route, { detail: "질문은 1~500자여야 합니다" }, 422),
      cause: "질문은 1~500자여야 합니다", action: "질문을 확인한 뒤 다시 보내 주세요.",
    },
    { name: "429", handler: (route) => json(route, { detail: "x" }, 429, { "Retry-After": "30" }), cause: "질문이 너무 잦습니다.", action: "잠시 후 다시 시도해 주세요." },
    {
      name: "404(프로젝트)",
      handler: (route) => json(route, { detail: "프로젝트를 찾을 수 없습니다" }, 404),
      cause: "프로젝트를 찾을 수 없습니다", action: "프로젝트 목록에서 다시 열어 주세요.",
    },
  ];
  for (const c of ERRORS) {
    test(`오류 안내(원인+조치): ${c.name}`, async ({ page }) => {
      await openProject(page, c.handler);
      await opener(page).click();
      await ask(page, "오류 시험 질문");
      const error = panel(page).getByTestId("chat-error");
      await expect(error.locator("span").nth(0)).toHaveText(c.cause);
      await expect(error.locator("span").nth(1)).toHaveText(c.action);
      await expect(input(page)).toHaveValue("오류 시험 질문");
      await expect(error).not.toContainText("오류 시험 질문");
    });
  }

  test("500자 초과는 전송 전에 막고, 취소하면 요청을 중단한다", async ({ page }) => {
    const chat = await openProject(page, async (route) => {
      await new Promise((r) => setTimeout(r, 4000));
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: answerBody() }).catch(() => undefined);
    });
    await opener(page).click();
    await input(page).fill("가".repeat(501));
    await expect(panel(page).getByTestId("chat-count")).toContainText("질문은 500자까지 입력할 수 있습니다");
    await expect(panel(page).getByRole("button", { name: "보내기" })).toBeDisabled();
    expect(chat.bodies).toHaveLength(0);
    await input(page).fill("오래 걸리는 질문");
    const failed = page.waitForEvent("requestfailed", (r) => r.url().includes("/chat"));
    await panel(page).getByRole("button", { name: "보내기" }).click();
    await panel(page).getByRole("button", { name: "취소" }).click();
    expect((await failed).failure()?.errorText).toMatch(/ABORTED|abort/i);
    await expect(panel(page).getByText("질문을 취소했습니다.")).toBeVisible();
    await expect(input(page)).toHaveValue("오래 걸리는 질문");
  });

  test("모바일 폭: 바텀시트", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 800 });
    await openProject(page, ok());
    await opener(page).click();
    const box = (await panel(page).boundingBox())!;
    expect(box.width).toBeGreaterThan(380);
    expect(box.y + box.height).toBeGreaterThan(790);
    expect(box.y).toBeGreaterThan(100);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });

  test("저장 안 함·새로고침 시 대화 소멸·\"내 업무로 등록\" 버튼 없음", async ({ page }) => {
    const logs: string[] = [];
    page.on("console", (m) => logs.push(m.text()));
    await openProject(page, ok());
    await opener(page).click();
    await ask(page, "비밀스러운프로젝트질문");
    await expect(panel(page).getByTestId("chat-answer")).toContainText("견적서는 금요일까지입니다");
    await expect(page.getByText("내 업무로 등록")).toHaveCount(0);
    const dump = await page.evaluate(() => JSON.stringify([{ ...localStorage }, { ...sessionStorage }, document.cookie]));
    expect(dump).not.toContain("비밀스러운프로젝트질문");
    expect(dump).not.toContain("금요일까지");
    expect(logs.join("\n")).not.toContain("비밀스러운프로젝트질문");
    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: "설비 개선 과제" })).toBeVisible();
    await opener(page).click();
    await expect(panel(page).getByTestId("chat-turn")).toHaveCount(0);
  });
});
