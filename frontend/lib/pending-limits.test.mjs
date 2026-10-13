// 등록 보류 한도 판정 단위 테스트 (Node 내장 test runner)
// 실행: cd frontend && node --test lib/pending-limits.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { checkPendingLimits, describeViolation, formatMb } from "./v2/pending-limits.ts";

const MB = 1024 * 1024;
const LIMITS = { maxCount: 5, maxTotalBytes: 300 * MB };

test("한도 안이면 통과", () => {
  assert.equal(checkPendingLimits([], 10 * MB, LIMITS), null);
  assert.equal(checkPendingLimits([100 * MB, 100 * MB], 100 * MB, LIMITS), null); // 합계가 정확히 상한이면 통과
});

test("6번째 건은 건수 한도 초과", () => {
  const violation = checkPendingLimits([1, 1, 1, 1, 1], 1, LIMITS);
  assert.deepEqual(violation, { kind: "count", maxCount: 5 });
  assert.match(describeViolation(violation), /최대 5건/);
});

test("합계 용량이 상한을 넘으면 용량 한도 초과(현재·이번 파일 크기 표시)", () => {
  const violation = checkPendingLimits([200 * MB], 101 * MB, LIMITS);
  assert.deepEqual(violation, { kind: "size", maxBytes: 300 * MB, currentBytes: 200 * MB, incomingBytes: 101 * MB });
  const text = describeViolation(violation);
  assert.match(text, /300MB/);
  assert.match(text, /200\.0MB/);
  assert.match(text, /101\.0MB/);
});

test("건수와 용량이 모두 넘으면 건수를 먼저 알린다", () => {
  assert.equal(checkPendingLimits([100 * MB, 100 * MB, 100 * MB, 1, 1], 100 * MB, LIMITS).kind, "count");
});

test("MB 표시", () => {
  assert.equal(formatMb(1.5 * MB), "1.5MB");
});
