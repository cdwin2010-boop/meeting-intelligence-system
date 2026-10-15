/*
 * 엑셀 자료 등록·처리 3단계 표시·설정 화면 지식 연동 항목 E2E(작업 84-1). 백엔드 없이 page.route 로 응답한다(데이터는 모두 가상).
 * 범위: 올리기 화면의 자료 종류(엑셀)·확장자 검사·제출 본문·임시 저장 복원, 상세의 자료 파일 안내, 처리 3단계(음성·자료 파일, 처리 중·실패·단계 정보 없음),
 *       시스템 설정의 지식 연동 스위치·주소(경고·사유·서버 422 문구), 375/768/1280px 가로 넘침.
 */
import { expect, test, type Page, type Route, enableXlsxUpload } from "./helpers/test";
import { withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const DRAFT_KEY = "mi.v2.uploadDraft";
const MANAGER = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const SYS = { id: 99, name: "시스템관리", rank: "staff", tenantId: 1, isSystemAdmin: true };
const json = (route: Route, status: number, body: unknown) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const XLSX = {
  name: "회의_자료.xlsx",
  mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  buffer: Buffer.from("PK-fake-xlsx-content"),
};
const TXT = { name: "회의_전사문.txt", mimeType: "text/plain", buffer: Buffer.from("[00:03] 화자1: 안녕하세요", "utf-8") };

async function signIn(page: Page, me: Record<string, unknown>) {
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, me) : json(route, 401, { detail: "x" }),
  );
  await enableXlsxUpload(page);
  await page.goto("/v2/login");
  await page.evaluate(([k, t]) => window.sessionStorage.setItem(k, t), [TOKEN_KEY, FAKE_TOKEN]);
}

const overflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

// ---------------- 올리기 화면 ----------------
async function openUpload(page: Page, uploads: string[] = []) {
  await signIn(page, MANAGER);
  await page.route("**/api/accounts", (route) => json(route, 200, [{ id: 7, name: "한팀장", rank: "manager" }]));
  await page.route("**/api/meetings/upload", (route) => {
    uploads.push(route.request().postDataBuffer()?.toString("utf-8") ?? "");
    return json(route, 202, { meetingId: 77, jobId: 5 });
  });
  await page.goto("/v2/upload");
}
const kindGroup = (page: Page) => page.getByRole("group", { name: "자료 종류" });
const typeLabel = (page: Page, label: string) =>
  page.getByRole("group", { name: "회의 유형" }).locator("label").filter({ has: page.getByRole("radio", { name: label, exact: true }) });

test.describe("엑셀 자료 올리기", () => {
  test("자료 종류에 엑셀이 있고 안내문·허용 확장자가 바뀐다", async ({ page }) => {
    await openUpload(page);
    const radios = kindGroup(page).getByRole("radio");
    await expect(radios).toHaveCount(3);
    await expect(radios.nth(2)).toHaveAccessibleName("자료 파일(엑셀)");
    await kindGroup(page).locator("label").filter({ hasText: "자료 파일(엑셀)" }).click();
    await expect(page.getByText("엑셀(.xlsx)을 올리면 음성 인식 없이")).toBeVisible();
    await expect(page.getByText("비밀번호가 걸린 파일과 매크로 파일은 올릴 수 없습니다")).toBeVisible();
    await expect(page.locator('input[type="file"]')).toHaveAttribute("accept", /\.xlsx/);
  });

  test("txt 를 고르면 서버를 부르지 않고 확장자 안내, xlsx 는 sourceKind 와 함께 제출", async ({ page }) => {
    const uploads: string[] = [];
    await openUpload(page, uploads);
    await kindGroup(page).locator("label").filter({ hasText: "자료 파일(엑셀)" }).click();
    await page.getByLabel("회의명").fill("엑셀 회의");
    await typeLabel(page, "정기회의").click();
    await page.locator('input[type="file"]').setInputFiles(TXT);
    await page.getByRole("button", { name: "올리고 분석 시작" }).click();
    await expect(page.getByText("자료 파일은 .xlsx 파일만 올릴 수 있습니다.")).toBeVisible();
    expect(uploads).toHaveLength(0);
    await page.locator('input[type="file"]').setInputFiles(XLSX);
    await page.getByRole("button", { name: "올리고 분석 시작" }).click();
    await expect.poll(() => uploads.length).toBe(1);
    expect(uploads[0]).toContain('name="sourceKind"');
    expect(uploads[0]).toContain("transcript_xlsx");
    expect(uploads[0]).toContain("회의_자료.xlsx");
  });

  test("임시 저장된 엑셀 종류가 복원된다", async ({ page }) => {
    await signIn(page, MANAGER);
    await page.evaluate(
      ([k, v]) => window.sessionStorage.setItem(k, v),
      [DRAFT_KEY, JSON.stringify({ title: "복원 회의", date: "2026-10-01", time: "10:00", participantIds: [], meetingType: "regular", sourceKind: "transcript_xlsx", savedAt: Date.now() })],
    );
    await page.route("**/api/accounts", (route) => json(route, 200, []));
    await page.goto("/v2/upload?resume=1");
    await expect(kindGroup(page).getByRole("radio", { name: "자료 파일(엑셀)" })).toBeChecked();
  });

  for (const width of [375, 768, 1280]) {
    test(`${width}px 에서 가로 넘침이 없다`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await openUpload(page);
      await kindGroup(page).locator("label").filter({ hasText: "자료 파일(엑셀)" }).click();
      await expect(page.getByText("엑셀(.xlsx)을 올리면 음성 인식 없이")).toBeVisible();
      expect(await overflow(page)).toBeLessThanOrEqual(0);
    });
  }
});

