/*
 * 작업 88-2 E2E: 모바일 올리기 Wi-Fi 안내 팝업, 등록 보류 목록(IndexedDB), 업로드 성공 안내.
 * navigator.connection 은 addInitScript 로 주입한다. 백엔드 없이 page.route 로 응답한다(데이터는 모두 가상).
 * 보류 저장소 유틸(IndexedDB 래퍼)은 프로젝트에 브라우저 단위 테스트 환경이 없어 이 E2E 로 검증한다(순수 판정·한도 계산은 node --test).
 */
import { expect, test, type Page } from "./helpers/test";
import { AUDIO, fillAudioForm, horizontalOverflow, openMobileUpload, submitButton } from "./helpers/queue-mock";

const NOTICE_TEXT = "Wi-Fi 환경에서 등록하셔야 통신 요금이 발생하지 않습니다. 지금 바로 업로드하시겠습니까?";
const ACCEPTED_TEXT = "녹취 파일이 정상적으로 등록되었습니다. 변환 작업 대기열에 등록되었으며, 순차적으로 변환이 진행됩니다.";
const dialog = (page: Page) => page.getByRole("dialog", { name: "Wi-Fi 연결 확인" });
const MB = 1024 * 1024;

/** 보류 저장소에 항목을 직접 넣는다(한도 시험용). size 는 메타데이터 값이라 실제 Blob 은 작다 */
async function seedPending(page: Page, count: number, sizeBytes: number, accountId = 7) {
  await page.evaluate(
    async ([n, size, account]) => {
      const db: IDBDatabase = await new Promise((resolve, reject) => {
        const open = indexedDB.open("mi-v2-pending-uploads", 1);
        open.onupgradeneeded = () => open.result.createObjectStore("uploads", { keyPath: "id" });
        open.onsuccess = () => resolve(open.result);
        open.onerror = () => reject(open.error);
      });
      await new Promise<void>((resolve, reject) => {
        const tx = db.transaction("uploads", "readwrite");
        for (let i = 0; i < n; i += 1) {
          tx.objectStore("uploads").put({
            id: `seed-${account}-${i}`, accountId: account, fileName: `보류${i + 1}.m4a`, size, mimeType: "audio/mp4", sourceKind: "audio",
            meetingType: "regular", title: `보류 회의 ${i + 1}`, heldAt: "2026-10-01T10:00:00+09:00", participantIds: [], projectId: null,
            pendingAt: new Date(Date.UTC(2026, 9, 1, 1, i)).toISOString(), blob: new Blob(["x"], { type: "audio/mp4" }),
          });
        }
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
      db.close();
    },
    [count, sizeBytes, accountId] as const,
  );
}

const pendingCount = (page: Page) =>
  page.evaluate(
    () =>
      new Promise<number>((resolve) => {
        const open = indexedDB.open("mi-v2-pending-uploads", 1);
        open.onupgradeneeded = () => open.result.createObjectStore("uploads", { keyPath: "id" });
        open.onsuccess = () => {
          const db = open.result;
          const request = db.transaction("uploads", "readonly").objectStore("uploads").count();
          request.onsuccess = () => {
            db.close();
            resolve(request.result);
          };
        };
      }),
  );

// ---------------- 네트워크 판정 ----------------
for (const connection of ["wifi", "ethernet"]) {
  test(`${connection}: 안내 팝업 없이 바로 올리고 상세로 이동 + 대기열 등록 안내`, async ({ page }) => {
    const uploads: string[] = [];
    await openMobileUpload(page, { connection, uploads });
    await fillAudioForm(page);
    await submitButton(page).click();
    await expect(dialog(page)).toHaveCount(0);
    await expect.poll(() => uploads.length).toBe(1);
    await expect(page).toHaveURL(/\/v2\/meetings\/41$/);
    // 업로드 성공 직후 문구(고정) — 한 번만 보인다
    await expect(page.getByRole("region", { name: "업로드 안내" })).toContainText(ACCEPTED_TEXT);
    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: "주간 생산 현안 회의" })).toBeVisible();
    await expect(page.getByRole("region", { name: "업로드 안내" })).toHaveCount(0);
  });
}

