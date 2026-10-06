/*
 * v2 회의록 상세 · 음성 재생기 E2E.
 * 실제 백엔드 없이 page.route 로 v2 API 와 음성 주소를 가로챈다(음성은 테스트에서 만든 120초 무음 WAV, Range 요청 지원). 데이터는 모두 가상이다.
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const STAFF = { id: 9, name: "이서연", rank: "staff", tenantId: 1 };

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

/** 8kHz 8비트 모노 무음 WAV(초 단위) */
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

const WAV = silentWav(120); // 재생 중 끝나지 않도록 넉넉히

const detail = {
  id: 41, title: "주간 생산 현안 회의", heldAt: "2026-10-01T01:00:00Z", summary: "", decisions: [],
  status: "confirmed", confirmKind: "manager", confirmedBy: null, confirmedAt: null,
  firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: null, registeredBy: { id: 7, name: "한팀장" },
  origin: "audio_minutes", participants: [], recentEvents: [], phase: "active",
  actionItems: [
    {
      id: 101, title: "견적서 송부", assignee: { id: 9, name: "이서연" }, dueDate: "2026-10-09", dueUndetermined: false, status: "confirmed",
      confirmKind: "manager", evidenceStartSec: 12, evidenceQuote: "견적서는 이번 주까지 보내겠습니다", needsCompletion: false, missingFields: [],
    },
  ],
};

const transcript = {
  fullText: "화자1: 안녕하세요\n화자2: 네",
  segments: [
    { speaker: "화자1", start_sec: 3, end_sec: 6, text: "안녕하세요" },
    { speaker: "화자2", start_sec: 15, end_sec: 18, text: "네, 시작하겠습니다" },
  ],
  sttProvider: "fake",
  speakers: [],
};

interface Calls {
  issued: number;
  ranges: (string | undefined)[];
}

