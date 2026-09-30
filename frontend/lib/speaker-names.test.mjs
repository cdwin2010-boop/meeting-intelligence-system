// 화자 이름 도구 단위 테스트 (새 패키지 없이 Node 내장 test runner + TS 타입 제거 실행)
// 실행: cd frontend && node --test lib/speaker-names.test.mjs   (Node 22.18+ / 23.6+)
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  applySpeakerNames,
  duplicateSpeakerNames,
  findSpeakerLabels,
  normalizeSpeakerNames,
} from "./speaker-names.ts";
import { transcriptFileName } from "./download-text.ts";

test("매핑된 화자만 바꾸고, 없는 화자와 빈 문자열은 그대로", () => {
  const names = { 화자1: "권영우 부장" };
  assert.equal(applySpeakerNames("[00:41] 화자1: 네. 화자3: 좋아요", names), "[00:41] 권영우 부장: 네. 화자3: 좋아요");
  assert.equal(applySpeakerNames("화자1", names), "권영우 부장");
  assert.equal(applySpeakerNames("", names), "");
  assert.equal(applySpeakerNames("화자2", {}), "화자2");
});

test("단일 패스: 바꾼 결과를 다시 바꾸지 않는다", () => {
  const names = { 화자1: "화자2", 화자2: "김 과장" };
  assert.equal(applySpeakerNames("화자1 / 화자2", names), "화자2 / 김 과장");
});

test("화자12는 화자1로 잘려 바뀌지 않는다", () => {
  assert.equal(applySpeakerNames("화자12 화자1", { 화자1: "권 부장" }), "화자12 권 부장");
});

test("여러 텍스트에서 화자N을 중복 없이 번호순으로 모은다", () => {
  assert.deepEqual(findSpeakerLabels(["화자10: a\n화자2: b", null, "화자2", "", "김도현"]), ["화자2", "화자10"]);
});

test("저장 전 검사: 공백 제거, 빈 값 삭제, 잘못된 키·30자 초과는 null", () => {
  assert.deepEqual(normalizeSpeakerNames({ 화자1: "  권 부장 ", 화자2: "   " }), { 화자1: "권 부장" });
  assert.equal(normalizeSpeakerNames({ 화자A: "권" }), null);
  assert.equal(normalizeSpeakerNames({ " 화자1": "권" }), null);
  assert.equal(normalizeSpeakerNames({ 화자1: 3 }), null);
  assert.equal(normalizeSpeakerNames({ 화자1: "가".repeat(31) }), null);
  assert.deepEqual(normalizeSpeakerNames({ 화자1: "가".repeat(30) }), { 화자1: "가".repeat(30) });
});

test("같은 이름을 여러 화자에 지정하면 그 이름을 돌려준다", () => {
  assert.deepEqual(duplicateSpeakerNames({ 화자1: "권 부장", 화자2: "권 부장", 화자3: "한 팀장" }), ["권 부장"]);
  assert.deepEqual(duplicateSpeakerNames({ 화자1: "권 부장" }), []);
});

test("이름 적용 다운로드용 변환: 화자N만 바뀌고 줄바꿈(LF, CRLF)·공백·그 밖의 글자는 그대로", () => {
  const original = "[00:00:03] 화자1: 안녕하세요.\r\n[00:00:05] 화자2:  네,\t알겠습니다.\n\n[00:00:09] 화자1: 끝";
  const named = applySpeakerNames(original, { 화자1: "권영우 부장" });
  assert.equal(named, "[00:00:03] 권영우 부장: 안녕하세요.\r\n[00:00:05] 화자2:  네,\t알겠습니다.\n\n[00:00:09] 권영우 부장: 끝");
  // 줄 수가 늘거나 줄지 않는다 (줄 나누기 등 추가 가공 없음)
  assert.equal(named.split("\n").length, original.split("\n").length);
});

test("다운로드 파일 이름: 원본 / 이름 적용", () => {
  assert.equal(transcriptFileName("주간 회의", "mtg-1", "original"), "주간 회의_전사원문.txt");
  assert.equal(transcriptFileName("주간 회의", "mtg-1", "named"), "주간 회의_전사원문_이름적용.txt");
  assert.equal(transcriptFileName('a/b:"c"', "mtg-1", "named"), "abc_전사원문_이름적용.txt"); // 쓸 수 없는 문자 제거
  assert.equal(transcriptFileName("  ", "mtg-1", "original"), "mtg-1_전사원문.txt"); // 제목이 비면 회의 ID
});

test("근거 인용 화자 표시: 값 전체가 화자N이면 이름으로, 실명·빈 값은 그대로, 원본 객체는 바뀌지 않음", () => {
  const names = { 화자3: "한 팀장" };
  const quote = { speaker: "화자3", timestamp: "00:02:49", text: "화자3: 알겠습니다." };
  const shown = { ...quote, speaker: applySpeakerNames(quote.speaker, names) };
  assert.equal(shown.speaker, "한 팀장");
  assert.equal(quote.speaker, "화자3"); // 원본 그대로 (표시용 복사본만 바뀜)
  assert.equal(applySpeakerNames("김도현", names), "김도현");
  assert.equal(applySpeakerNames("", names), "");
});
