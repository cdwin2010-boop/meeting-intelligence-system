/*
 * 전사문 시각·근거 위치 대응 단위 테스트(작업 B-1b). 브라우저 없이 순수 함수만 검사한다.
 */
import { expect, test } from "@playwright/test";
import { parseTranscriptBlocks, resolveEvidenceIndex, splitStamp } from "../lib/v2/transcript-time";

const ONE_LINE =
  "[00:00:00] 화자1: 시작합니다. [00:00:10] 화자2: 견적서를 보내겠습니다. [00:00:10] 화자3: 일정은 [00:12] 쯤 정합니다. 시간: 오후입니다. [00:00:30] 화자1: 알겠습니다.";

test.describe("전사문 발화 나누기·근거 대응(B-1b)", () => {
  test("줄바꿈 없는 한 줄 전사문을 \"[시각] 화자:\" 경계로 나누고 말 속 [시각]은 경계로 보지 않는다", () => {
    const blocks = parseTranscriptBlocks(ONE_LINE);
    expect(blocks.map((b) => b.start)).toEqual([0, 10, 10, 30]);
    expect(blocks[2].text).toContain("[00:12] 쯤");
    expect(splitStamp(blocks[1].text).stamp).toBe("[00:00:10]");
  });

  const blocks = parseTranscriptBlocks(ONE_LINE);
  const starts = blocks.map((b) => b.start);
  const texts = blocks.map((b) => b.text);

  test("시각이 정확히 하나만 일치하면 그 발화", () => {
    expect(resolveEvidenceIndex(starts, texts, 30, "")).toBe(3);
    expect(resolveEvidenceIndex(starts, texts, 0, "")).toBe(0);
  });
  test("같은 시각에 발화가 여럿이면 인용문이 든 발화", () => {
    expect(resolveEvidenceIndex(starts, texts, 10, "견적서를 보내겠습니다")).toBe(1);
    expect(resolveEvidenceIndex(starts, texts, 10, "오후입니다")).toBe(2);
  });
  test("시각으로 못 찾으면 인용문 텍스트 검색으로 대체", () => {
    expect(resolveEvidenceIndex(starts, texts, 99, "알겠습니다")).toBe(3);
    expect(resolveEvidenceIndex(starts, texts, null, "시작합니다")).toBe(0);
  });
  test("시각도 인용문도 없으면 못 찾음(-1)", () => {
    expect(resolveEvidenceIndex(starts, texts, null, "없는 문장")).toBe(-1);
    expect(resolveEvidenceIndex([], [], 10, "알겠습니다")).toBe(-1);
  });
});
