/*
 * 작업 88-2 E2E 공용 가짜 서버 부품: 변환 대기열·즉시 변환(process-now)·모바일 올리기 시나리오.
 * 응답 모양은 docs/contracts/process-now-v1.json 과 process-now-examples/valid.json 을 따른다(데이터는 모두 가상).
 */
import { expect, type Page, type Route } from "./test";

export const FAKE_TOKEN = "e2e-fake-token";
export const TOKEN_KEY = "mi.v2.accessToken";
export const ME_MANAGER = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
export const ME_STAFF = { id: 8, name: "이담당", rank: "staff", tenantId: 1 };

export const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

/** 계약 예시(process-now-examples/valid.json)와 같은 모양의 성공 응답. overrides 로 시나리오를 바꾼다 */
export function processNowBody(overrides: Record<string, unknown> = {}) {
  return {
    meetingId: 41,
    jobId: 77,
    status: "queued",
    queueAhead: 0,
    prioritized: true,
    alreadyPrioritized: false,
    waitingForPrior: true,
    message: "시스템의 선행 작업완료 후 처리하겠습니다.",
    workerAlive: true,
    ...overrides,
  };
}

/** 처리 대기(queued) 상태의 회의록 상세. processing 에 대기열 칸이 붙는다 */
export function queuedDetail(overrides: Record<string, unknown> = {}, processing: Record<string, unknown> = {}, allowed: string[] = ["process_now"]) {
  return {
    id: 41,
    title: "주간 생산 현안 회의",
    heldAt: "2026-10-01T01:00:00Z",
    summary: "",
    decisions: [],
    status: "processing",
    confirmKind: null,
    confirmedBy: null,
    confirmedAt: null,
    firstCreatedAt: "2026-10-01T02:00:00Z",
    autoConfirmAt: null,
    registeredBy: { id: 7, name: "한팀장" },
    origin: "audio",
    participants: [{ id: 7, name: "한팀장" }],
    guestParticipants: [],
    actionItems: [],
    recentEvents: [],
    phase: "active",
    allowedActions: ["request_change", "download_excel", "view_history", ...allowed],
    processing: {
      status: "queued",
      errorCode: null,
      startedAt: null,
      finishedAt: null,
      stage: "convert",
      queueAhead: 2,
      prioritized: false,
      waitingForPrior: true,
      workerAlive: true,
      ...processing,
    },
    ...overrides,
  };
}

export interface LoginOptions {
  me?: typeof ME_MANAGER;
  detail?: unknown;
}

/** 로그인 상태 + 기본 가로채기(me·계정 목록·회의록 상세·전사문 없음)를 깔고 로그인 토큰을 넣는다 */
export async function loginWithMocks(page: Page, { me = ME_MANAGER, detail }: LoginOptions = {}) {
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, me) : json(route, 401, { detail: "x" }),
  );
  await page.route("**/api/accounts", (route) => json(route, 200, [{ id: me.id, name: me.name, rank: me.rank }]));
  await page.route("**/api/me/todos", (route) =>
    json(route, 200, {
      awaitingConfirmMeetings: { total: 0, items: [] },
      needsCompletionItems: { total: 0, items: [] },
      myItems: { total: 0, items: [] },
      unreadAutoConfirmed: { total: 0, items: [] },
      pendingChangeRequests: { total: 0, items: [] },
    }),
  );
  if (detail !== undefined) {
    await page.route(/\/api\/meetings\/\d+$/, (route) => json(route, 200, detail));
    await page.route(/\/api\/meetings\/\d+\/transcript$/, (route) => json(route, 404, { detail: "없음" }));
  }
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
}

/** navigator.connection 주입: type 문자열, 또는 undefined(미지원처럼 connection 자체를 없앰) */
export async function injectConnection(page: Page, type: string | undefined) {
  await page.addInitScript((value) => {
    Object.defineProperty(navigator, "connection", { configurable: true, get: () => (value === null ? undefined : { type: value }) });
  }, type ?? null);
}

export const horizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

/** 모바일 올리기 화면 열기(375px 기본). uploads 에는 업로드 요청 본문이 쌓인다 */
export async function openMobileUpload(
  page: Page,
  options: { width?: number; connection?: string | undefined; uploadStatus?: number; uploads?: string[]; detail?: unknown } = {},
) {
  const { width = 375, connection, uploadStatus = 202, uploads = [], detail = queuedDetail() } = options;
  await page.setViewportSize({ width, height: 800 });
  await injectConnection(page, connection);
  await loginWithMocks(page, { detail });
  await page.route("**/api/meetings/upload", (route) => {
    uploads.push(route.request().postDataBuffer()?.toString("utf-8") ?? "");
    return uploadStatus === 202
      ? json(route, 202, { meetingId: 41, jobId: 77 })
      : json(route, uploadStatus, { detail: "서버가 지금 처리할 수 없습니다" });
  });
  await page.goto("/v2/upload");
  await expect(page.getByRole("heading", { level: 1, name: "회의록 올리기" })).toBeVisible();
}

export const AUDIO = { name: "주간회의.m4a", mimeType: "audio/mp4", buffer: Buffer.from("fake-audio-bytes") };

/** 음성 파일을 고르고 회의 유형까지 채운다(제출은 하지 않는다) */
export async function fillAudioForm(page: Page, file = AUDIO) {
  await page.locator('input[type="file"]:not([capture])').setInputFiles(file);
  await expect(page.getByText(file.name)).toBeVisible();
  await page.getByLabel("회의명").fill("주간 회의");
  await page.getByRole("group", { name: "회의 유형" }).locator("label").filter({ has: page.getByRole("radio", { name: "정기회의", exact: true }) }).click();
}

export const submitButton = (page: Page) => page.getByRole("button", { name: "올리고 분석 시작" });
