/*
 * 작업 88-2 화면 스크린샷(375px): Wi-Fi 안내 팝업, 보류 목록, 업로드 완료 안내, 대기 상태, [즉시 변환] 표시.
 * 저장 위치: test-results/design-88-2/ (.gitignore 대상, 커밋하지 않음). 데이터는 모두 가상이다.
 */
import { expect, test } from "./helpers/test";
import { fillAudioForm, horizontalOverflow, openMobileUpload, queuedDetail, submitButton } from "./helpers/queue-mock";

const DIR = "test-results/design-88-2";

test("375px 스크린샷: 팝업 → 보류 목록 → 업로드 완료 안내 → 대기·즉시 변환", async ({ page }) => {
  await openMobileUpload(page, { connection: "cellular", detail: queuedDetail() });
  await fillAudioForm(page);
  await submitButton(page).click();
  const dialog = page.getByRole("dialog", { name: "Wi-Fi 연결 확인" });
  await expect(dialog).toBeVisible();
  await page.screenshot({ path: `${DIR}/01-wifi-popup.png` });

  await dialog.getByRole("button", { name: "등록 보류" }).click();
  await expect(page.getByRole("heading", { name: /등록 보류 목록 \(1건\)/ })).toBeVisible();
  await page.getByRole("region", { name: "등록 보류 목록" }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${DIR}/02-pending-list.png`, fullPage: true });

  await page.getByRole("button", { name: /지금 올리기/ }).click();
  await page.getByRole("dialog", { name: "Wi-Fi 연결 확인" }).getByRole("button", { name: "업로드 계속" }).click();
  await expect(page.getByRole("region", { name: "업로드 안내" })).toBeVisible();
  await page.screenshot({ path: `${DIR}/03-upload-accepted.png`, fullPage: true });

  await expect(page.getByRole("status", { name: "처리 상태" }).getByRole("button", { name: "즉시 변환" })).toBeVisible();
  await expect(page.getByRole("status", { name: "처리 상태" })).toContainText("앞에 2건");
  await page.screenshot({ path: `${DIR}/04-queued-process-now.png`, fullPage: true });
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
});
