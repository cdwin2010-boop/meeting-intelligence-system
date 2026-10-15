// 고객 데모 영상(약 1분) 녹화 스크립트(작업 D-1). 사용법은 demo/README.md.
// - 가상 샘플 데이터만 쓴다: 임시 DB 에 seed_demo.py 로 시드하고, 백엔드(8102)·프론트(3202)를 별도 포트로 띄운다. 실제 AI 는 호출하지 않는다.
// - 결과는 저장소가 아니라 C:\DOTS_demo\<yyyymmdd_hhmm>\ 에 만든다(이미 있으면 새 이름).
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync, copyFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");
const FRONTEND = join(ROOT, "frontend");
const BACKEND = join(ROOT, "backend");
const require = createRequire(join(FRONTEND, "package.json"));
const { chromium } = require("@playwright/test");

const DEMO_ROOT = "C:\\DOTS_demo";
const BACK_PORT = 8102;
const FRONT_PORT = 3202;
const WIDTH = 1920;
const HEIGHT = 1080;
const PRODUCT = "AI DOTS";
const TOKEN_KEY = "mi.v2.accessToken";
const MIN_SEC = 55;
const MAX_SEC = 90; // 1분 30초 이내(영상 속도·길이를 강제로 조정하지 않고 자연 녹화 길이로 판정)

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function stamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}`;
}

function freshDir() {
  mkdirSync(DEMO_ROOT, { recursive: true });
  let name = stamp();
  let dir = join(DEMO_ROOT, name);
  for (let i = 2; existsSync(dir); i++) dir = join(DEMO_ROOT, `${name}_${i}`);
  mkdirSync(dir, { recursive: true });
  return dir;
}

function findTool(envName, exe) {
  if (process.env[envName]) return process.env[envName];
  const pkgs = join(process.env.LOCALAPPDATA ?? "", "Microsoft", "WinGet", "Packages");
  if (existsSync(pkgs)) {
    for (const p of readdirSync(pkgs)) {
      if (!p.startsWith("Gyan.FFmpeg")) continue;
      for (const sub of readdirSync(join(pkgs, p))) {
        const candidate = join(pkgs, p, sub, "bin", exe);
        if (existsSync(candidate)) return candidate;
      }
    }
  }
  return exe;
}
const FFMPEG = findTool("FFMPEG_PATH", "ffmpeg.exe");
const FFPROBE = findTool("FFPROBE_PATH", "ffprobe.exe");

const children = [];
function start(cmd, args, options) {
  const child = spawn(cmd, args, { ...options, stdio: "ignore", windowsHide: true });
  children.push(child);
  return child;
}
function stopAll() {
  for (const child of children) {
    if (child.pid && child.exitCode === null) spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  }
}

async function waitFor(url, label, ms = 180000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try {
      const res = await fetch(url);
      if (res.status < 500) return;
    } catch {
      /* 아직 기동 중 */
    }
    await sleep(1000);
  }
  throw new Error(`${label} 기동 시간 초과`);
}

/* ---------------- 화면 오버레이(자막·표본 데이터 문구·포인터) ---------------- */
const OVERLAY_SCRIPT = `(() => {
  if (window.__demoOverlay) return;
  window.__demoOverlay = true;
  const css = document.createElement('style');
  css.textContent = [
    'nextjs-portal{display:none!important}',
    '#demo-badge{position:fixed;left:16px;bottom:16px;z-index:2147483646;padding:6px 12px;border-radius:999px;background:#1B2430;color:#fff;font:600 14px "IBM Plex Sans KR","Malgun Gothic",sans-serif;opacity:.92}',
    '#demo-caption{position:fixed;left:50%;bottom:64px;transform:translateX(-50%);z-index:2147483646;max-width:1400px;padding:14px 28px;border-radius:12px;background:rgba(27,36,48,.92);color:#fff;font:600 30px/1.4 "IBM Plex Sans KR","Malgun Gothic",sans-serif;text-align:center;transition:opacity .25s}',
    '#demo-cursor{position:fixed;left:0;top:0;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:50%;background:rgba(47,91,234,.35);border:2px solid #2F5BEA;z-index:2147483647;pointer-events:none;transition:transform .08s}',
    '.demo-ripple{position:fixed;width:20px;height:20px;margin:-10px 0 0 -10px;border-radius:50%;border:3px solid #2F5BEA;z-index:2147483647;pointer-events:none;animation:demoRipple .6s ease-out forwards}',
    '@keyframes demoRipple{to{transform:scale(3.2);opacity:0}}',
    '#demo-card{position:fixed;inset:0;z-index:2147483645;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;background:#F6F7F9;color:#1B2430;font-family:"IBM Plex Sans KR","Malgun Gothic",sans-serif;text-align:center;transition:opacity .35s}',
    '#demo-card h1{font-size:76px;margin:0;font-weight:700}#demo-card p{font-size:38px;margin:0;color:#5B6675}'
  ].join('');
  document.head.appendChild(css);
  const mount = () => {
    const badge = document.createElement('div'); badge.id = 'demo-badge'; badge.textContent = '시연용 샘플 데이터'; document.body.appendChild(badge);
    const cap = document.createElement('div'); cap.id = 'demo-caption'; cap.style.opacity = '0'; document.body.appendChild(cap);
    const cur = document.createElement('div'); cur.id = 'demo-cursor'; document.body.appendChild(cur);
    window.addEventListener('mousemove', (e) => { cur.style.left = e.clientX + 'px'; cur.style.top = e.clientY + 'px'; }, true);
    window.addEventListener('mousedown', (e) => {
      const r = document.createElement('div'); r.className = 'demo-ripple'; r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px';
      document.body.appendChild(r); setTimeout(() => r.remove(), 700);
    }, true);
  };
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);
  window.__setCaption = (t) => { const c = document.getElementById('demo-caption'); if (!c) return; c.textContent = t || ''; c.style.opacity = t ? '1' : '0'; };
  window.__showCard = (title, sub) => {
    let card = document.getElementById('demo-card');
    if (!card) { card = document.createElement('div'); card.id = 'demo-card'; document.body.appendChild(card); }
    card.style.opacity = '1';
    card.innerHTML = '';
    const h = document.createElement('h1'); h.textContent = title; card.appendChild(h);
    const p = document.createElement('p'); p.textContent = sub; card.appendChild(p);
  };
  window.__hideCard = () => { const c = document.getElementById('demo-card'); if (c) { c.style.opacity = '0'; setTimeout(() => c.remove(), 400); } };
})();`;

// 비밀 패턴: 키 형태 문자열, 주소(http), 이메일, 파일 경로
const SECRET_PATTERNS = [
  [/AIza[0-9A-Za-z_-]{20,}/, "API 키 형태"],
  [/sk-[A-Za-z0-9]{20,}/, "API 키 형태"],
  [/https?:\/\/\S+/i, "http 주소"],
  [/[\w.+-]+@[\w-]+\.[\w.-]+/, "이메일"],
  [/[A-Za-z]:\\[^\s]+/, "파일 경로"],
  [/\/Users\/\S+/, "파일 경로"],
  [/Bearer\s+[A-Za-z0-9._-]{10,}/, "토큰"],
];

async function scanScreen(page, label) {
  const text = await page.evaluate(() => document.body.innerText);
  for (const [pattern, kind] of SECRET_PATTERNS) {
    if (pattern.test(text)) throw new Error(`비밀 패턴 검사 실패(${kind}): ${label}`);
  }
}

/* ---------------- 장면 ---------------- */
async function glide(page, locator, { click = false } = {}) {
  await locator.scrollIntoViewIfNeeded();
  const box = await locator.boundingBox();
  if (!box) throw new Error("대상 위치를 찾지 못했습니다");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 30 });
  await sleep(250);
  if (click) await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
}

async function caption(page, text) {
  await page.evaluate((t) => window.__setCaption && window.__setCaption(t), text);
}

async function runScenes(page, seed, scale, timings) {
  const base = `http://localhost:${FRONT_PORT}`;
  const meetings = seed.meetings;
  let sceneStart = Date.now();
  const mark = async (name, budgetSec, body) => {
    const started = Date.now();
    await body();
    const wait = Math.max(0, budgetSec * scale * 1000 - (Date.now() - started));
    await sleep(wait);
    timings.push({ scene: name, start: (started - sceneStart) / 1000, end: (Date.now() - sceneStart) / 1000 });
  };

  await page.goto(`${base}/v2`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("h1");
  await page.evaluate(OVERLAY_SCRIPT);
  sceneStart = Date.now();

  // 1) 오프닝
  await mark("1 오프닝", 3, async () => {
    await page.evaluate(([t, s]) => window.__showCard(t, s), [PRODUCT, "회의 결정을 업무로, 근거와 함께"]);
  });
  await page.evaluate(() => window.__hideCard());

  // 2) 입력: 붙여넣기와 txt 업로드(엑셀은 보이지 않는다)
  await mark("2 입력", 7, async () => {
    await page.goto(`${base}/v2/upload`, { waitUntil: "domcontentloaded" });
    await page.evaluate(OVERLAY_SCRIPT);
    await page.getByRole("heading", { level: 1, name: "회의록 올리기" }).waitFor();
    await scanScreen(page, "입력(업로드 화면)");
    await caption(page, "음성 파일·txt 파일·텍스트 붙여넣기로 회의 내용을 올립니다");
    await glide(page, page.getByRole("group", { name: "자료 종류" }).locator("label").filter({ hasText: "자료 파일(txt)" }), { click: true });
    await sleep(500);
    await glide(page, page.getByRole("group", { name: "자료 종류" }).locator("label").filter({ hasText: "텍스트 직접 입력" }), { click: true });
    const area = page.getByLabel("회의 내용");
    await glide(page, area, { click: true });
    await area.pressSequentially("[00:01:13] 박서준: 시제품 검수 결과 보고서를 금요일까지 작성해 주세요.", { delay: 22 });
  });

  // 3) 업무 정리
  await mark("3 업무 정리", 10, async () => {
    await page.goto(`${base}/v2/meetings/${meetings.awaiting}`, { waitUntil: "domcontentloaded" });
    await page.evaluate(OVERLAY_SCRIPT);
    await page.getByRole("heading", { level: 1 }).waitFor();
    await caption(page, "회의 내용에서 업무를 담당자·기한과 함께 정리합니다(확정 전 검토)");
    await page.getByText("협력사 납기 일정 재확인").first().waitFor();
    await scanScreen(page, "업무 정리(회의록 상세)");
    await glide(page, page.getByText("시제품 검수 결과 보고서 작성").first());
    await sleep(900);
    await glide(page, page.getByText("출시 홍보 문구 초안 공유").first());
  });

  // 4) 근거 1:1 확인
  await mark("4 근거 확인", 12, async () => {
    await caption(page, "업무마다 근거 시각과 원문 인용문을 1:1로 확인합니다");
    const evidence = page.getByRole("button", { name: /근거 위치 보기|전사문/ }).first();
    await glide(page, evidence, { click: true });
    await sleep(1500);
    await scanScreen(page, "근거 확인(전사문 보기)");
    await page.keyboard.press("Escape");
  });

  // 5) 역할별 확정
  await mark("5 역할별 확정", 8, async () => {
    await caption(page, "확정은 지시자·관리자만, 담당자는 열람과 본인 업무 중심");
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
    const confirm = page.getByRole("button", { name: "회의록 확정" }).first();
    await glide(page, confirm);
    await sleep(2000);
    // 담당자 계정으로 같은 화면을 다시 연다: 확정 버튼이 없다
    await page.evaluate((t) => sessionStorage.setItem("demo.token", t), seed.tokens.kim);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { level: 1 }).waitFor();
    await page.evaluate(OVERLAY_SCRIPT);
    await caption(page, "담당자(김도연) 계정으로 열면 확정 버튼이 보이지 않습니다");
    await sleep(500);
    await page.evaluate(() => sessionStorage.removeItem("demo.token"));
  });

  // 6) 이력 관리
  await mark("6 이력", 7, async () => {
    await page.goto(`${base}/v2/meetings/${meetings.held}`, { waitUntil: "domcontentloaded" });
    await page.evaluate(OVERLAY_SCRIPT);
    await page.getByRole("heading", { level: 1 }).waitFor();
    await caption(page, "보류·삭제에는 처리자와 사유가 남고, 삭제는 목록에서 숨김 처리됩니다");
    await scanScreen(page, "이력(보류 회의록)");
    await page.getByText("협력사 회신 대기로 일정 재협의").first().waitFor();
    await glide(page, page.getByText("협력사 회신 대기로 일정 재협의").first());
  });

  // 7) 할 일 캘린더
  await mark("7 캘린더", 10, async () => {
    await page.goto(`${base}/v2`, { waitUntil: "domcontentloaded" });
    await page.evaluate(OVERLAY_SCRIPT);
    await page.getByRole("heading", { level: 1, name: "할 일" }).waitFor();
    await caption(page, "목록과 캘린더로 전환해 마감일을 한눈에 봅니다");
    await glide(page, page.getByRole("button", { name: "캘린더" }), { click: true });
    await page.getByRole("grid", { name: /마감일 캘린더/ }).waitFor();
    await glide(page, page.getByRole("button", { name: "전체 업무" }), { click: true }); // 관리자는 팀 전체 업무도 볼 수 있다
    await page.getByRole("button", { name: /협력사 납기 일정 재확인/ }).first().waitFor();
    await scanScreen(page, "캘린더(월간)");
    await sleep(900);
    await glide(page, page.getByRole("button", { name: "주간" }), { click: true });
    await sleep(900);
    await glide(page, page.getByRole("button", { name: "월간" }), { click: true });
    await sleep(500);
    await glide(page, page.getByRole("button", { name: /협력사 납기 일정 재확인/ }).first(), { click: true });
    await page.getByRole("dialog").waitFor();
    await sleep(1500);
    await scanScreen(page, "캘린더(상세 모달)");
    await page.keyboard.press("Escape");
    await caption(page, "기한이 정해지지 않은 업무는 별도 목록으로 분리해 보여 줍니다");
    await glide(page, page.getByRole("region", { name: "기한 미확정 업무" }));
  });

  // 8) 마무리
  await mark("8 마무리", 3, async () => {
    await caption(page, "");
    await page.evaluate(([t, s]) => window.__showCard(t, s), [PRODUCT, "도입 문의: [문의처 안내 자리]"]);
  });
}