// ---------------- 회의록 상세 ----------------
const DETAIL = (over: Record<string, unknown> = {}) => ({
  id: 77, title: "설비 점검 주간 회의", heldAt: "2026-10-01T04:20:00Z", summary: "", decisions: [], status: "confirmed",
  confirmKind: "manager", confirmedBy: null, confirmedAt: null, firstCreatedAt: "2026-10-01T05:00:00Z", autoConfirmAt: null,
  registeredBy: { id: 7, name: "한팀장" }, origin: "audio_minutes", participants: [], actionItems: [], recentEvents: [], phase: "active", ...over,
});

async function openDetail(page: Page, detail: Record<string, unknown>) {
  const audioRequests: string[] = [];
  await signIn(page, MANAGER);
  await page.route(/\/api\/meetings\/77$/, (route) => json(route, 200, withAllowed(DETAIL(detail) as never, MANAGER)));
  await page.route(/\/api\/meetings\/77\/audio-url$/, (route) => {
    audioRequests.push(route.request().url());
    return json(route, 404, { detail: "음성 파일이 없습니다" });
  });
  await page.goto("/v2/meetings/77");
  await expect(page.getByRole("heading", { level: 1, name: "설비 점검 주간 회의" })).toBeVisible();
  return audioRequests;
}

test.describe("자료 파일(엑셀) 상세", () => {
  test("자료 종류 표시와 '음성 없음' 안내, 음성 재생 요청 없음", async ({ page }) => {
    const audio = await openDetail(page, { sourceKind: "transcript_xlsx", hasAudio: false });
    await expect(page.getByText("자료 파일(엑셀)").first()).toBeVisible();
    await expect(page.getByText("자료 파일로 등록된 회의록입니다(음성 없음)")).toBeVisible();
    expect(audio).toHaveLength(0);
  });
});

// ---------------- 처리 3단계 ----------------
const stagesOf = (page: Page) => page.getByRole("list", { name: "처리 단계" }).getByRole("listitem");

