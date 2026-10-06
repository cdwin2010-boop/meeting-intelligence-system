/*
 * v2 E2E 공용 test: 왼쪽 메뉴 "처리 현황"이 모든 화면에서 GET /api/me/processing 을 부르므로, 기본 응답(빈 목록)을 먼저 깔아 둔다.
 * 테스트가 같은 주소를 다시 가로채면 그쪽이 우선한다(나중에 등록한 가로채기가 먼저 적용된다).
 */
import { test as base, expect } from "@playwright/test";

export { expect };
export type { Page, Route } from "@playwright/test";

export const test = base.extend({
  page: async ({ page }, use) => {
    await page.route("**/api/me/processing", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
    await use(page);
  },
});