function probeDuration(file) {
  const out = spawnSync(FFPROBE, ["-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", file], { encoding: "utf-8" });
  return Number.parseFloat((out.stdout ?? "").trim());
}

async function record(outDir, seed, scale, attempt) {
  const videoDir = join(outDir, `_video_${attempt}`);
  mkdirSync(videoDir, { recursive: true });
  const browser = await chromium.launch({ channel: "chrome", args: ["--force-device-scale-factor=1", "--hide-scrollbars"] });
  const context = await browser.newContext({
    viewport: { width: WIDTH, height: HEIGHT }, locale: "ko-KR", timezoneId: "Asia/Seoul", colorScheme: "light",
    recordVideo: { dir: videoDir, size: { width: WIDTH, height: HEIGHT } },
  });
  await context.addInitScript(([k, t]) => sessionStorage.setItem(k, sessionStorage.getItem("demo.token") || t), [TOKEN_KEY, seed.tokens.mgr]);
  context.setDefaultTimeout(90000); // 개발 서버의 첫 컴파일 지연에 대비
  const page = await context.newPage();
  const timings = [];
  let failure = null;
  try {
    await runScenes(page, seed, scale, timings);
  } catch (error) {
    failure = error;
    await page.screenshot({ path: join(outDir, "_fail.png") }).catch(() => undefined); // 실패 원인 확인용(성공하면 남지 않음)
  }
  const videoPath = await page.video().path();
  await context.close();
  await browser.close();
  if (failure) throw failure;
  return { videoPath, timings };
}

