/*
 * v2 회의록 상세 · 질의응답 서랍 E2E (작업 89-2a, 계약 docs/contracts/meeting-chat-v1.json).
 * 실제 백엔드 없이 page.route 로 상세·전사문·음성·chat(SSE)을 가로챈다. 데이터는 모두 가상이다.
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const STAFF = { id: 9, name: "이서연", rank: "staff", tenantId: 1 };
const FIXED = "본 회의록에서 확인할 수 없는 내용입니다";

const json = (route: Route, status: number, body: unknown, headers: Record<string, string> = {}) =>
  route.fulfill({ status, contentType: "application/json", headers, body: JSON.stringify(body) });

function silentWav(seconds: number): Buffer {
  const rate = 8000;
  const data = Buffer.alloc(rate * seconds, 128);
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write("WAVEfmt ", 8);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(rate, 24);
  header.writeUInt32LE(rate, 28);
  header.writeUInt16LE(1, 32);
  header.writeUInt16LE(8, 34);
  header.write("data", 36);
  header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}
const WAV = silentWav(120);

const detail = {
  id: 41, title: "주간 생산 현안 회의", heldAt: "2026-10-01T01:00:00Z", summary: "", decisions: [],
  status: "confirmed", confirmKind: "manager", confirmedBy: null, confirmedAt: null,
  firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: null, registeredBy: { id: 7, name: "한팀장" },
  origin: "audio_minutes", participants: [], recentEvents: [], phase: "active", hasAudio: true,
  actionItems: [],
};

const transcript = {
  fullText: "화자1: 안녕하세요\n화자2: 네",
  segments: [
    { speaker: "화자1", start_sec: 3, end_sec: 6, text: "안녕하세요" },
    { speaker: "화자2", start_sec: 15, end_sec: 18, text: "네, 시작하겠습니다" },
    { speaker: "화자2", start_sec: 75, end_sec: 80, text: "견적서는 금요일까지 보내겠습니다" },
  ],
  sttProvider: "fake",
  speakers: [],
};

const sse = (events: [string, unknown][]) => events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join("");
const META = { requestId: "abc123def456", contextMode: "summary", model: "stub", confirmedPendingIncluded: false };

function answerBody(opts: { text?: string; items?: unknown[]; pending?: boolean; noAnswer?: boolean } = {}) {
  const noAnswer = opts.noAnswer === true;
  return sse([
    ["meta", { ...META, confirmedPendingIncluded: opts.pending === true }],
    ["delta", { text: noAnswer ? FIXED : (opts.text ?? "견적서는 금요일까지 보냅니다. [화자2/01:15]") }],
    ["citations", { items: noAnswer ? [] : (opts.items ?? [{ refId: "S1", utteranceId: 2, speaker: "화자2", startSec: 75, label: "화자2/01:15" }]) }],
    ["done", { grounded: !noAnswer, noAnswer, elapsedMs: 12 }],
  ]);
}

interface Chat {
  bodies: { question: string; history: unknown[]; quickPrompt: string | null }[];
}

type Handler = (route: Route, body: Chat["bodies"][number]) => Promise<void> | void;

async function openDetail(page: Page, handler: Handler, options: { askMeeting?: boolean } = {}): Promise<Chat> {
  const chat: Chat = { bodies: [] };
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, STAFF) : json(route, 401, { detail: "인증 필요" }),
  );
  await page.route(/\/api\/meetings\/41$/, (route) => {
    const body: { allowedActions: string[] } = withAllowed(detail, STAFF) as never;
    if (options.askMeeting === false) body.allowedActions = body.allowedActions.filter((a: string) => a !== "ask_meeting");
    return json(route, 200, body);
  });
  await page.route(/\/api\/meetings\/41\/transcript$/, (route) => json(route, 200, transcript));
  await page.route(/\/api\/meetings\/41\/speakers$/, (route) => json(route, 200, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route(/\/api\/meetings\/41\/change-requests$/, (route) => json(route, 200, []));
  await page.route("**/api/accounts", (route) => json(route, 200, []));
  await page.route(/\/api\/meetings\/41\/audio-url$/, (route) => json(route, 200, { url: "/meetings/41/audio?token=signed-e2e", expiresInSec: 600 }));
  await page.route(/\/api\/meetings\/41\/audio\?token=signed-e2e$/, (route) => {
    const range = route.request().headers()["range"];
    const match = /bytes=(\d+)-(\d*)/.exec(range ?? "");
    if (!match) return route.fulfill({ status: 200, contentType: "audio/wav", headers: { "Accept-Ranges": "bytes" }, body: WAV });
    const start = Number(match[1]);
    const end = match[2] ? Math.min(Number(match[2]), WAV.length - 1) : WAV.length - 1;
    return route.fulfill({ status: 206, contentType: "audio/wav", headers: { "Accept-Ranges": "bytes", "Content-Range": `bytes ${start}-${end}/${WAV.length}` }, body: WAV.subarray(start, end + 1) });
  });
  await page.route(/\/api\/meetings\/41\/chat$/, async (route) => {
    const body = route.request().postDataJSON();
    chat.bodies.push(body);
    await handler(route, body);
  });
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto("/v2/meetings/41");
  await expect(page.getByRole("heading", { level: 1, name: "주간 생산 현안 회의" })).toBeVisible();
  return chat;
}

