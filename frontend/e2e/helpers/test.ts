/*
 * v2 E2E 공용 test (작업 73-2 보강).
 * 1) 가로채지 않은 /api/* 요청의 기본 응답: 테스트가 가로채지 않은 요청은 실제 백엔드(기본 8001)로 나가서, 백엔드가 꺼져 있으면
 *    콘솔에 ERR_CONNECTION_REFUSED 가 남고 켜져 있으면 실제 데이터에 기대어 통과하는 문제가 있었다. 가장 낮은 우선순위의 기본 응답을 먼저 깔아
 *    둔다(나중에 등록한 가로채기가 먼저 적용되므로 각 테스트가 가로챈 라우트가 우선한다). 기본 응답은 경로에 맞는 최소 유효 응답이다
 *    (알려진 경로는 모양에 맞는 빈 값, 모르는 경로는 GET 이면 빈 목록, 그 밖에는 빈 객체). 오류 상태로 답하지 않는다.
 *    E2E_LOG_UNINTERCEPTED=파일경로 를 주면 기본 응답까지 내려온(가로채지 않은) 경로를 그 파일에 한 줄씩 남긴다.
 * 2) 가드: 어떤 /api/* 요청이든 실제 네트워크로 나가면(응답의 서버 주소가 있음) 테스트를 실패시킨다.
 * 3) 왼쪽 메뉴 "처리 현황"이 모든 화면에서 GET /api/me/processing 을 부르므로, 기본 응답(빈 목록)을 먼저 깔아 둔다.
 */
import { appendFileSync } from "node:fs";

import { test as base, expect, type Route } from "@playwright/test";

export { expect };
export type { Page, Route } from "@playwright/test";

const EMPTY_TODOS = {
  awaitingConfirmMeetings: { total: 0, items: [] },
  needsCompletionItems: { total: 0, items: [] },
  myItems: { total: 0, items: [] },
  unreadAutoConfirmed: { total: 0, items: [] },
  pendingChangeRequests: { total: 0, items: [] },
};

/** 알려진 경로의 최소 유효 응답. 없으면 null */
function knownBody(method: string, path: string): unknown | null {
  if (method !== "GET") return null;
  if (path === "/me/todos") return EMPTY_TODOS;
  // 재생 주소는 빈 목록이 아니라 모양에 맞는 값을 준다(url 이 없으면 주소가 /apiundefined 로 만들어져 연결 오류가 난다)
  const audioUrl = /^\/meetings\/(\d+)\/audio-url$/.exec(path);
  if (audioUrl) return { url: `/meetings/${audioUrl[1]}/audio?token=e2e`, expiresInSec: 600 };
  if (path === "/system/engine") return { engine: "gemini", isFake: false };
  if (path === "/meetings") return { items: [], total: 0, page: 1, size: 20, availablePhases: ["active"] };
  if (/^\/projects\/\d+\/meetings$/.test(path)) return { items: [], total: 0, page: 1, size: 20, availablePhases: ["active"] };
  return null;
}

async function fallbackApi(route: Route) {
  const request = route.request();
  const url = new URL(request.url());
  const path = url.pathname.replace(/^\/api/, "");
  const log = process.env.E2E_LOG_UNINTERCEPTED;
  if (log) appendFileSync(log, `${request.method()} ${path}\n`);
  if (request.method() === "GET" && /^\/meetings\/\d+\/audio$/.test(path)) {
    await route.fulfill({ status: 200, contentType: "audio/mpeg", body: "" }); // 빈 음성(재생 시도는 오류 이벤트로만 끝난다)
    return;
  }
  const body = knownBody(request.method(), path) ?? (request.method() === "GET" ? [] : {});
  await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
}

export const test = base.extend({
  page: async ({ page }, use) => {
    const leaked: string[] = [];
    const pending: Promise<void>[] = [];
    page.on("response", (response) => {
      const url = response.url();
      if (!/\/api\//.test(url)) return;
      pending.push(
        response
          .serverAddr()
          .then((addr) => {
            if (addr) leaked.push(`${response.request().method()} ${new URL(url).pathname}`);
          })
          .catch(() => undefined),
      );
    });
    await page.route("**/api/**", fallbackApi); // 가장 낮은 우선순위(먼저 등록)
    await page.route("**/api/me/processing", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
    await use(page);
    await Promise.all(pending);
    expect(leaked, "가로채지 않은 /api 요청이 실제 네트워크로 나갔습니다").toEqual([]);
  },
});
