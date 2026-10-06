/*
 * Playwright E2E 설정 (v1.9.9) — 목 모드(NEXT_PUBLIC_USE_MOCK=true) 화면만 검증한다. 실서버·백엔드 데이터는 쓰지 않는다.
 *
 * 실행 방법
 *  1) 테스트용 서버를 따로 띄워 둔 경우: E2E_BASE_URL=http://localhost:3100 npx playwright test
 *     (서버는 반드시 목 모드로 빌드·실행된 것이어야 한다)
 *  2) E2E_BASE_URL 이 없으면 아래 webServer 가 목 모드 dev 서버(3100)를 직접 띄운다.
 *     주의: 같은 폴더에서 `npm run dev`(3000)가 이미 돌고 있으면 두 서버가 .next 폴더를 함께 쓰므로 1) 방식을 쓴다.
 *
 * 브라우저는 새로 내려받지 않고 설치된 Chrome(channel: "chrome")을 쓴다.
 */
import { defineConfig } from "@playwright/test";

const externalBaseURL = process.env.E2E_BASE_URL;
const PORT = 3100;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000, // 목 업로드는 접수 후 약 8초 뒤 완료된다
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: externalBaseURL ?? `http://localhost:${PORT}`,
    channel: "chrome",
    headless: true,
    viewport: { width: 1280, height: 900 },
    acceptDownloads: true,
    locale: "ko-KR",
    timezoneId: "Asia/Seoul",
  },
  webServer: externalBaseURL
    ? undefined
    : {
        command: `npx next dev -p ${PORT}`,
        url: `http://localhost:${PORT}`,
        reuseExistingServer: false,
        timeout: 120_000,
        // 프로세스 환경변수가 .env.local 보다 우선 → 항상 목 모드
        env: { NEXT_PUBLIC_USE_MOCK: "true", NEXT_PUBLIC_PROCESSING_REFRESH_SEC: "1" },
      },
});