test.describe("처리 3단계 표시", () => {
  test("음성 처리 중(변환 단계)", async ({ page }) => {
    await openDetail(page, { status: "processing", processing: { status: "running", stage: "convert", errorCode: null, startedAt: null, finishedAt: null } });
    await expect(stagesOf(page)).toHaveCount(3);
    await expect(stagesOf(page).nth(0)).toContainText("준비·변환");
    await expect(stagesOf(page).nth(0)).toContainText("진행 중");
    await expect(stagesOf(page).nth(1)).toContainText("대기");
    await expect(stagesOf(page).nth(2)).toContainText("완료 정리");
  });

  test("자료 파일은 1단계가 '음성 변환 건너뜀'이고 바로 2단계", async ({ page }) => {
    await openDetail(page, {
      sourceKind: "transcript_xlsx", hasAudio: false, status: "processing",
      processing: { status: "running", stage: "extract", errorCode: null, startedAt: null, finishedAt: null },
    });
    await expect(stagesOf(page).nth(0)).toContainText("음성 변환 건너뜀(자료 파일)");
    await expect(stagesOf(page).nth(0)).toContainText("건너뜀");
    await expect(stagesOf(page).nth(1)).toContainText("업무 추출");
    await expect(stagesOf(page).nth(1)).toContainText("진행 중");
  });

  test("음성 변환 단계에서 실패하면 멈춘 단계와 실패 코드 문구를 함께 보인다", async ({ page }) => {
    await openDetail(page, { status: "failed", processing: { status: "failed", stage: "convert", errorCode: "timeout", startedAt: null, finishedAt: null } });
    const banner = page.getByRole("alert", { name: "처리 상태" });
    await expect(stagesOf(page).nth(0)).toContainText("여기서 멈춤");
    await expect(banner).toContainText("1단계(준비·변환)에서 멈췄습니다.");
    await expect(banner).toContainText("Gemini 응답 시간이 초과되었습니다");
    await expect(banner).toContainText("timeout");
  });

  test("업무 추출 단계에서 실패(새 코드 문구)", async ({ page }) => {
    await openDetail(page, {
      sourceKind: "transcript_txt", hasAudio: false, status: "failed",
      processing: { status: "failed", stage: "extract", errorCode: "llm_unavailable", startedAt: null, finishedAt: null },
    });
    const banner = page.getByRole("alert", { name: "처리 상태" });
    await expect(stagesOf(page).nth(1)).toContainText("여기서 멈춤");
    await expect(banner).toContainText("2단계(업무 추출)에서 멈췄습니다.");
    await expect(banner).toContainText("로컬 AI 서버에 연결하지 못했");
  });

  test("단계 정보가 없는 서버 응답의 실패는 단계 없이 기존 표시 그대로", async ({ page }) => {
    await openDetail(page, { status: "failed", processing: { status: "failed", errorCode: "internal_error", startedAt: null, finishedAt: null } });
    await expect(page.getByRole("alert", { name: "처리 상태" })).toContainText("서버 내부 오류로 처리하지 못했습니다");
    await expect(stagesOf(page)).toHaveCount(0);
  });

  test("완료된 회의록에는 단계 표시가 없다", async ({ page }) => {
    await openDetail(page, { status: "awaiting_confirmation", processing: { status: "completed", stage: "done", errorCode: null, startedAt: null, finishedAt: null } });
    await expect(stagesOf(page)).toHaveCount(0);
  });

  for (const width of [375, 768, 1280]) {
    test(`${width}px 에서 가로 넘침이 없다`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await openDetail(page, {
        sourceKind: "transcript_xlsx", hasAudio: false, status: "failed",
        processing: { status: "failed", stage: "extract", errorCode: "external_disabled", startedAt: null, finishedAt: null },
      });
      await expect(stagesOf(page)).toHaveCount(3);
      expect(await overflow(page)).toBeLessThanOrEqual(0);
    });
  }
});

// ---------------- 시스템 설정: 지식 연동 ----------------
interface Put {
  key: string;
  body: { value: boolean | string; reason: string };
}

async function openSettings(page: Page, opts: { knowledge?: boolean; endpointError?: string } = {}) {
  const puts: Put[] = [];
  await signIn(page, SYS);
  const base = {
    enforced: true,
    items: [
      { key: "external_llm_enabled", value: false, source: "default", updatedAt: null, updatedBy: null },
      { key: "local_llm_external_host_allowed", value: false, source: "env", updatedAt: null, updatedBy: null },
    ],
  };
  const knowledge = [
    { key: "knowledge_integration_enabled", value: false, source: "default", updatedAt: null, updatedBy: null },
    { key: "knowledge_api_endpoint", value: "", source: "default", updatedAt: null, updatedBy: null },
  ];
  await page.route(/\/api\/admin\/settings(\/.*)?$/, (route) => {
    const request = route.request();
    if (request.method() === "PUT") {
      const key = new URL(request.url()).pathname.split("/").pop() as string;
      const body = request.postDataJSON();
      puts.push({ key, body });
      if (key === "knowledge_api_endpoint" && opts.endpointError) return json(route, 422, { detail: opts.endpointError });
      return json(route, 200, { key, value: body.value, source: "admin", updatedAt: "2026-10-12T00:00:00Z", updatedBy: { id: 99, name: "시스템관리" } });
    }
    return json(route, 200, opts.knowledge === false ? base : { ...base, knowledge });
  });
  await page.goto("/v2/admin/settings");
  return puts;
}