async function warmUp(seed) {
  // 첫 방문 컴파일 지연이 영상에 들어가지 않도록 같은 경로를 녹화 없이 한 번 연다
  const browser = await chromium.launch({ channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: WIDTH, height: HEIGHT }, locale: "ko-KR" });
  await context.addInitScript(([k, t]) => sessionStorage.setItem(k, sessionStorage.getItem("demo.token") || t), [TOKEN_KEY, seed.tokens.mgr]);
  const page = await context.newPage();
  const paths = ["/v2", "/v2/upload", `/v2/meetings/${seed.meetings.awaiting}`, `/v2/meetings/${seed.meetings.held}`];
  for (const p of paths) {
    await page.goto(`http://localhost:${FRONT_PORT}${p}`, { waitUntil: "networkidle", timeout: 120000 });
  }
  await browser.close();
}

async function main() {
  const outDir = freshDir();
  const work = join(outDir, "_work");
  mkdirSync(work, { recursive: true });
  const dbFile = join(work, "demo.db").replaceAll("\\", "/");
  const seedJson = join(work, "seed.json");
  const python = join(BACKEND, ".venv", "Scripts", "python.exe");
  const backendEnv = {
    ...process.env, DATABASE_URL: `sqlite:///${dbFile}`, UPLOAD_DIR: join(work, "uploads"), STT_PROVIDER: "fake", LLM_PROVIDER: "fake",
    CORS_ORIGINS: `http://localhost:${FRONT_PORT}`, PYTHONIOENCODING: "utf-8",
  };
  let exitCode = 0;
  try {
    const seeded = spawnSync(python, [join(HERE, "seed_demo.py"), dbFile, seedJson], { cwd: BACKEND, env: backendEnv, encoding: "utf-8" });
    if (seeded.status !== 0) throw new Error("시드 실패");
    const seed = JSON.parse(readFileSync(seedJson, "utf-8"));
    start(python, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(BACK_PORT)], { cwd: BACKEND, env: backendEnv });
    start("cmd.exe", ["/c", "npx", "next", "dev", "-p", String(FRONT_PORT)], {
      cwd: FRONTEND,
      env: { ...process.env, NEXT_PUBLIC_API_V2_BASE_URL: `http://127.0.0.1:${BACK_PORT}/api`, NEXT_PUBLIC_USE_MOCK: "false" },
    });
    await waitFor(`http://127.0.0.1:${BACK_PORT}/api/health`, "백엔드");
    await waitFor(`http://localhost:${FRONT_PORT}/v2/login`, "프론트");
    await warmUp(seed);

    let scale = 1;
    let result = null;
    let duration = 0;
    let mp4 = join(outDir, "demo_1min.mp4");
    for (let attempt = 1; attempt <= 3; attempt++) {
      result = await record(outDir, seed, scale, attempt);
      const webm = join(outDir, "demo_1min.webm");
      copyFileSync(result.videoPath, webm);
      const conv = spawnSync(FFMPEG, ["-y", "-i", webm, "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "20", "-movflags", "+faststart", mp4], { encoding: "utf-8" });
      if (conv.status !== 0) throw new Error("MP4 변환 실패");
      duration = probeDuration(mp4);
      console.log(`시도 ${attempt}: 길이 ${duration.toFixed(1)}초 (배율 ${scale.toFixed(3)})`);
      if (duration >= MIN_SEC && duration <= MAX_SEC) break;
      // 목표 60초에 맞춰 장면 간격을 조정한다(장면 안 동작 시간은 고정이라 남는 대기만 늘리거나 줄인다)
      scale = Math.max(0.5, scale * (60 / duration));
    }
    if (!(duration >= MIN_SEC && duration <= MAX_SEC)) throw new Error(`영상 길이 ${duration.toFixed(1)}초: 55~90초 범위를 벗어났습니다`);

    // 산출물: 타이밍 CSV, 시나리오, 대표 프레임
    const lines = ["scene,start_sec,end_sec"].concat(result.timings.map((t) => `${t.scene},${t.start.toFixed(1)},${t.end.toFixed(1)}`));
    writeFileSync(join(outDir, "scene_timings.csv"), lines.join("\n") + "\n", "utf-8");
    const frames = join(outDir, "frames");
    mkdirSync(frames, { recursive: true });
    for (const [i, t] of result.timings.entries()) {
      const mid = (t.start + t.end) / 2;
      spawnSync(FFMPEG, ["-y", "-ss", mid.toFixed(2), "-i", mp4, "-frames:v", "1", join(frames, `scene_${i + 1}.png`)], { stdio: "ignore" });
    }
    const scenario = readFileSync(join(HERE, "scenario_template.md"), "utf-8")
      .replace("{{DURATION}}", duration.toFixed(1))
      .replace("{{TABLE}}", result.timings.map((t) => `| ${t.scene} | ${t.start.toFixed(1)}~${t.end.toFixed(1)}초 |`).join("\n"));
    writeFileSync(join(outDir, "scenario.md"), scenario, "utf-8");
    console.log(`완료: ${outDir}`);
    console.log(`길이: ${duration.toFixed(1)}초`);
  } catch (error) {
    console.error(`실패: ${error instanceof Error ? error.message : error}`);
    exitCode = 1;
  } finally {
    stopAll();
    await sleep(1500);
    // 임시 DB·업로드·시드 파일은 녹화가 끝나면 지운다(가상 데이터지만 결과 폴더에는 영상 파일만 남긴다)
    for (const p of [work]) rmSync(p, { recursive: true, force: true });
    for (const name of readdirSync(outDir)) if (name.startsWith("_video_")) rmSync(join(outDir, name), { recursive: true, force: true });
  }
  process.exit(exitCode);
}

main();
