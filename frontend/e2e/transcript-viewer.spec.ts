/*
 * 전사 원문 창 E2E (v1.9.9) — 목 모드 전용. 업로드·화자 이름은 테스트 브라우저의 메모리(목 저장소)에만 생기고
 * 실서버·백엔드 DB에는 아무것도 등록·삭제·초기화하지 않는다.
 *
 * 준비: /upload 에서 제목에 "#speakers" 를 넣어 목 업로드 → 약 8초 뒤 "결과 보기"(화면 이동은 클라이언트 전환이라 목 메모리 유지)
 *       → 화자1 이름을 저장 → "전사 원문 보기".
 */
import { readFile } from "node:fs/promises";
import { expect, test, type Locator, type Page } from "@playwright/test";

const SPEAKER1_NAME = "권영우 부장";
const MIN_BODY = { width: 288, height: 256 }; // TranscriptViewer 의 최소 본문 크기
// 창 테두리(1px×2)·안쪽 여백(24px×2), 세로는 제목(28px)·간격(8px)까지 → 창 최소 크기 = 본문 최소 + 이 값
const CHROME = { width: 50, height: 86 };
const MARGIN = 16; // 창과 화면 가장자리 사이 최소 여백

type Box = { x: number; y: number; width: number; height: number };
const edges = (b: Box) => ({ left: b.x, top: b.y, right: b.x + b.width, bottom: b.y + b.height });

/** 목 업로드(#speakers) → 결과 화면 → 화자1 이름 저장까지 */
async function openSpeakersMeeting(page: Page): Promise<void> {
  await page.goto("/upload");
  await page.getByLabel("음성 파일").setInputFiles({ name: "e2e.mp3", mimeType: "audio/mpeg", buffer: Buffer.from("ID3-e2e") });
  await page.getByLabel("회의 제목").fill("E2E 화자 이름 #speakers");
  await page.getByLabel("회의 일시 (KST)").fill("2026-09-30T10:00");
  await page.getByRole("button", { name: "Upload" }).click();
  await page.getByRole("link", { name: "결과 보기" }).click({ timeout: 30_000 });
  await expect(page.getByRole("heading", { name: "E2E 화자 이름 #speakers" })).toBeVisible();

  await page.getByLabel("화자1").fill(SPEAKER1_NAME);
  await page.getByRole("button", { name: "화자 이름 저장" }).click();
  await expect(page.getByText("저장했습니다")).toBeVisible();
}

async function openViewer(page: Page): Promise<Locator> {
  await page.getByRole("button", { name: "전사 원문 보기" }).click();
  const dialog = page.getByRole("dialog", { name: "전사 원문" });
  await expect(dialog).toBeVisible();
  return dialog;
}

async function box(locator: Locator): Promise<Box> {
  const b = await locator.boundingBox();
  if (!b) throw new Error("요소가 화면에 없습니다");
  return b;
}

/** data-edge 손잡이의 가운데를 잡고 (dx, dy)만큼 끈다 */
async function drag(page: Page, dialog: Locator, edge: string, dx: number, dy: number): Promise<void> {
  const handle = await box(dialog.locator(`[data-edge="${edge}"]`));
  const x = handle.x + handle.width / 2;
  const y = handle.y + handle.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dx / 2, y + dy / 2, { steps: 4 });
  await page.mouse.move(x + dx, y + dy, { steps: 4 });
  await page.mouse.up();
}