test.describe("시스템 설정 · 지식 연동", () => {
  test("항목·구현 전 안내가 보이고 기존 두 스위치는 그대로다", async ({ page }) => {
    await openSettings(page);
    const section = page.getByRole("region", { name: "지식 연동", exact: true });
    await expect(section).toContainText("아직 구현되지 않았습니다");
    await expect(section).toContainText("켜도 지금은 동작하지 않습니다");
    await expect(section).toContainText("외부 AI(LLM) 허용과는 별개");
    await expect(section.getByText("설정 안 됨")).toBeVisible();
    await expect(page.getByRole("list", { name: "시스템 설정 목록" }).getByRole("listitem")).toHaveCount(2);
  });

  test("서버 응답에 knowledge 가 없으면 지식 연동 항목을 그리지 않는다", async ({ page }) => {
    await openSettings(page, { knowledge: false });
    await expect(page.getByRole("list", { name: "시스템 설정 목록" })).toBeVisible();
    await expect(page.getByRole("region", { name: "지식 연동", exact: true })).toHaveCount(0);
  });

  test("켤 때는 사내 연동 경고 확인 → 사유 필수 → 변경", async ({ page }) => {
    const puts = await openSettings(page);
    await page.getByRole("button", { name: "지식 연동 켜기" }).click();
    const warn = page.getByRole("alertdialog", { name: "지식 연동 켜기" });
    await expect(warn).toContainText("사내(사설망)의 지식 시스템과 연동");
    await expect(warn).toContainText("외부 AI 사용이 허용되지는 않습니다");
    await warn.getByRole("button", { name: "사내 연동 허용으로 계속" }).click();
    const reason = page.getByRole("alertdialog", { name: "지식 연동 켜기" });
    const confirm = reason.getByRole("button", { name: "연동 켜기" });
    await expect(confirm).toBeDisabled();
    await reason.getByLabel(/사유/).fill("사내 지식 시스템 시험");
    await confirm.click();
    await expect(page.getByRole("status").filter({ hasText: "지식 연동을 켰습니다" })).toBeVisible();
    expect(puts).toEqual([{ key: "knowledge_integration_enabled", body: { value: true, reason: "사내 지식 시스템 시험" } }]);
  });

  test("주소 변경: 사유 필수, 서버 422 문구를 그대로 보이고 성공하면 닫힌다", async ({ page }) => {
    const puts = await openSettings(page, { endpointError: "사설망·루프백 주소만 허용합니다. 외부 주소는 설정할 수 없습니다." });
    await page.getByRole("button", { name: "지식 연동 주소 변경" }).click();
    const dialog = page.getByRole("dialog", { name: "지식 연동 주소 변경" });
    await expect(dialog).toContainText("주소 전체는 변경 이력에 남지 않습니다");
    await dialog.getByLabel("주소").fill("https://kms.example.com");
    await expect(dialog.getByRole("button", { name: "주소 저장" })).toBeDisabled(); // 사유 없음
    await dialog.getByLabel(/사유/).fill("외부 주소 시험");
    await dialog.getByRole("button", { name: "주소 저장" }).click();
    await expect(dialog.getByRole("alert")).toContainText("사설망·루프백 주소만 허용합니다");
    await expect(dialog.getByLabel("주소")).toHaveValue("https://kms.example.com"); // 입력값 유지
    expect(puts).toEqual([{ key: "knowledge_api_endpoint", body: { value: "https://kms.example.com", reason: "외부 주소 시험" } }]);
  });

  test("주소 변경 성공", async ({ page }) => {
    const puts = await openSettings(page);
    await page.getByRole("button", { name: "지식 연동 주소 변경" }).click();
    const dialog = page.getByRole("dialog", { name: "지식 연동 주소 변경" });
    await dialog.getByLabel("주소").fill("http://192.168.0.10:9000");
    await dialog.getByLabel(/사유/).fill("내부 서버 지정");
    await dialog.getByRole("button", { name: "주소 저장" }).click();
    await expect(page.getByRole("status").filter({ hasText: "지식 연동 주소를 변경했습니다" })).toBeVisible();
    expect(puts[0]).toEqual({ key: "knowledge_api_endpoint", body: { value: "http://192.168.0.10:9000", reason: "내부 서버 지정" } });
  });

  for (const width of [375, 768, 1280]) {
    test(`${width}px 에서 가로 넘침이 없다`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await openSettings(page);
      await expect(page.getByRole("region", { name: "지식 연동", exact: true })).toBeVisible();
      expect(await overflow(page)).toBeLessThanOrEqual(0);
    });
  }
});