const ok = (opts?: Parameters<typeof answerBody>[0]): Handler => (route) =>
  route.fulfill({ status: 200, contentType: "text/event-stream", body: answerBody(opts) });

const opener = (page: Page) => page.getByRole("button", { name: "질문하기" });
const panel = (page: Page) => page.getByRole("dialog", { name: "회의록 질의응답" });
async function ask(page: Page, text: string) {
  await panel(page).getByRole("textbox", { name: "질문" }).fill(text);
  await panel(page).getByRole("button", { name: "보내기" }).click();
}

test.describe("v2 회의록 질의응답 서랍", () => {
  test("서랍 열기/닫기: 범위 표시·안내, ESC 로 닫으면 여는 버튼으로 포커스 복귀", async ({ page }) => {
    await openDetail(page, ok());
    await opener(page).click();
    await expect(panel(page)).toBeVisible();
    await expect(panel(page).getByText("범위: 이 회의록")).toBeVisible();
    await expect(panel(page).getByText("이 회의록 안의 내용만 답변합니다.", { exact: false })).toBeVisible();
    await expect(panel(page).getByRole("textbox", { name: "질문" })).toBeFocused();
    // PC 폭: 오른쪽 서랍
    const box = (await panel(page).boundingBox())!;
    expect(box.x + box.width).toBeGreaterThan(1270);
    expect(box.width).toBeLessThan(500);
    await page.keyboard.press("Escape");
    await expect(panel(page)).toBeHidden();
    await expect(opener(page)).toBeFocused();
    await opener(page).click();
    await panel(page).getByRole("button", { name: "닫기" }).click();
    await expect(panel(page)).toBeHidden();
  });

  test("추천 질문 칩 3개: 클릭하면 quickPrompt 로 전송", async ({ page }) => {
    const chat = await openDetail(page, ok());
    await opener(page).click();
    const chips = panel(page).getByRole("list", { name: "추천 질문" }).getByRole("button");
    await expect(chips).toHaveText(["결정사항", "내 업무", "일정"]);
    await chips.nth(0).click();
    await expect(panel(page).getByTestId("chat-answer")).toHaveCount(1);
    await chips.nth(1).click();
    await expect(panel(page).getByTestId("chat-answer")).toHaveCount(2);
    await chips.nth(2).click();
    await expect(panel(page).getByTestId("chat-answer")).toHaveCount(3);
    expect(chat.bodies.map((b) => b.quickPrompt)).toEqual(["decisions", "my_tasks", "schedule"]);
  });

  test("전송 중: 입력 잠금·\"근거 확인 중\" 표시 뒤 완성된 답변과 근거(화자/MM:SS)", async ({ page }) => {
    await openDetail(page, async (route) => {
      await new Promise((r) => setTimeout(r, 700));
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: answerBody() });
    });
    await opener(page).click();
    await ask(page, "견적서는 언제까지 보내나요?");
    await expect(panel(page).getByTestId("chat-pending")).toContainText("근거 확인 중");
    await expect(panel(page).getByRole("textbox", { name: "질문" })).toBeDisabled();
    await expect(panel(page).getByRole("button", { name: "취소" })).toBeVisible();
    await expect(panel(page).getByTestId("chat-answer")).toHaveCount(0); // 완성 전에는 답변이 보이지 않는다
    const answer = panel(page).getByTestId("chat-answer");
    await expect(answer).toContainText("견적서는 금요일까지 보냅니다.");
    await expect(panel(page).getByTestId("chat-pending")).toHaveCount(0);
    await expect(answer.getByTestId("chat-citation")).toHaveText("화자2/01:15");
    await expect(panel(page).getByTestId("chat-pending-badge")).toHaveCount(0);
    await expect(panel(page).getByRole("textbox", { name: "질문" })).toBeEnabled();
  });

  test("\"확정 전 내용 포함\" 배지: meta.confirmedPendingIncluded=true 일 때만", async ({ page }) => {
    await openDetail(page, (route, body) =>
      route.fulfill({ status: 200, contentType: "text/event-stream", body: answerBody({ pending: body.question.includes("대기") }) }),
    );
    await opener(page).click();
    await ask(page, "일반 질문");
    await expect(panel(page).getByTestId("chat-answer")).toHaveCount(1);
    await expect(panel(page).getByText("확정 전 내용 포함")).toHaveCount(0);
    await ask(page, "확정 대기 업무도 알려줘");
    await expect(panel(page).getByText("확정 전 내용 포함")).toHaveCount(1);
  });

  test("근거 클릭: 전사문이 열리고 해당 발화로 이동, 음성 재생기가 그 시각으로 이동", async ({ page }) => {
    await openDetail(page, ok());
    await opener(page).click();
    await ask(page, "견적서 일정");
    await panel(page).getByTestId("chat-citation").click();
    const row = page.locator('[data-segment-index="2"]');
    await expect(row).toBeVisible();
    await expect(row).toContainText("견적서는 금요일까지 보내겠습니다");
    await expect(row.getByText("근거 위치")).toBeAttached();
    await expect(page.getByTestId("evidence-approx-notice")).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => document.querySelector("audio")!.currentTime)).toBeGreaterThan(74);
    // 서랍은 열린 채 유지(PC)
    await expect(panel(page)).toBeVisible();
  });

  test("근거 위치를 못 찾으면 가장 가까운 이전 발화와 \"근사 위치\" 표시", async ({ page }) => {
    await openDetail(page, ok({ items: [{ refId: "S1", utteranceId: 9, speaker: "화자2", startSec: 40, label: "화자2/00:40" }] }));
    await opener(page).click();
    await ask(page, "질문");
    await panel(page).getByTestId("chat-citation").click();
    await expect(page.locator('[data-segment-index="1"]').getByText("근거 위치")).toBeAttached();
    await expect(page.getByTestId("evidence-approx-notice")).toContainText("근사 위치");
  });

  test("요약 근거(시각 없음)는 누를 수 없는 글자로만 표시", async ({ page }) => {
    await openDetail(page, ok({ items: [{ refId: "S1", utteranceId: null, speaker: "", startSec: null, label: "회의록·결정사항" }], text: "결정사항입니다. [회의록·결정사항]" }));
    await opener(page).click();
    await ask(page, "결정");
    await expect(panel(page).getByTestId("chat-citation-static")).toHaveText("회의록·결정사항");
    await expect(panel(page).getByTestId("chat-citation")).toHaveCount(0);
  });

  test("noAnswer: 서버 고정 문구만 보이고 근거 목록은 없다", async ({ page }) => {
    await openDetail(page, ok({ noAnswer: true }));
    await opener(page).click();
    await ask(page, "회의와 무관한 질문");
    await expect(panel(page).getByTestId("chat-answer")).toHaveText(FIXED);
    await expect(panel(page).getByRole("list", { name: "근거 목록" })).toHaveCount(0);
  });

  test("grounded=false 도 고정 문구로 보고 근거를 숨긴다", async ({ page }) => {
    await openDetail(page, (route) =>
      route.fulfill({
        status: 200, contentType: "text/event-stream",
        body: sse([["meta", META], ["delta", { text: FIXED }], ["citations", { items: [{ refId: "S1", utteranceId: 1, speaker: "화자2", startSec: 15, label: "화자2/00:15" }] }], ["done", { grounded: false, noAnswer: false, elapsedMs: 3 }]]),
      }),
    );
    await opener(page).click();
    await ask(page, "질문");
    await expect(panel(page).getByTestId("chat-answer")).toHaveText(FIXED);
    await expect(panel(page).getByTestId("chat-citation")).toHaveCount(0);
  });

  const ERRORS: { name: string; handler: Handler; cause: string; action: string }[] = [
    {
      // 다른 출처(포트)의 서버 헤더는 서버가 Access-Control-Expose-Headers 로 열어야 브라우저 코드가 읽는다
      name: "503 gpu_busy(Retry-After 표시)",
      handler: (route) => json(route, 503, { detail: { code: "gpu_busy", message: "음성 변환 중" } }, { "Retry-After": "12", "Access-Control-Expose-Headers": "Retry-After" }),
      cause: "음성 변환이 진행 중입니다.", action: "잠시 후 다시 시도해 주세요. (약 12초 후)",
    },
    {
      name: "503 gpu_busy(Retry-After 헤더를 읽을 수 없으면 초 없이 표시)",
      handler: (route) => json(route, 503, { detail: { code: "gpu_busy", message: "음성 변환 중" } }, { "Retry-After": "12" }),
      cause: "음성 변환이 진행 중입니다.", action: "잠시 후 다시 시도해 주세요.",
    },
    {
      name: "503 llm_unavailable",
      handler: (route) => json(route, 503, { detail: { code: "llm_unavailable", message: "엔진 없음" } }),
      cause: "답변 엔진을 사용할 수 없습니다.", action: "관리자에게 문의하세요.",
    },
    {
      name: "스트림 error 이벤트(시간 초과)",
      handler: (route) => route.fulfill({ status: 200, contentType: "text/event-stream", body: sse([["meta", META], ["error", { code: "llm_timeout", message: "시간 초과" }]]) }),
      cause: "응답이 지연되고 있습니다.", action: "다시 시도해 주세요.",
    },
    {
      name: "연결 끊김",
      handler: (route) => route.abort("connectionreset"),
      cause: "응답이 지연되고 있습니다.", action: "다시 시도해 주세요.",
    },
    {
      name: "끝 이벤트 없이 끊긴 스트림",
      handler: (route) => route.fulfill({ status: 200, contentType: "text/event-stream", body: sse([["meta", META]]) }),
      cause: "응답이 지연되고 있습니다.", action: "다시 시도해 주세요.",
    },
    {
      name: "409 정리된 회의록 없음",
      handler: (route) => json(route, 409, { detail: "서버 원문 409" }),
      cause: "정리된 회의록이 아직 없습니다.", action: "처리가 끝난 뒤 다시 질문해 주세요.",
    },
    {
      name: "422 서버 문구",
      handler: (route) => json(route, 422, { detail: "질문은 1~500자여야 합니다" }),
      cause: "질문은 1~500자여야 합니다", action: "질문을 확인한 뒤 다시 보내 주세요.",
    },
    {
      name: "429",
      handler: (route) => json(route, 429, { detail: "서버 원문 429" }, { "Retry-After": "30" }),
      cause: "질문이 너무 잦습니다.", action: "잠시 후 다시 시도해 주세요.",
    },
    {
      name: "403 서버 문구",
      handler: (route) => json(route, 403, { detail: "삭제된 회의록입니다" }),
      cause: "삭제된 회의록입니다", action: "삭제된 회의록에는 질문할 수 없습니다.",
    },
    {
      name: "404 서버 문구",
      handler: (route) => json(route, 404, { detail: "회의록을 찾을 수 없습니다" }),
      cause: "회의록을 찾을 수 없습니다", action: "회의록 목록에서 다시 열어 주세요.",
    },
  ];
  for (const c of ERRORS) {
    test(`오류 안내(원인+조치): ${c.name}`, async ({ page }) => {
      await openDetail(page, c.handler);
      await opener(page).click();
      await ask(page, "오류 시험 질문");
      const error = panel(page).getByTestId("chat-error");
      await expect(error).toBeVisible();
      await expect(error.locator("span").nth(0)).toHaveText(c.cause);
      await expect(error.locator("span").nth(1)).toHaveText(c.action);
      // 실패한 질문은 입력창에 돌려놓아 다시 보낼 수 있고, 오류 문구에 질문 원문은 들어가지 않는다
      await expect(panel(page).getByRole("textbox", { name: "질문" })).toHaveValue("오류 시험 질문");
      await expect(error).not.toContainText("오류 시험 질문");
      await expect(panel(page).getByTestId("chat-answer")).toHaveCount(0);
    });
  }

  test("500자 초과: 전송 전에 안내하고 요청을 보내지 않는다(500자는 가능)", async ({ page }) => {
    const chat = await openDetail(page, ok());
    await opener(page).click();
    const input = panel(page).getByRole("textbox", { name: "질문" });
    await input.fill("가".repeat(501));
    await expect(panel(page).getByTestId("chat-count")).toContainText("질문은 500자까지 입력할 수 있습니다");
    await expect(panel(page).getByRole("button", { name: "보내기" })).toBeDisabled();
    await input.press("Enter");
    expect(chat.bodies).toHaveLength(0);
    await input.fill("가".repeat(500));
    await expect(panel(page).getByRole("button", { name: "보내기" })).toBeEnabled();
    await panel(page).getByRole("button", { name: "보내기" }).click();
    await expect(panel(page).getByTestId("chat-answer")).toHaveCount(1);
    expect(chat.bodies).toHaveLength(1);
  });

  test("취소: 요청을 중단하고 입력을 돌려놓는다", async ({ page }) => {
    await openDetail(page, async (route) => {
      await new Promise((r) => setTimeout(r, 4000));
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: answerBody() }).catch(() => undefined);
    });
    await opener(page).click();
    const failed = page.waitForEvent("requestfailed", (r) => r.url().includes("/chat"));
    await ask(page, "오래 걸리는 질문");
    await panel(page).getByRole("button", { name: "취소" }).click();
    expect((await failed).failure()?.errorText).toMatch(/ABORTED|abort/i); // 브라우저가 요청을 끊었다
    await expect(panel(page).getByTestId("chat-pending")).toHaveCount(0);
    await expect(panel(page).getByText("질문을 취소했습니다.")).toBeVisible();
    await expect(panel(page).getByRole("textbox", { name: "질문" })).toHaveValue("오래 걸리는 질문");
    await expect(panel(page).getByTestId("chat-error")).toHaveCount(0);
    await expect(panel(page).getByTestId("chat-answer")).toHaveCount(0);
  });

  test("대화 이력은 직전 대화를 history 로 보내고 4턴까지만 유지한다", async ({ page }) => {
    const chat = await openDetail(page, ok());
    await opener(page).click();
    for (let i = 1; i <= 6; i += 1) {
      await ask(page, `질문 ${i}`);
      await expect(panel(page).getByTestId("chat-answer")).toHaveCount(Math.min(i, 4));
    }
    expect(chat.bodies[0].history).toHaveLength(0);
    expect(chat.bodies[5].history).toHaveLength(4);
    expect((chat.bodies[5].history as { question: string }[])[3].question).toBe("질문 5");
    await expect(panel(page).getByText("질문 1", { exact: true })).toHaveCount(0);
  });

  test("서랍을 닫았다 열어도 같은 회의록에서는 대화가 유지된다", async ({ page }) => {
    await openDetail(page, ok());
    await opener(page).click();
    await ask(page, "유지 질문");
    await expect(panel(page).getByTestId("chat-answer")).toHaveCount(1);
    await page.keyboard.press("Escape");
    await opener(page).click();
    await expect(panel(page).getByText("유지 질문", { exact: true })).toBeVisible();
  });

  test("ask_meeting 이 없으면 질문 버튼이 없다", async ({ page }) => {
    await openDetail(page, ok(), { askMeeting: false });
    await expect(opener(page)).toHaveCount(0);
    await expect(page.getByRole("dialog", { name: "회의록 질의응답" })).toHaveCount(0);
  });

  test("모바일 폭: 바텀시트(아래에 붙고 화면 너비), 근거를 누르면 시트를 접고 전사 위치로 이동", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 800 });
    await openDetail(page, ok());
    await opener(page).click();
    const box = (await panel(page).boundingBox())!;
    expect(box.width).toBeGreaterThan(380);
    expect(box.y + box.height).toBeGreaterThan(790); // 아래에 붙는다
    expect(box.y).toBeGreaterThan(100); // 화면 전체를 덮지 않는다(최대 80%)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await ask(page, "견적서 일정");
    await panel(page).getByTestId("chat-citation").click();
    await expect(panel(page)).toBeHidden();
    await expect(page.locator('[data-segment-index="2"]').getByText("근거 위치")).toBeAttached();
  });

  test("저장 안 함: 저장소·콘솔에 질문·답변이 없고 새로고침하면 대화가 사라진다", async ({ page }) => {
    const logs: string[] = [];
    page.on("console", (m) => logs.push(m.text()));
    await openDetail(page, ok());
    await opener(page).click();
    await ask(page, "비밀스러운질문문장");
    await expect(panel(page).getByTestId("chat-answer")).toContainText("견적서는 금요일까지 보냅니다");
    const dump = await page.evaluate(() => JSON.stringify([{ ...localStorage }, { ...sessionStorage }, document.cookie]));
    expect(dump).not.toContain("비밀스러운질문문장");
    expect(dump).not.toContain("금요일까지");
    expect(await page.evaluate(async () => (indexedDB.databases ? (await indexedDB.databases()).length : 0))).toBe(0);
    expect(logs.join("\n")).not.toContain("비밀스러운질문문장");
    expect(logs.join("\n")).not.toContain("금요일까지");
    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: "주간 생산 현안 회의" })).toBeVisible();
    await expect(panel(page)).toHaveCount(0);
    await opener(page).click();
    await expect(panel(page).getByTestId("chat-turn")).toHaveCount(0);
    await expect(panel(page).getByText("비밀스러운질문문장")).toHaveCount(0);
  });

  test("\"내 업무로 등록\" 버튼은 없다", async ({ page }) => {
    await openDetail(page, ok());
    await opener(page).click();
    await ask(page, "질문");
    await expect(panel(page).getByTestId("chat-answer")).toHaveCount(1);
    await expect(page.getByRole("button", { name: /내 업무로 등록/ })).toHaveCount(0);
    await expect(page.getByText("내 업무로 등록")).toHaveCount(0);
  });
});