for (const [label, connection] of [["cellular", "cellular"], ["미정의(connection 없음)", undefined], ["unknown", "unknown"]] as const) {
  test(`${label}: 올리기 직전 안내 팝업(고정 문구·두 버튼, [등록 보류]에 먼저 포커스)`, async ({ page }) => {
    const uploads: string[] = [];
    await openMobileUpload(page, { connection, uploads });
    await fillAudioForm(page);
    await submitButton(page).click();
    await expect(dialog(page)).toBeVisible();
    await expect(dialog(page)).toContainText(NOTICE_TEXT);
    await expect(dialog(page).getByRole("button", { name: "업로드 계속" })).toBeVisible();
    await expect(dialog(page).getByRole("button", { name: "등록 보류" })).toBeFocused();
    expect(uploads).toHaveLength(0); // 선택하기 전에는 올리지 않는다
    await expect.poll(() => horizontalOverflow(page)).toBeLessThanOrEqual(0);
  });
}

test("[업로드 계속]: 즉시 올리고 상세로 이동", async ({ page }) => {
  const uploads: string[] = [];
  await openMobileUpload(page, { connection: "cellular", uploads });
  await fillAudioForm(page);
  await submitButton(page).click();
  await dialog(page).getByRole("button", { name: "업로드 계속" }).click();
  await expect.poll(() => uploads.length).toBe(1);
  expect(uploads[0]).toContain(AUDIO.name);
  await expect(page).toHaveURL(/\/v2\/meetings\/41$/);
});

test("Esc: 팝업만 닫히고 올리지도 보류하지도 않는다(입력은 그대로)", async ({ page }) => {
  const uploads: string[] = [];
  await openMobileUpload(page, { connection: "cellular", uploads });
  await fillAudioForm(page);
  await submitButton(page).click();
  await expect(dialog(page)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog(page)).toHaveCount(0);
  expect(uploads).toHaveLength(0);
  expect(await pendingCount(page)).toBe(0);
  await expect(page.getByText(AUDIO.name)).toBeVisible();
  await expect(submitButton(page)).toBeFocused(); // 연 버튼으로 포커스 복귀
});

// ---------------- 등록 보류 ----------------
test("[등록 보류]: 업로드 없이 보류 목록에 저장(파일명·크기·보류 시각), 입력칸은 비워진다", async ({ page }) => {
  const uploads: string[] = [];
  await openMobileUpload(page, { connection: "cellular", uploads });
  await fillAudioForm(page);
  await submitButton(page).click();
  await dialog(page).getByRole("button", { name: "등록 보류" }).click();
  await expect(dialog(page)).toHaveCount(0);
  expect(uploads).toHaveLength(0);
  const list = page.getByRole("region", { name: "등록 보류 목록" });
  await expect(list.getByRole("heading", { name: /등록 보류 목록 \(1건\)/ })).toBeVisible();
  await expect(list).toContainText(AUDIO.name);
  await expect(list.getByText(/보류 \d{4}-\d{2}-\d{2}/)).toBeVisible();
  await expect(list.getByRole("button", { name: `${AUDIO.name} 지금 올리기` })).toBeVisible();
  await expect(list.getByRole("button", { name: `${AUDIO.name} 삭제` })).toBeVisible();
  expect(await pendingCount(page)).toBe(1);
  await expect(page.getByText("선택한 파일 없음")).toBeVisible();
  // 새로고침해도 기기에 남아 있다(서버가 아니라 이 기기 저장소)
  await page.reload();
  await expect(page.getByRole("heading", { name: /등록 보류 목록 \(1건\)/ })).toBeVisible();
  await expect.poll(() => horizontalOverflow(page)).toBeLessThanOrEqual(0);
});