test.describe("전사 원문 창", () => {
  test.beforeEach(async ({ page }) => {
    await openSpeakersMeeting(page);
  });

  test("다운로드 버튼 2개가 창 맨 위에 있고, 원본은 화자N · 이름 적용본은 지정한 이름", async ({ page }) => {
    const dialog = await openViewer(page);
    const original = dialog.getByRole("button", { name: "원본 다운로드 (.txt 파일)" });
    const named = dialog.getByRole("button", { name: "이름 적용 다운로드 (.txt 파일)" });
    await expect(original).toBeVisible();
    await expect(named).toBeEnabled();

    // "맨 위": 두 버튼이 원문 상자보다 위에 있다
    const region = await box(dialog.getByRole("region", { name: "전사 원문" }));
    expect((await box(original)).y).toBeLessThan(region.y);
    expect((await box(named)).y).toBeLessThan(region.y);
    // 창 표시에도 이름이 적용된다
    await expect(dialog.getByRole("region", { name: "전사 원문" })).toContainText(`${SPEAKER1_NAME}:`);

    const [originalFile] = await Promise.all([page.waitForEvent("download"), original.click()]);
    expect(originalFile.suggestedFilename()).toBe("E2E 화자 이름 #speakers_전사원문.txt");
    const originalText = await readFile(await originalFile.path(), "utf8");
    expect(originalText.charCodeAt(0)).toBe(0xfeff); // BOM
    expect(originalText).toContain("화자1:");
    expect(originalText).not.toContain(SPEAKER1_NAME);

    const [namedFile] = await Promise.all([page.waitForEvent("download"), named.click()]);
    expect(namedFile.suggestedFilename()).toBe("E2E 화자 이름 #speakers_전사원문_이름적용.txt");
    const namedText = await readFile(await namedFile.path(), "utf8");
    expect(namedText.charCodeAt(0)).toBe(0xfeff);
    expect(namedText).toContain(`${SPEAKER1_NAME}:`);
    expect(namedText).not.toContain("화자1:");
    expect(namedText).toContain("화자2:"); // 이름을 지정하지 않은 화자는 그대로
    expect(namedText.split("\n").length).toBe(originalText.split("\n").length); // 줄 구성은 원본과 같다
  });

  test("근거 인용 화자가 저장한 이름으로 보인다", async ({ page }) => {
    // 첫 행(근거: 화자1 발화)을 펼치면 인용 화자가 이름으로, 담당자(화자2)는 이름이 없어 그대로
    await page.locator("tbody button[aria-expanded]").first().click();
    const caption = page.locator("figcaption").first();
    await expect(caption).toContainText(SPEAKER1_NAME);
    await expect(caption).not.toContainText("화자1");
    await expect(page.locator("tbody tr").first()).toContainText("화자2");
  });

  test("네 변·네 모서리를 끌면 크기가 바뀌고 반대쪽 변은 고정", async ({ page }) => {
    const dialog = await openViewer(page);
    const start = await box(dialog);
    expect(Math.round(start.width)).toBe(448); // 처음 크기: 기존 창 폭 28rem 그대로

    const cases: { edge: string; dx: number; dy: number; moved: ("left" | "right" | "top" | "bottom")[] }[] = [
      { edge: "right", dx: 80, dy: 0, moved: ["right"] },
      { edge: "left", dx: -60, dy: 0, moved: ["left"] },
      { edge: "bottom", dx: 0, dy: 40, moved: ["bottom"] },
      { edge: "top", dx: 0, dy: -30, moved: ["top"] },
      { edge: "bottomright", dx: -30, dy: -20, moved: ["right", "bottom"] },
      { edge: "topleft", dx: 25, dy: 15, moved: ["left", "top"] },
      { edge: "topright", dx: 20, dy: -10, moved: ["right", "top"] },
      { edge: "bottomleft", dx: -20, dy: 10, moved: ["left", "bottom"] },
    ];
    for (const c of cases) {
      const before = edges(await box(dialog));
      await drag(page, dialog, c.edge, c.dx, c.dy);
      const after = edges(await box(dialog));
      for (const side of ["left", "right", "top", "bottom"] as const) {
        const delta = side === "left" || side === "right" ? c.dx : c.dy;
        const expected = c.moved.includes(side) ? before[side] + delta : before[side];
        expect(after[side], `${c.edge} 끌기: ${side} 변`).toBeCloseTo(expected, 0);
      }
    }
    // 버튼과 닫기는 계속 보인다
    await expect(dialog.getByRole("button", { name: "원본 다운로드 (.txt 파일)" })).toBeInViewport();
    await expect(dialog.getByRole("button", { name: "닫기" })).toBeInViewport();
  });

  test("최소 크기 이하·화면 밖으로는 줄거나 나가지 않는다", async ({ page }) => {
    const dialog = await openViewer(page);
    const viewport = page.viewportSize()!;
    const region = dialog.getByRole("region", { name: "전사 원문" });

    // 왼쪽 변을 오른쪽 끝까지: 최소 폭에서 멈추고 오른쪽 변은 그대로
    const before = edges(await box(dialog));
    await drag(page, dialog, "left", 1000, 0);
    const shrunk = edges(await box(dialog));
    expect(shrunk.right).toBeCloseTo(before.right, 0);
    expect(shrunk.right - shrunk.left).toBeCloseTo(MIN_BODY.width + CHROME.width, 0);

    // 아래 변을 위로 끝까지: 최소 높이에서 멈추고 위 변은 그대로, 버튼은 잘리지 않음
    await drag(page, dialog, "bottom", 0, -2000);
    const short = edges(await box(dialog));
    expect(short.top).toBeCloseTo(shrunk.top, 0);
    expect(short.bottom - short.top).toBeCloseTo(MIN_BODY.height + CHROME.height, 0);
    await expect(dialog.getByRole("button", { name: "닫기" })).toBeInViewport({ ratio: 1 });
    await expect(dialog.getByRole("button", { name: "원본 다운로드 (.txt 파일)" })).toBeInViewport({ ratio: 1 });
    expect((await box(region)).height).toBeGreaterThan(0);

    // 모서리를 화면 밖으로: 여백 안에서 멈춘다
    await drag(page, dialog, "bottomright", 3000, 3000);
    await drag(page, dialog, "topleft", -3000, -3000);
    const big = edges(await box(dialog));
    expect(big.left).toBeGreaterThanOrEqual(MARGIN - 1);
    expect(big.top).toBeGreaterThanOrEqual(MARGIN - 1);
    expect(big.right).toBeLessThanOrEqual(viewport.width - MARGIN + 1);
    expect(big.bottom).toBeLessThanOrEqual(viewport.height - MARGIN + 1);
    await expect(dialog.getByRole("button", { name: "닫기" })).toBeInViewport({ ratio: 1 });
  });

  test("Esc·바깥 클릭으로 닫히고, 닫히면 포커스가 '전사 원문 보기'로 돌아온다", async ({ page }) => {
    const opener = page.getByRole("button", { name: "전사 원문 보기" });

    let dialog = await openViewer(page);
    await expect(dialog.getByRole("button", { name: "닫기" })).toBeFocused(); // 처음 포커스는 닫기
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();

    dialog = await openViewer(page);
    // 끌기를 바깥에서 놓아도 닫히지 않는다
    await drag(page, dialog, "right", 2000, 0);
    await expect(dialog).toBeVisible();
    await page.mouse.click(5, 5); // 배경(바깥) 클릭
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
  });
});
