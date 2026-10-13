// 모바일 Wi-Fi 안내 판정 단위 테스트 (Node 내장 test runner, 새 패키지 없음)
// 실행: cd frontend && node --test lib/network.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { connectionTypeOf, shouldShowWifiNotice, wifiNoticeRequiredFor } from "./v2/network.ts";

test("wifi·ethernet 은 안내 없이 진행", () => {
  assert.equal(shouldShowWifiNotice({ connection: { type: "wifi" } }), false);
  assert.equal(shouldShowWifiNotice({ connection: { type: "ethernet" } }), false);
  assert.equal(shouldShowWifiNotice({ connection: { type: "WiFi" } }), false); // 대소문자·공백 무시
  assert.equal(wifiNoticeRequiredFor(" ethernet "), false);
});

test("cellular 는 안내를 띄운다", () => {
  assert.equal(shouldShowWifiNotice({ connection: { type: "cellular" } }), true);
});

test("값을 알 수 없으면(미지원·undefined·unknown·none·other·빈 문자열) 항상 안내", () => {
  for (const source of [{}, { connection: null }, { connection: {} }, { connection: { type: undefined } }, null]) {
    assert.equal(shouldShowWifiNotice(source), true);
  }
  for (const type of ["unknown", "none", "other", "bluetooth", "", undefined, null]) {
    assert.equal(wifiNoticeRequiredFor(type), true, String(type));
  }
});

test("읽다가 예외가 나도 알 수 없음으로 보고 안내", () => {
  const source = {
    get connection() {
      throw new Error("blocked");
    },
  };
  assert.equal(connectionTypeOf(source), undefined);
  assert.equal(shouldShowWifiNotice(source), true);
});