test("건수 상한: 5건이 있으면 6번째 보류는 저장하지 않고 한도 안내", async ({ page }) => {
  await openMobileUpload(page, { connection: "cellular" });
  await seedPending(page, 5, 1024);
  await page.reload();
  await expect(page.getByRole("heading", { name: /등록 보류 목록 \(5건\)/ })).toBeVisible();
  await fillAudioForm(page);
  await submitButton(page).click();
  await dialog(page).getByRole("button", { name: "등록 보류" }).click();
  await expect(dialog(page)).toContainText("보류는 최대 5건까지 저장할 수 있습니다");
  expect(await pendingCount(page)).toBe(5);
  await expect(dialog(page).getByRole("button", { name: "업로드 계속" })).toBeVisible(); // 계속 올리기는 여전히 가능
});

test("용량 상한: 합계가 300MB 를 넘으면 저장하지 않고 어떤 한도인지 안내", async ({ page }) => {
  await openMobileUpload(page, { connection: "cellular" });
  await seedPending(page, 1, 300 * MB); // 이미 상한까지 찼다: 어떤 파일이든 넘는다
  await page.reload();
  await fillAudioForm(page);
  await submitButton(page).click();
  await dialog(page).getByRole("button", { name: "등록 보류" }).click();
  await expect(dialog(page)).toContainText("보류 파일 합계가 한도 300MB를 넘습니다");
  expect(await pendingCount(page)).toBe(1);
});

test("저장소를 못 쓰는 환경: [등록 보류]가 막히고 안내, [업로드 계속]은 가능", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, "indexedDB", { configurable: true, get: () => undefined });
  });
  await openMobileUpload(page, { connection: "cellular" });
  await fillAudioForm(page);
  await submitButton(page).click();
  await expect(dialog(page)).toContainText("이 기기에서는 보류할 수 없습니다. Wi-Fi에서 다시 시도해 주세요.");
  await expect(dialog(page).getByRole("button", { name: "등록 보류" })).toBeDisabled();
  await expect(dialog(page).getByRole("button", { name: "업로드 계속" })).toBeEnabled();
  await expect(page.getByRole("region", { name: "등록 보류 목록" })).toHaveCount(0);
});

test("삭제: 확인 팝업(보류 파일 삭제, 취소에 먼저 포커스) 뒤에만 지워지고, 취소하면 그대로", async ({ page }) => {
  await openMobileUpload(page, { connection: "cellular" });
  await seedPending(page, 2, 2048);
  await page.reload();
  const list = page.getByRole("region", { name: "등록 보류 목록" });
  await expect(list.getByRole("heading", { name: /\(2건\)/ })).toBeVisible();
  await list.getByRole("button", { name: "보류1.m4a 삭제" }).click();
  const confirm = page.getByRole("alertdialog", { name: "보류 파일 삭제" });
  await expect(confirm).toContainText("기기에 임시 저장된 파일만 지워집니다");
  await expect(confirm.getByRole("button", { name: "취소" })).toBeFocused();
  await confirm.getByRole("button", { name: "취소" }).click();
  expect(await pendingCount(page)).toBe(2);
  await list.getByRole("button", { name: "보류1.m4a 삭제" }).click();
  await confirm.getByRole("button", { name: "보류 파일 삭제" }).click();
  await expect(list.getByRole("heading", { name: /\(1건\)/ })).toBeVisible();
  await expect(list).not.toContainText("보류1.m4a");
  expect(await pendingCount(page)).toBe(1);
});

