/*
 * v2 회의록 상세 · 5개 항목·변경 이력·엑셀 다운로드·수정 회의록 업로드·수기 업무 등록 E2E.
 * 실제 백엔드 없이 page.route 로 v2 API 를 가로챈다(상세는 동작에 따라 바뀌는 가짜 상태). 데이터는 모두 가상이다.
 */
import { expect, test, type Page, type Route } from "./helpers/test";
import { withAllowed } from "./helpers/allowed-actions";

const FAKE_TOKEN = "e2e-fake-token";
const TOKEN_KEY = "mi.v2.accessToken";
const MANAGER = { id: 7, name: "한팀장", rank: "manager", tenantId: 1 };
const STAFF = { id: 9, name: "이서연", rank: "staff", tenantId: 1 };

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const item = (id: number, title: string, extra: Record<string, unknown> = {}) => ({
  id, title, assignee: { id: 9, name: "이서연" }, dueDate: "2026-10-09", dueUndetermined: false, status: "pending",
  confirmKind: null, evidenceStartSec: null, evidenceQuote: null, needsCompletion: false, missingFields: [], origin: "ai", ...extra,
});

const MINUTES = {
  purpose: "STT 정확도 개선 일정 점검", discussion: "화자 분리 오류 논의\n시안 일정 논의", decisions: "시안은 목요일까지 확정",
  risks: "내용없음", nextAgenda: "점심 메뉴", engine: "gemini", updatedBy: null, updatedAt: null,
};

type MinutesBody = Omit<typeof MINUTES, "engine" | "updatedBy" | "updatedAt"> & { engine: string | null; updatedBy: unknown; updatedAt: string | null };
type Detail = Record<string, unknown> & { actionItems: ReturnType<typeof item>[]; minutes?: MinutesBody };

const baseDetail = (): Detail => ({
  id: 41, title: "주간 생산 현안 회의", heldAt: "2026-10-01T01:00:00Z", summary: "", decisions: [],
  status: "confirmed", confirmKind: "manager", confirmedBy: null, confirmedAt: null,
  firstCreatedAt: "2026-10-01T02:00:00Z", autoConfirmAt: null, registeredBy: { id: 7, name: "한팀장" },
  origin: "audio_minutes", participants: [{ id: 9, name: "이서연" }], guestParticipants: [], recentEvents: [], phase: "active",
  minutes: { ...MINUTES }, actionItems: [item(101, "견적서 송부"), item(102, "설비 점검")],
});

interface Calls {
  bodies: Record<string, unknown[]>;
}

type Fail = Record<string, [number, string]>;

