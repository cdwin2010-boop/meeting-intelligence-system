// 창 크기 조절 계산 단위 테스트 (Node 내장 test runner, 새 패키지 없음)
// 실행: cd frontend && node --test lib/resize-rect.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { fitRect, offsetFromCenter, resizeRect } from "./resize-rect.ts";

const LIMITS = { viewportWidth: 1000, viewportHeight: 800, margin: 16, minWidth: 338, minHeight: 342 };
const START = { left: 250, top: 100, right: 750, bottom: 700 }; // 500 x 600

test("오른쪽 변을 끌면 왼쪽 변은 그대로", () => {
  assert.deepEqual(resizeRect(START, { x: 1, y: 0 }, 100, 50, LIMITS), { left: 250, top: 100, right: 850, bottom: 700 });
});

test("왼쪽 변을 끌면 오른쪽 변은 그대로 (위아래도 그대로)", () => {
  assert.deepEqual(resizeRect(START, { x: -1, y: 0 }, -100, 30, LIMITS), { left: 150, top: 100, right: 750, bottom: 700 });
});

test("위쪽 변을 끌면 아래쪽 변은 그대로", () => {
  assert.deepEqual(resizeRect(START, { x: 0, y: -1 }, 40, -50, LIMITS), { left: 250, top: 50, right: 750, bottom: 700 });
});

test("모서리: 왼쪽 위를 끌면 오른쪽·아래쪽 변은 그대로", () => {
  assert.deepEqual(resizeRect(START, { x: -1, y: -1 }, -20, -30, LIMITS), { left: 230, top: 70, right: 750, bottom: 700 });
  assert.deepEqual(resizeRect(START, { x: 1, y: -1 }, 20, -30, LIMITS), { left: 250, top: 70, right: 770, bottom: 700 });
});

test("최소 크기보다 작아지지 않는다 (반대쪽 변은 여전히 고정)", () => {
  assert.deepEqual(resizeRect(START, { x: -1, y: 0 }, 400, 0, LIMITS), { left: 750 - 338, top: 100, right: 750, bottom: 700 });
  assert.deepEqual(resizeRect(START, { x: 0, y: 1 }, 0, -500, LIMITS), { left: 250, top: 100, right: 750, bottom: 100 + 342 });
});

test("화면 여백 밖으로 나가지 않는다", () => {
  assert.deepEqual(resizeRect(START, { x: 1, y: 1 }, 900, 900, LIMITS), { left: 250, top: 100, right: 984, bottom: 784 });
  assert.deepEqual(resizeRect(START, { x: -1, y: -1 }, -900, -900, LIMITS), { left: 16, top: 16, right: 750, bottom: 700 });
});

test("fitRect: 화면이 줄면 크기를 줄이고 안쪽으로 민다 (최소 크기는 지킴)", () => {
  const small = { ...LIMITS, viewportWidth: 600, viewportHeight: 500 };
  // 폭 500은 들어가므로 그대로 두고 오른쪽 여백 안으로 밀기만, 높이 600은 468로 줄임
  assert.deepEqual(fitRect(START, small), { left: 84, top: 16, right: 584, bottom: 484 });
  const tiny = { ...LIMITS, viewportWidth: 300, viewportHeight: 300 };
  const fitted = fitRect(START, tiny);
  assert.equal(fitted.right - fitted.left, 338);
  assert.equal(fitted.bottom - fitted.top, 342);
});

test("offsetFromCenter: 가운데 정렬 위치에서 얼마나 밀지", () => {
  assert.deepEqual(offsetFromCenter(START, 1000, 800), { x: 0, y: 0 }); // 이미 가운데(가로)·위로 100
  assert.deepEqual(offsetFromCenter({ left: 100, top: 100, right: 600, bottom: 700 }, 1000, 800), { x: -150, y: 0 });
});