test("[지금 올리기]: 실패하면 목록에 남고 사유가 보이며, 다시 눌러 성공하면 목록에서 사라진다(자동 재업로드 없음)", async ({ page }) => {
  const uploads: string[] = [];
  await openMobileUpload(page, { connection: "wifi", uploadStatus: 500, uploads });
  await seedPending(page, 1, 2048);
  await page.reload();
  const list = page.getByRole("region", { name: "등록 보류 목록" });
  await page.waitForTimeout(500);
  expect(uploads).toHaveLength(0); // 보류 항목은 저절로 올라가지 않는다
  await list.getByRole("button", { name: "보류1.m4a 지금 올리기" }).click();
  await expect(list).toContainText("올리지 못했습니다 · 서버가 지금 처리할 수 없습니다");
  expect(uploads).toHaveLength(1);
  expect(await pendingCount(page)).toBe(1);
  // 서버가 회복됐다고 보고 성공 응답으로 바꾼다(나중에 등록한 라우트가 먼저 적용된다)
  await page.route("**/api/meetings/upload", (route) => route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ meetingId: 41, jobId: 77 }) }));
  await list.getByRole("button", { name: "보류1.m4a 지금 올리기" }).click();
  await expect(page).toHaveURL(/\/v2\/meetings\/41$/);
  expect(await pendingCount(page)).toBe(0);
});

test("목록의 [지금 올리기]도 같은 안내 판정을 거친다: 셀룰러면 팝업, [등록 보류]는 항목을 목록에 그대로 둔다", async ({ page }) => {
  const uploads: string[] = [];
  await openMobileUpload(page, { connection: "cellular", uploads });
  await seedPending(page, 1, 2048);
  await page.reload();
  await page.getByRole("region", { name: "등록 보류 목록" }).getByRole("button", { name: "보류1.m4a 지금 올리기" }).click();
  await expect(dialog(page)).toContainText(NOTICE_TEXT);
  await dialog(page).getByRole("button", { name: "등록 보류" }).click();
  await expect(dialog(page)).toHaveCount(0);
  expect(uploads).toHaveLength(0);
  expect(await pendingCount(page)).toBe(1); // 중복 저장 없음
});

// ---------------- PC·접근성 ----------------
test("PC(1280px): 셀룰러로 판정돼도 팝업·보류 목록이 없고 기존처럼 바로 올린다", async ({ page }) => {
  const uploads: string[] = [];
  await openMobileUpload(page, { width: 1280, connection: "cellular", uploads });
  await fillAudioForm(page);
  await submitButton(page).click();
  await expect(dialog(page)).toHaveCount(0);
  await expect.poll(() => uploads.length).toBe(1);
  await expect(page).toHaveURL(/\/v2\/meetings\/41$/);
});

test("PC(1280px): 보류 목록 영역이 그려지지 않는다", async ({ page }) => {
  await openMobileUpload(page, { width: 1280, connection: "cellular" });
  await seedPending(page, 1, 2048);
  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: "회의록 올리기" })).toBeVisible();
  await expect(page.getByRole("region", { name: "등록 보류 목록" })).toHaveCount(0);
});

test("375px: 팝업 접근성 속성과 보류 목록 영역에서 가로 넘침이 없다", async ({ page }) => {
  await openMobileUpload(page, { connection: "cellular" });
  await seedPending(page, 3, 5 * MB);
  await page.reload();
  await expect(page.getByRole("heading", { name: /\(3건\)/ })).toBeVisible();
  await expect.poll(() => horizontalOverflow(page)).toBeLessThanOrEqual(0);
  await fillAudioForm(page);
  await submitButton(page).click();
  const box = dialog(page);
  await expect(box).toHaveAttribute("aria-modal", "true");
  await expect(box).toHaveAccessibleName("Wi-Fi 연결 확인");
  await expect(box).toHaveAccessibleDescription(NOTICE_TEXT);
  // Tab 포커스 트랩: 팝업 안 버튼 사이에서만 돈다
  for (let i = 0; i < 4; i += 1) {
    await page.keyboard.press("Tab");
    expect(await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'))).toBe(true);
  }
  await expect.poll(() => horizontalOverflow(page)).toBeLessThanOrEqual(0);
});