/** audioUrl: "ok" 면 발급 성공, 그 외는 [상태, 문구]로 발급 거부 */
async function openDetail(page: Page, audioUrl: "ok" | [number, string]) {
  const calls: Calls = { issued: 0, ranges: [] };
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, STAFF) : json(route, 401, { detail: "인증 필요" }),
  );
  await page.route(/\/api\/meetings\/41$/, (route) => json(route, 200, withAllowed(detail, STAFF)));
  await page.route(/\/api\/meetings\/41\/transcript$/, (route) => json(route, 200, transcript));
  await page.route(/\/api\/meetings\/41\/speakers$/, (route) => json(route, 200, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route(/\/api\/meetings\/41\/change-requests$/, (route) => json(route, 200, []));
  await page.route("**/api/accounts", (route) => json(route, 200, []));
  await page.route(/\/api\/meetings\/41\/audio-url$/, (route) => {
    calls.issued += 1;
    if (audioUrl !== "ok") return json(route, audioUrl[0], { detail: audioUrl[1] });
    return json(route, 200, { url: "/meetings/41/audio?token=signed-e2e", expiresInSec: 600 });
  });
  // 음성: 토큰은 주소의 쿼리로만(로그인 헤더 없음). Range 요청은 206 으로 응답
  await page.route(/\/api\/meetings\/41\/audio\?token=signed-e2e$/, (route) => {
    const headers = route.request().headers();
    calls.ranges.push(headers["range"]);
    expect(headers["authorization"]).toBeUndefined();
    const match = /bytes=(\d+)-(\d*)/.exec(headers["range"] ?? "");
    if (!match) {
      return route.fulfill({ status: 200, contentType: "audio/wav", headers: { "Accept-Ranges": "bytes" }, body: WAV });
    }
    const start = Number(match[1]);
    const end = match[2] ? Math.min(Number(match[2]), WAV.length - 1) : WAV.length - 1;
    return route.fulfill({
      status: 206,
      contentType: "audio/wav",
      headers: { "Accept-Ranges": "bytes", "Content-Range": `bytes ${start}-${end}/${WAV.length}` },
      body: WAV.subarray(start, end + 1),
    });
  });
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto("/v2/meetings/41");
  await expect(page.getByRole("heading", { level: 1, name: "주간 생산 현안 회의" })).toBeVisible();
  return calls;
}

const player = (page: Page) => page.getByRole("region", { name: "음성 재생" });
const audioTime = (page: Page) => page.evaluate(() => document.querySelector("audio")!.currentTime);
const audioPaused = (page: Page) => page.evaluate(() => document.querySelector("audio")!.paused);

test.describe("v2 회의록 음성 재생기", () => {
  test("재생기 표시: 재생 버튼·위치 슬라이더·현재 시각/전체 길이, 발급 요청 1~2회", async ({ page }) => {
    const calls = await openDetail(page, "ok");
    await expect(player(page).getByRole("button", { name: "재생", exact: true })).toBeEnabled();
    await expect(player(page).getByLabel("재생 위치")).toBeVisible();
    await expect(player(page).getByLabel("현재 시각")).toHaveText("00:00:00 / 00:02:00");
    expect(calls.issued).toBeLessThanOrEqual(2); // 개발 모드 StrictMode 는 효과를 두 번 돌린다(첫 요청은 취소)
  });

  test("재생 → 일시정지", async ({ page }) => {
    await openDetail(page, "ok");
    await player(page).getByRole("button", { name: "재생", exact: true }).click();
    await expect(player(page).getByRole("button", { name: "일시정지" })).toBeVisible();
    await expect.poll(() => audioPaused(page)).toBe(false);
    await player(page).getByRole("button", { name: "일시정지" }).click();
    await expect(player(page).getByRole("button", { name: "재생", exact: true })).toBeVisible();
    expect(await audioPaused(page)).toBe(true);
  });

  test("근거 타임스탬프를 누르면 그 시각으로 이동해 재생", async ({ page }) => {
    await openDetail(page, "ok");
    await expect(player(page).getByLabel("현재 시각")).toContainText("/ 00:02:00");
    await page.getByRole("button", { name: "00:00:12부터 재생" }).click();
    await expect.poll(() => audioTime(page), { timeout: 10_000 }).toBeGreaterThanOrEqual(12);
    expect(await audioPaused(page)).toBe(false);
    await expect(player(page).getByRole("button", { name: "일시정지" })).toBeVisible();
  });

  test("전사문 구간 시각을 누르면 그 시각으로 이동해 재생", async ({ page }) => {
    await openDetail(page, "ok");
    await page.getByRole("button", { name: /전사문/ }).first().click();
    await expect(player(page).getByLabel("현재 시각")).toContainText("/ 00:02:00");
    await page.getByRole("button", { name: "00:00:15부터 재생" }).click();
    await expect.poll(() => audioTime(page), { timeout: 10_000 }).toBeGreaterThanOrEqual(15);
    expect(await audioPaused(page)).toBe(false);
  });

  test("음성 파일 없음(404) → 서버 문구 그대로, 타임스탬프를 눌러도 오류 없음", async ({ page }) => {
    await openDetail(page, [404, "음성 파일이 없습니다"]);
    await expect(player(page).getByText("음성 파일이 없습니다", { exact: true }).first()).toBeVisible();
    await expect(player(page).getByRole("button")).toHaveCount(0);
    await page.getByRole("button", { name: "00:00:12부터 재생" }).click();
    await expect(player(page).getByText("음성 파일이 없습니다", { exact: true }).first()).toBeVisible();
  });

  test("발급 거부(403) → 서버 문구와 '음성 파일이 없습니다' 표시", async ({ page }) => {
    await openDetail(page, [403, "재생 권한이 없습니다"]);
    await expect(player(page).getByText("재생 권한이 없습니다 · 음성 파일이 없습니다", { exact: true }).first()).toBeVisible();
  });
});
