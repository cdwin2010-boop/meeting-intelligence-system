// 회의 일시 입력 도우미 단위 테스트 (Node 내장 test runner)
// 실행: cd frontend && node --test lib/meeting-at.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { futureToleranceMs, isFutureMeetingAt, partsFromTimestamp, suggestionFromTimestamp, toOffsetIso } from "./v2/meeting-at.ts";

test("toOffsetIso: 현지 날짜·시각 + 오프셋, 잘못된 값은 null", () => {
  const iso = toOffsetIso("2026-10-01", "13:20");
  assert.match(iso, /^2026-10-01T13:20:00[+-]\d{2}:\d{2}$/);
  assert.equal(new Date(iso).getTime(), new Date("2026-10-01T13:20:00").getTime()); // 같은 순간(날짜가 밀리지 않음)
  assert.equal(toOffsetIso("", "13:20"), null);
  assert.equal(toOffsetIso("2026-10-01", ""), null);
  assert.equal(toOffsetIso("2026-13-45", "13:20"), null);
});

test("partsFromTimestamp: 현지 date·time 형식", () => {
  const ms = new Date("2026-10-01T09:05:00").getTime();
  assert.deepEqual(partsFromTimestamp(ms), { date: "2026-10-01", time: "09:05" });
});

test("미래 판정: 허용 오차 안은 통과, 넘으면 미래", () => {
  const now = new Date("2026-10-13T10:00:00").getTime();
  const tol = 10 * 60_000;
  assert.equal(isFutureMeetingAt("2026-10-13T10:05:00+00:00", now - new Date().getTimezoneOffset() * 0, tol), isFutureMeetingAt("2026-10-13T10:05:00+00:00", now, tol));
  assert.equal(isFutureMeetingAt(new Date(now + 5 * 60_000).toISOString(), now, tol), false);
  assert.equal(isFutureMeetingAt(new Date(now + 30 * 60_000).toISOString(), now, tol), true);
  assert.equal(isFutureMeetingAt("아무 값", now, tol), false); // 해석 불가는 서버가 판정
});

test("제안값: 과거 시각만, 0·음수·NaN·미래·없음은 null(빈칸 + 필수 안내)", () => {
  const now = new Date("2026-10-13T10:00:00").getTime();
  const tol = 10 * 60_000;
  assert.deepEqual(suggestionFromTimestamp(new Date("2026-10-12T08:30:00").getTime(), now, tol), { date: "2026-10-12", time: "08:30" });
  for (const value of [0, -5, Number.NaN, undefined, null, now + 3_600_000]) {
    assert.equal(suggestionFromTimestamp(value, now, tol), null, String(value));
  }
});

test("허용 오차 환경값: 잘못되면 10분", () => {
  assert.equal(futureToleranceMs("5"), 5 * 60_000);
  assert.equal(futureToleranceMs("abc"), 10 * 60_000);
  assert.equal(futureToleranceMs("-1"), 10 * 60_000);
});
