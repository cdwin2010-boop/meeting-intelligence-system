/*
 * Clean Light 토큰 글자 대비(작업 87-1): theme-v2.css 의 라이트 블록 값을 읽어 WCAG 4.5:1(글자)·3:1(컨트롤 테두리·포커스 링)을 계산한다.
 * 브라우저 없이 파일만 읽는다.
 */
import fs from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

const css = fs.readFileSync(path.join(process.cwd(), "app/mono-dark/theme-v2.css"), "utf-8");
const block = /html\[data-v2-theme="light"\]\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? "";
const token = (name: string): string => {
  const hit = new RegExp(`--mn-${name}:\\s*(#[0-9a-fA-F]{6})`).exec(block);
  if (!hit) throw new Error(`라이트 토큰 없음: ${name}`);
  return hit[1];
};

const channel = (v: number) => (v / 255 <= 0.03928 ? v / 255 / 12.92 : ((v / 255 + 0.055) / 1.055) ** 2.4);
const luminance = (hex: string) => {
  const h = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
};
const ratio = (a: string, b: string) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

const TEXT_PAIRS: Array<[string, string, string]> = [
  ["ink / surface", token("text"), token("surface")],
  ["ink / ground", token("text"), token("background")],
  ["ink-muted / ground", token("text-muted"), token("background")],
  ["ink-muted / surface", token("text-muted"), token("surface")],
  ["ink-muted / elevated(hover)", token("text-muted"), token("surface-elevated")],
  ["흰 글자 / brand", token("on-accent"), token("accent")],
  ["흰 글자 / brand(hover)", token("on-accent"), token("accent-hover")],
  ["brand-ink / brand-tint", token("brand-ink"), token("brand-tint")],
  ["ink-muted / brand-tint(선택)", token("text-muted"), token("selected")],
  ["ink / avatar", token("text"), token("avatar")],
  ["link / surface", token("link"), token("surface")],
  ["흰 글자 / red", token("on-red"), token("red")],
  ["승인 대기", token("chip-warning-fg"), token("chip-warning-bg")],
  ["수정 요청", token("chip-revision-fg"), token("chip-revision-bg")],
  ["보완 필요", token("chip-danger-fg"), token("chip-danger-bg")],
  ["진행 중", token("chip-info-fg"), token("chip-info-bg")],
  ["완료", token("chip-success-fg"), token("chip-success-bg")],
  ["보조 칩 neutral", token("chip-neutral-fg"), token("chip-neutral-bg")],
  ["보조 칩 muted", token("chip-muted-fg"), token("chip-muted-bg")],
  ["보조 칩 superseded", token("chip-superseded-fg"), token("chip-superseded-bg")],
  ["보조 칩 cancelled", token("chip-cancelled-fg"), token("chip-cancelled-bg")],
];

test.describe("Clean Light 토큰 대비", () => {
  for (const [name, fg, bg] of TEXT_PAIRS) {
    test(`글자 4.5:1 이상 — ${name}`, () => {
      expect(ratio(fg, bg), `${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
    });
  }

  test("컨트롤 테두리·포커스 링 3:1 이상, 지정 토큰 값", () => {
    for (const ground of [token("background"), token("surface")]) {
      expect(ratio(token("control-border"), ground)).toBeGreaterThanOrEqual(3);
      expect(ratio(token("focus"), ground)).toBeGreaterThanOrEqual(3);
    }
    expect([token("background"), token("surface"), token("border"), token("text"), token("text-muted"), token("accent"), token("brand-tint"), token("brand-ink"), token("avatar")]).toEqual(
      ["#f6f7f9", "#ffffff", "#e3e6eb", "#1b2430", "#5b6675", "#2f5bea", "#e9efff", "#1f3fa8", "#dde3ee"],
    );
  });
});