/** 가짜 서버. fail[동작] 이 있으면 그 응답으로 한 번 거부한다 */
async function openDetail(
  page: Page,
  account: typeof MANAGER,
  detail: Detail,
  opts: { fail?: Fail; history?: unknown[]; preview?: (choices: Record<string, number>) => unknown } = {},
) {
  const fail = opts.fail ?? {};
  const calls: Calls = { bodies: {} };
  const take = (key: string, route: Route): [number, string] | undefined => {
    (calls.bodies[key] ??= []).push(route.request().postData());
    const failure = fail[key];
    delete fail[key];
    return failure;
  };
  await page.route("**/api/auth/me", (route) =>
    route.request().headers()["authorization"] === `Bearer ${FAKE_TOKEN}` ? json(route, 200, account) : json(route, 401, { detail: "인증 필요" }),
  );
  await page.route(/\/api\/meetings\/41$/, (route) => json(route, 200, withAllowed(detail, account)));
  await page.route(/\/api\/meetings\/41\/transcript$/, (route) => json(route, 404, { detail: "전사문이 없습니다" }));
  await page.route(/\/api\/meetings\/41\/speakers$/, (route) => json(route, 200, { labels: [], speakers: [], autoAssignedItemIds: [] }));
  await page.route(/\/api\/meetings\/41\/change-requests$/, (route) => json(route, 200, []));
  await page.route(/\/api\/meetings\/41\/audio-url$/, (route) => json(route, 404, { detail: "음성 파일이 없습니다" }));
  await page.route("**/api/accounts", (route) =>
    json(route, 200, [{ id: 9, name: "이서연", rank: "staff" }, { id: 12, name: "박동명", rank: "staff" }, { id: 13, name: "박동명", rank: "staff" }]),
  );
  await page.route(/\/api\/meetings\/41\/minutes$/, (route) => {
    const failure = take("minutes", route);
    if (failure) return json(route, failure[0], { detail: failure[1] });
    const body = JSON.parse(route.request().postData() ?? "{}") as Record<string, string>;
    const next = { ...detail.minutes! };
    for (const [key, value] of Object.entries(body)) (next as Record<string, unknown>)[key] = value.trim() || "내용없음";
    next.updatedBy = MANAGER;
    next.updatedAt = "2026-10-05T03:00:00Z";
    detail.minutes = next;
    return json(route, 200, next);
  });
  await page.route(/\/api\/meetings\/41\/history$/, (route) => {
    const failure = take("history", route);
    return failure ? json(route, failure[0], { detail: failure[1] }) : json(route, 200, opts.history ?? []);
  });
  await page.route(/\/api\/meetings\/41\/export$/, (route) => {
    const failure = take("export", route);
    if (failure) return json(route, failure[0], { detail: failure[1] });
    expect(route.request().headers()["authorization"]).toBe(`Bearer ${FAKE_TOKEN}`); // 로그인 헤더로 받는다
    return route.fulfill({ status: 200, contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", body: Buffer.from("FAKE-XLSX-BYTES") });
  });
  await page.route(/\/api\/meetings\/41\/update-upload\/preview$/, (route) => {
    const failure = take("preview", route);
    if (failure) return json(route, failure[0], { detail: failure[1] });
    const raw = route.request().postData() ?? "";
    const match = /name="choices"\r\n\r\n([^\r]*)/.exec(raw);
    return json(route, 200, opts.preview ? opts.preview(JSON.parse(match?.[1] || "{}")) : emptyPreview());
  });
  await page.route(/\/api\/meetings\/41\/update-upload\/apply$/, (route) => {
    const failure = take("apply", route);
    if (failure) return json(route, failure[0], { detail: failure[1] });
    detail.actionItems = [...detail.actionItems, item(201, "파일로 추가한 업무", { origin: "manual", evidenceQuote: "등록자 직권 지정", assignee: null as never, needsCompletion: true, missingFields: ["assignee"] })];
    return json(route, 200, {
      batchId: "b1", updatedItemIds: [101], addedItemIds: [201], skipped: [{ itemId: 102, reason: "확정된 업무라 바꾸지 않았습니다" }],
      unchangedItemIds: [], participantsChanged: false, minutesChanged: [], warnings: [],
    });
  });
  await page.route(/\/api\/meetings\/41\/action-items$/, (route) => {
    const failure = take("manual", route);
    if (failure) return json(route, failure[0], { detail: failure[1] });
    const body = JSON.parse(route.request().postData() ?? "{}");
    const created = item(301, body.title, {
      origin: "manual", evidenceQuote: "등록자 직권 지정", assignee: (body.assigneeId ? { id: body.assigneeId, name: "이서연" } : null) as never,
      dueDate: body.dueDate ?? null, dueUndetermined: Boolean(body.dueUndetermined),
    });
    detail.actionItems = [...detail.actionItems, created];
    return json(route, 201, created);
  });
  await page.goto("/v2/login");
  await page.evaluate(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, FAKE_TOKEN]);
  await page.goto("/v2/meetings/41");
  await expect(page.getByRole("heading", { level: 1, name: detail.title as string })).toBeVisible();
  return calls;
}

function emptyPreview() {
  return {
    canApply: true, errors: [], warnings: [], ambiguities: [],
    items: { updates: [], unchanged: [], skipped: [], added: [] }, participants: null, minutes: {},
  };
}

const more = (page: Page) => page.getByRole("button", { name: "회의록 더보기" });
const menuItem = (page: Page, name: string) => page.getByRole("menu", { name: "회의록 동작" }).getByRole("menuitem", { name });
const dialog = (page: Page) => page.getByRole("dialog");
const overview = (page: Page) => page.getByRole("region", { name: "회의 개요" });
const row = (page: Page, title: string) => page.getByRole("table", { name: "업무 원장" }).locator("tbody tr").filter({ hasText: title });

async function chooseMenu(page: Page, name: string) {
  await more(page).click();
  await menuItem(page, name).click();
}

const XLSX = { name: "수정.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("fake") };

test.describe("v2 회의록 5개 항목", () => {
  test("5개 항목 표시: '내용없음'은 흐리게, 요약·결정사항 자리를 대체", async ({ page }) => {
    await openDetail(page, MANAGER, baseDetail());
    const box = overview(page);
    for (const label of ["목적", "주요 논의사항", "결정사항", "리스크", "다음 안건"]) await expect(box.getByText(label, { exact: true })).toBeVisible();
    await expect(box).toContainText("STT 정확도 개선 일정 점검");
    await expect(box).toContainText("시안은 목요일까지 확정");
    await expect(box.getByText("내용없음", { exact: true })).toHaveCount(1); // 리스크
    await expect(box).toContainText("생성 엔진 gemini");
    await expect(box).not.toContainText("요약이 없습니다");
  });

  test("5개 항목이 없는 기존 회의록: 안내와 직권 수정으로 채우기", async ({ page }) => {
    const detail = baseDetail();
    detail.minutes = { purpose: "내용없음", discussion: "내용없음", decisions: "내용없음", risks: "내용없음", nextAgenda: "내용없음", engine: null, updatedBy: null, updatedAt: null };
    await openDetail(page, MANAGER, detail);
    await expect(overview(page)).toContainText("아직 생성되지 않았습니다");
    await page.getByRole("button", { name: "항목 수정" }).click();
    await dialog(page).getByLabel("목적").fill("직접 채운 목적");
    await dialog(page).getByRole("button", { name: "항목 저장" }).click();
    await expect(dialog(page)).toBeHidden();
    await expect(overview(page)).toContainText("직접 채운 목적");
    await expect(overview(page)).not.toContainText("아직 생성되지 않았습니다");
    await expect(overview(page)).toContainText("직접 작성");
  });

  test("직권 수정 저장: 바뀐 항목만 전송·서버 응답 기준 안내·팝업 규칙", async ({ page }) => {
    const calls = await openDetail(page, MANAGER, baseDetail(), { fail: { minutes: [403, "회의록 항목은 이 회의록을 확정할 수 있는 사람만 수정할 수 있습니다"] } });
    const edit = page.getByRole("button", { name: "항목 수정" });
    await edit.click();
    const box = dialog(page);
    await expect(box.getByRole("button", { name: "취소" })).toBeFocused();
    await expect(box).toContainText("바뀐 항목만 저장");
    await expect(box).toContainText("\"내용없음\"으로 저장됩니다");
    await expect(box.getByRole("button", { name: "항목 저장" })).toBeDisabled(); // 바뀐 것이 없으면 저장 불가
    await expect(box.getByLabel("리스크")).toHaveValue(""); // "내용없음"은 빈 칸으로
    await box.getByLabel("목적").fill("새 목적");
    await expect(box.getByText("4자")).toBeVisible();
    await box.getByLabel("리스크").fill("납기 지연 우려");
    await box.getByRole("button", { name: "항목 저장" }).click();
    await expect(box.getByRole("alert")).toContainText("회의록 항목은 이 회의록을 확정할 수 있는 사람만 수정할 수 있습니다"); // 서버 문구 그대로
    await expect(box.getByLabel("목적")).toHaveValue("새 목적"); // 입력 유지
    await box.getByRole("button", { name: "항목 저장" }).click();
    await expect(box).toBeHidden();
    await expect(edit).toBeFocused(); // 연 버튼으로 포커스 복귀
    await expect(overview(page)).toContainText("새 목적");
    await expect(overview(page)).toContainText("납기 지연 우려");
    await expect(page.getByText("저장했습니다 · 바뀐 항목 목적, 리스크")).toBeVisible();
    expect(calls.bodies.minutes).toHaveLength(2);
    expect(JSON.parse(calls.bodies.minutes[1] as string)).toEqual({ purpose: "새 목적", risks: "납기 지연 우려" }); // 바뀐 항목만
  });

  test("Esc 로 닫으면 저장 요청 없음", async ({ page }) => {
    const calls = await openDetail(page, MANAGER, baseDetail());
    await page.getByRole("button", { name: "항목 수정" }).click();
    await dialog(page).getByLabel("목적").fill("바꾸다 만 내용");
    await page.keyboard.press("Escape");
    await expect(dialog(page)).toBeHidden();
    await expect(page.getByRole("button", { name: "항목 수정" })).toBeFocused();
    expect(calls.bodies.minutes).toBeUndefined();
  });
});

test.describe("v2 변경 이력", () => {
  const entry = (id: number, extra: Record<string, unknown> = {}) => ({
    id, targetType: "meeting", targetId: 41, kind: "직권 수정", kindCode: "minutes.overridden", batchId: null,
    before: { purpose: `이전 ${id}` }, after: { purpose: `이후 ${id}` }, changedBy: { id: 7, name: "한팀장" }, changedAt: "2026-10-05T03:00:00Z", ...extra,
  });

  test("이력 표시·묶음·20건 단위 쪽 나눔", async ({ page }) => {
    const batch = ["a", "b", "c"].map((n, i) =>
      entry(900 + i, { kind: "업무 갱신", kindCode: "item.upload_updated", batchId: "up1", targetType: "action_item", targetId: 101 + i, before: { title: `전 ${n}` }, after: { title: `후 ${n}` } }),
    );
    const singles = Array.from({ length: 45 }, (_, i) => entry(800 - i));
    await openDetail(page, MANAGER, baseDetail(), { history: [...batch, ...singles] }); // 46 단위(묶음 1 + 낱건 45)
    await chooseMenu(page, "변경 이력");
    const box = dialog(page);
    await expect(box).toContainText("변경 이력");
    await expect(box.getByRole("button", { name: "닫기" })).toBeFocused();
    await expect(box.getByLabel("같은 업로드 묶음")).toContainText("3건 한 묶음");
    await expect(box.getByLabel("같은 업로드 묶음").getByText("업무 갱신")).toHaveCount(3);
    await expect(box).toContainText("변경 전 전 a");
    await expect(box).toContainText("변경 후 후 a");
    await expect(box).toContainText("업무 #101");
    await expect(box).toContainText("한팀장");
    await expect(box.getByText("직권 수정", { exact: true })).toHaveCount(19); // 첫 쪽: 묶음 1 + 낱건 19
    await expect(box.getByLabel("변경 이력 쪽")).toContainText("1 / 3");
    await box.getByRole("button", { name: "다음" }).click();
    await expect(box.getByLabel("변경 이력 쪽")).toContainText("2 / 3");
    await expect(box.getByText("직권 수정", { exact: true })).toHaveCount(20);
    await box.getByRole("button", { name: "다음" }).click();
    await expect(box.getByText("직권 수정", { exact: true })).toHaveCount(6);
    await expect(box.getByRole("button", { name: "다음" })).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(dialog(page)).toBeHidden();
    await expect(more(page)).toBeFocused();
  });

  test("이력 없음·서버 거절 문구", async ({ page }) => {
    const calls = await openDetail(page, MANAGER, baseDetail(), { fail: { history: [403, "이력을 볼 수 없습니다"] } });
    await chooseMenu(page, "변경 이력");
    await expect(dialog(page).getByRole("alert")).toContainText("이력을 볼 수 없습니다");
    await dialog(page).getByRole("button", { name: "다시 시도" }).click();
    await expect(dialog(page)).toContainText("변경 이력이 없습니다");
    expect(calls.bodies.history).toHaveLength(2);
  });
});

test.describe("v2 엑셀 다운로드", () => {
  test("로그인 헤더로 받아 '{회의명}_회의록.xlsx' 로 저장(쓸 수 없는 문자 제거)", async ({ page }) => {
    const detail = baseDetail();
    detail.title = "주간/생산:현안?\"회의\"";
    await openDetail(page, MANAGER, detail);
    const [download] = await Promise.all([page.waitForEvent("download"), chooseMenu(page, "엑셀 다운로드")]);
    expect(download.suggestedFilename()).toBe("주간생산현안회의_회의록.xlsx");
    const path = await download.path();
    expect(require("fs").readFileSync(path, "utf8")).toBe("FAKE-XLSX-BYTES");
  });

  test("담당자도 받을 수 있다", async ({ page }) => {
    await openDetail(page, STAFF, baseDetail());
    const [download] = await Promise.all([page.waitForEvent("download"), chooseMenu(page, "엑셀 다운로드")]);
    expect(download.suggestedFilename()).toBe("주간 생산 현안 회의_회의록.xlsx");
  });

  test("실패하면 서버 문구 표시", async ({ page }) => {
    await openDetail(page, MANAGER, baseDetail(), { fail: { export: [404, "회의록을 찾을 수 없습니다"] } });
    await chooseMenu(page, "엑셀 다운로드");
    await expect(page.getByRole("alert").filter({ hasText: "엑셀을 내려받지 못했습니다 · 회의록을 찾을 수 없습니다" })).toBeVisible();
  });
});

test.describe("v2 수정 회의록 업로드", () => {
  const ambiguous = (choices: Record<string, number>) => {
    const chosen = choices["row:3"];
    return {
      ...emptyPreview(),
      canApply: Boolean(chosen), // 동명이인이 남아 있으면 서버도 canApply=false
      ambiguities: chosen
        ? []
        : [{ key: "row:3", name: "박동명", sheet: "업무", row: 3, candidates: [{ id: 12, name: "박동명", loginId: "dm1" }, { id: 13, name: "박동명", loginId: "dm2" }] }],
      warnings: [{ sheet: "업무", row: 4, message: "담당자 계정을 찾을 수 없어 담당자를 비워 둡니다(보완 필요)" }],
      items: {
        updates: [{ itemId: 101, row: 2, title: "견적서 송부", before: { title: "견적서 송부", dueDate: "2026-10-09" }, after: { title: "견적서 송부(수정)", dueDate: "2026-11-01" } }],
        unchanged: [], skipped: [{ itemId: 102, row: 3, title: "설비 점검", status: "confirmed", reason: "확정된 업무라 바꾸지 않았습니다" }],
        added: [{ row: 5, title: "파일로 추가한 업무", assigneeId: null, dueDate: null, dueUndetermined: false, evidence: "등록자 직권 지정" }],
      },
      participants: { before: ["이서연(es)"], after: ["이서연(es)", "홍길동(미등록)"], added: ["홍길동(미등록)"], removed: [{ accountId: 5, name: "정빠짐", loginId: "out", viewImpact: "열람 불가(참석자에서 빠져 볼 수 없게 됨)" }] },
      minutes: { purpose: { before: "내용없음", after: "새 목적" } },
    };
  };

  test("미리보기 → 동명이인 선택 필수 → 적용 → 갱신", async ({ page }) => {
    const detail = baseDetail();
    const calls = await openDetail(page, MANAGER, detail, { preview: ambiguous });
    await chooseMenu(page, "수정 회의록 업로드");
    const box = dialog(page);
    await expect(box.getByRole("button", { name: "취소" })).toBeFocused();
    await expect(box.getByRole("button", { name: "변경 적용" })).toBeDisabled(); // 미리보기 전
    await box.getByLabel("수정한 파일").setInputFiles(XLSX);
    await box.getByRole("button", { name: "미리보기" }).click();

    await expect(box.getByLabel("업무 변경")).toContainText("견적서 송부(수정)");
    await expect(box.getByLabel("업무 변경")).toContainText("기한: 2026-10-09 → 2026-11-01");
    await expect(box.getByLabel("건너뛴 업무")).toContainText("확정된 업무라 바꾸지 않았습니다");
    await expect(box.getByLabel("새 업무")).toContainText("등록자 직권 지정");
    await expect(box.getByLabel("참석자 변경")).toContainText("홍길동(미등록)");
    await expect(box.getByLabel("참석자 변경")).toContainText("열람 영향 열람 불가");
    await expect(box.getByLabel("회의록 항목 변경")).toContainText("새 목적");
    await expect(box.getByLabel("경고")).toContainText("담당자를 비워 둡니다");

    // 동명이인: 모두 선택해야 적용 가능
    await expect(box.getByLabel("동명이인 선택")).toContainText("남은 1건");
    await expect(box.getByRole("button", { name: "변경 적용" })).toBeDisabled();
    await box.getByLabel(/박동명/).selectOption("13");
    await expect(box.getByLabel("동명이인 선택")).toHaveCount(0); // 선택값으로 다시 미리보기
    await expect(box.getByRole("button", { name: "변경 적용" })).toBeEnabled();
    await box.getByRole("button", { name: "변경 적용" }).click();
    await expect(box).toBeHidden();
    await expect(more(page)).toBeFocused();
    await expect(page.getByText("수정 회의록을 적용했습니다 · 업무 1건 갱신, 1건 추가, 1건 건너뜀")).toBeVisible();
    await expect(row(page, "파일로 추가한 업무")).toContainText("수기"); // 상세를 새로 불러온다
    // 선택값은 choices 형식({키: 계정 ID})으로 보낸다
    const sent = (calls.bodies.apply as string[])[0];
    expect(/name="choices"\r\n\r\n([^\r]*)/.exec(sent)?.[1]).toBe('{"row:3":13}');
  });

  test("오류가 있으면 오류 목록만 보이고 적용할 수 없다", async ({ page }) => {
    await openDetail(page, MANAGER, baseDetail(), {
      preview: () => ({ ...emptyPreview(), canApply: false, errors: [{ sheet: "업무", row: 4, message: "업무 ID 999999 은(는) 이 회의록의 업무가 아닙니다" }, { sheet: "업무", row: 6, message: "기한 \"내일\" 은(는) 날짜(YYYY-MM-DD)나 \"미확정\"이 아닙니다" }] }),
    });
    await chooseMenu(page, "수정 회의록 업로드");
    const box = dialog(page);
    await box.getByLabel("수정한 파일").setInputFiles(XLSX);
    await box.getByRole("button", { name: "미리보기" }).click();
    await expect(box.getByLabel("파일 오류")).toContainText("오류 2건");
    await expect(box.getByLabel("파일 오류")).toContainText("업무 4행");
    await expect(box.getByLabel("파일 오류")).toContainText("업무 ID 999999");
    await expect(box.getByLabel("업무 변경")).toHaveCount(0);
    await expect(box.getByRole("button", { name: "변경 적용" })).toBeDisabled();
  });

  test("서버 거절(409)은 문구 그대로, 선택값 유지 / 파일 용량(413)·형식(400) 문구", async ({ page }) => {
    const calls = await openDetail(page, MANAGER, baseDetail(), {
      preview: ambiguous,
      fail: { apply: [409, "보류된 회의록은 변경할 수 없습니다"] },
    });
    await chooseMenu(page, "수정 회의록 업로드");
    const box = dialog(page);
    await box.getByLabel("수정한 파일").setInputFiles(XLSX);
    await box.getByRole("button", { name: "미리보기" }).click();
    await box.getByLabel(/박동명/).selectOption("12");
    await expect(box.getByRole("button", { name: "변경 적용" })).toBeEnabled();
    await box.getByRole("button", { name: "변경 적용" }).click();
    await expect(box.getByRole("alert")).toContainText("보류된 회의록은 변경할 수 없습니다");
    await expect(box.getByRole("button", { name: "변경 적용" })).toBeEnabled(); // 미리보기·선택값 유지, 다시 시도 가능
    await expect(box.getByLabel("업무 변경")).toBeVisible();
    await box.getByRole("button", { name: "변경 적용" }).click();
    await expect(box).toBeHidden();
    expect(calls.bodies.apply).toHaveLength(2);
  });

  test("미리보기에서 서버가 파일을 거절하면(413) 서버 문구만 표시", async ({ page }) => {
    await openDetail(page, MANAGER, baseDetail(), { fail: { preview: [413, "파일이 너무 큽니다(최대 2048KB)."] } });
    await chooseMenu(page, "수정 회의록 업로드");
    await dialog(page).getByLabel("수정한 파일").setInputFiles(XLSX);
    await dialog(page).getByRole("button", { name: "미리보기" }).click();
    await expect(dialog(page).getByRole("alert")).toContainText("파일이 너무 큽니다(최대 2048KB).");
    await expect(dialog(page).getByRole("button", { name: "변경 적용" })).toBeDisabled();
  });

  test("파일 선택 표시: 고르기 전 '선택한 파일 없음', 고른 뒤 이름·크기와 '파일 다시 선택'", async ({ page }) => {
    await openDetail(page, MANAGER, baseDetail());
    await chooseMenu(page, "수정 회의록 업로드");
    const box = dialog(page);
    const pick = box.getByRole("button", { name: "수정할 파일 선택" });
    await expect(pick).toBeVisible();
    await expect(box.getByText("선택한 파일 없음")).toBeVisible();
    await expect(box.getByRole("button", { name: "미리보기" })).toBeDisabled();
    // 브라우저 기본 입력은 화면에 보이지 않지만 접근성 이름("수정한 파일")으로 찾을 수 있고, 거기에 파일을 넣어도 동작한다
    const input = box.getByLabel("수정한 파일");
    await expect(input).toHaveClass(/sr-only/);
    await input.setInputFiles({ ...XLSX, name: "아주아주아주긴파일이름".repeat(8) + ".xlsx", buffer: Buffer.alloc(2048) });
    await expect(box.getByText("선택한 파일 없음")).toHaveCount(0);
    await expect(box.getByText("2.0 KB")).toBeVisible();
    await expect(box.getByTitle(/아주아주아주긴파일이름/)).toBeVisible();
    await expect(box.getByRole("button", { name: "파일 다시 선택" })).toBeVisible();
    await expect(box.getByRole("button", { name: "수정할 파일 선택" })).toHaveCount(0);
    await expect(box.getByRole("button", { name: "미리보기" })).toBeEnabled();
  });

  test("파일을 다시 고르면 이전 미리보기를 비우고 처음 단계로", async ({ page }) => {
    await openDetail(page, MANAGER, baseDetail(), { preview: ambiguous });
    await chooseMenu(page, "수정 회의록 업로드");
    const box = dialog(page);
    await box.getByLabel("수정한 파일").setInputFiles(XLSX);
    await box.getByText("4 B").waitFor();
    await box.getByRole("button", { name: "미리보기" }).click();
    await expect(box.getByLabel("업무 변경")).toBeVisible();
    await box.getByLabel("수정한 파일").setInputFiles({ ...XLSX, name: "다시.xlsx" });
    await expect(box.getByText("다시.xlsx")).toBeVisible();
    await expect(box.getByLabel("업무 변경")).toHaveCount(0);
    await expect(box.getByLabel("동명이인 선택")).toHaveCount(0);
    await expect(box.getByRole("button", { name: "변경 적용" })).toBeDisabled();
  });

  test("키보드(Enter·Space)로 파일 선택 버튼을 누르면 파일 선택창이 열리고 포커스 링이 보인다", async ({ page }) => {
    await openDetail(page, MANAGER, baseDetail());
    await chooseMenu(page, "수정 회의록 업로드");
    const pick = dialog(page).getByRole("button", { name: "수정할 파일 선택" });
    await pick.focus();
    await page.keyboard.press("Tab"); // 키보드로 이동해 와야 :focus-visible 링이 켜진다
    await page.keyboard.press("Shift+Tab");
    await expect(pick).toBeFocused();
    await expect(pick).toHaveCSS("box-shadow", /rgb/); // 포커스 링(box-shadow)
    for (const key of ["Enter", "Space"]) {
      const chooser = page.waitForEvent("filechooser");
      await page.keyboard.press(key);
      await (await chooser).setFiles(XLSX);
      await expect(dialog(page).getByText("4 B")).toBeVisible();
      await dialog(page).getByRole("button", { name: "파일 다시 선택" }).focus();
    }
  });

  test("Esc 로 닫으면 적용 요청 없음", async ({ page }) => {
    const calls = await openDetail(page, MANAGER, baseDetail());
    await chooseMenu(page, "수정 회의록 업로드");
    await page.keyboard.press("Escape");
    await expect(dialog(page)).toBeHidden();
    await expect(more(page)).toBeFocused();
    expect(calls.bodies.apply).toBeUndefined();
  });
});

test.describe("v2 수기 업무 등록과 표시", () => {
  test("업무 추가: 필수 업무명·담당자·기한 → 등록 → '수기' 배지와 직권 지정 근거", async ({ page }) => {
    const calls = await openDetail(page, MANAGER, baseDetail());
    const add = page.getByRole("button", { name: "업무 추가" });
    await add.click();
    const box = dialog(page);
    await expect(box.getByRole("button", { name: "취소" })).toBeFocused();
    await expect(box).toContainText("근거는 \"등록자 직권 지정\"으로 자동 표시됩니다");
    await expect(box.getByRole("button", { name: "업무 등록" })).toBeDisabled(); // 업무명 필수
    await box.getByLabel(/업무명/).fill("  현장 사진 정리  ");
    await box.getByLabel(/담당자/).selectOption("9");
    await box.getByLabel("기한", { exact: true }).fill("2026-11-30");
    await box.getByRole("button", { name: "업무 등록" }).click();
    await expect(box).toBeHidden();
    await expect(add).toBeFocused();
    expect(JSON.parse(calls.bodies.manual[0] as string)).toEqual({ title: "현장 사진 정리", assigneeId: 9, dueDate: "2026-11-30" });
    const created = row(page, "현장 사진 정리");
    await expect(created).toContainText("수기"); // 색이 아니라 글자 배지
    await expect(created).toContainText("등록자 직권 지정"); // 직권 지정 근거
    await expect(row(page, "견적서 송부")).not.toContainText("수기");
  });

  test("기한 '미확정' 선택, 서버 오류 문구 그대로·입력 유지", async ({ page }) => {
    const calls = await openDetail(page, MANAGER, baseDetail(), { fail: { manual: [409, "보류된 회의록에는 업무를 추가할 수 없습니다"] } });
    await page.getByRole("button", { name: "업무 추가" }).click();
    const box = dialog(page);
    await box.getByLabel(/업무명/).fill("미정 기한 업무");
    await box.getByLabel("미확정").check();
    await expect(box.getByLabel("기한", { exact: true })).toBeDisabled();
    await box.getByRole("button", { name: "업무 등록" }).click();
    await expect(box.getByRole("alert")).toContainText("보류된 회의록에는 업무를 추가할 수 없습니다");
    await expect(box.getByLabel(/업무명/)).toHaveValue("미정 기한 업무");
    await box.getByRole("button", { name: "업무 등록" }).click();
    await expect(box).toBeHidden();
    expect(JSON.parse(calls.bodies.manual[1] as string)).toEqual({ title: "미정 기한 업무", dueUndetermined: true });
  });

  test("미등록 참석자는 '이름(미등록)'으로 함께 표시", async ({ page }) => {
    const detail = baseDetail();
    detail.guestParticipants = ["홍길동"];
    await openDetail(page, MANAGER, detail);
    await expect(overview(page)).toContainText("이서연, 홍길동(미등록)");
  });

  test("담당자에게는 관리자용 버튼이 없고, 다운로드·이력 메뉴만", async ({ page }) => {
    await openDetail(page, STAFF, baseDetail());
    await expect(page.getByRole("button", { name: "업무 추가" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "항목 수정" })).toHaveCount(0);
    await more(page).click();
    await expect(menuItem(page, "엑셀 다운로드")).toBeVisible();
    await expect(menuItem(page, "변경 이력")).toBeVisible();
    await expect(menuItem(page, "수정 회의록 업로드")).toHaveCount(0);
  });
});
