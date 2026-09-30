/*
 * 화자 이름 매핑 도구 (docs/API-CONTRACT.md "화자 이름", v1.9.9)
 * 서버에는 {"화자1": "권영우 부장"} 매핑만 저장하고, 원본(assignee·전사 원문)은 바꾸지 않는다.
 * 화면에 보여 줄 때만 applySpeakerNames로 바꾼다.
 *
 * 이 파일은 다른 모듈을 import 하지 않는다 (node --test 로 바로 단위 테스트하기 위해).
 */

/** 화자 표기 → 실제 이름/직함. 매핑이 없으면 {} */
export type SpeakerNames = Record<string, string>;

/** 서버와 같은 키 규칙: "화자" + 숫자(0-9) */
export const SPEAKER_KEY_PATTERN = /^화자\d+$/;
export const SPEAKER_NAME_MAX = 30;

// 텍스트 안의 화자 표기를 찾는 패턴. \d+ 가 최대한 길게 잡으므로 "화자12"는 "화자1"로 잘리지 않는다.
const SPEAKER_TOKEN = /화자\d+/g;

/**
 * 텍스트의 "화자N"을 매핑 값으로 바꾼다.
 * 한 번의 replace(단일 패스)라 바꾼 결과를 다시 바꾸지 않는다. 예: {화자1: "화자2", 화자2: "김"} → "화자1" 은 "화자2" 로 끝.
 * 매핑에 없는 화자N과 빈 문자열은 그대로 둔다.
 */
export function applySpeakerNames(text: string, names: SpeakerNames): string {
  if (!text) return text;
  return text.replace(SPEAKER_TOKEN, (token) =>
    Object.prototype.hasOwnProperty.call(names, token) ? names[token] : token,
  );
}

/** 여러 텍스트에서 나온 "화자N"을 중복 없이 번호순(화자2 < 화자10)으로 모은다 */
export function findSpeakerLabels(texts: ReadonlyArray<string | null | undefined>): string[] {
  const found = new Set<string>();
  for (const text of texts) {
    if (!text) continue;
    for (const match of text.matchAll(SPEAKER_TOKEN)) found.add(match[0]);
  }
  return [...found].sort((a, b) => Number(a.slice(2)) - Number(b.slice(2)));
}

/** 글자 수: 서버(파이썬 len)와 같게 코드 포인트 단위로 센다 */
const charCount = (value: string) => [...value].length;

/**
 * 저장 전 검사·정리 (서버와 같은 규칙). 목 API와 화면이 함께 쓴다.
 * - 키는 "화자N"만, 값은 앞뒤 공백 제거 후 1~30자
 * - 공백 제거 후 빈 값이면 그 키를 뺀다(이름 지정 해제)
 * 규칙을 어기면 null (서버라면 400)
 */
export function normalizeSpeakerNames(input: Readonly<Record<string, unknown>>): SpeakerNames | null {
  const result: SpeakerNames = {};
  for (const [key, value] of Object.entries(input)) {
    if (!SPEAKER_KEY_PATTERN.test(key) || typeof value !== "string") return null;
    const name = value.trim();
    if (!name) continue;
    if (charCount(name) > SPEAKER_NAME_MAX) return null;
    result[key] = name;
  }
  return result;
}

/** 같은 이름이 둘 이상의 화자에 지정된 경우 그 이름 목록 (저장은 막지 않고 경고만) */
export function duplicateSpeakerNames(names: SpeakerNames): string[] {
  const seen = new Map<string, number>();
  for (const name of Object.values(names)) seen.set(name, (seen.get(name) ?? 0) + 1);
  return [...seen].filter(([, count]) => count > 1).map(([name]) => name);
}
