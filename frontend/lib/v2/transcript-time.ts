/*
 * 업무 근거 시각 ↔ 전사문 위치 짝짓기용 시각 정규화(작업 68-2b).
 * 실제 데이터: 근거 시각은 API 의 evidenceStartSec(숫자 초, 0 도 유효, 없으면 null)이고, 전사문은 구간 목록(segments)이 없을 수 있다
 * (Gemini 전사는 segments 가 null 이고 본문이 "[mm:ss] 화자: 내용" 줄 형식 텍스트다). 그래서 구간 시각과 본문 줄머리 시각을 모두 "초(정수)"로 바꿔 비교한다.
 */

/**
 * 시각 → 초(정수, 소수는 내림). 지원: 숫자 초, 숫자 문자열, "mm:ss", "HH:MM:SS", 대괄호로 싼 위 형식("[mm:ss]").
 * 값이 없거나(null·undefined·빈 문자열) 해석할 수 없으면 null. 0 은 유효한 시각이다.
 */
export function parseSeconds(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) && value >= 0 ? Math.floor(value) : null;
  if (typeof value !== "string") return null;
  const text = value.trim().replace(/^\[\s*|\s*\]$/g, "").trim();
  if (!text) return null;
  if (/^\d+(\.\d+)?$/.test(text)) return Math.floor(Number(text));
  const match = /^(\d{1,3}):(\d{2})(?::(\d{2}))?(\.\d+)?$/.exec(text);
  if (!match) return null;
  const [, a, b, c] = match;
  const parts = c === undefined ? [0, Number(a), Number(b)] : [Number(a), Number(b), Number(c)];
  if (parts[1] > 59 || parts[2] > 59) return null;
  return parts[0] * 3600 + parts[1] * 60 + parts[2];
}

/**
 * 근거 시각(초)에 대응하는 구간 번호. starts 는 구간별 시작 초(해석할 수 없으면 null)이며 정렬돼 있지 않아도 된다.
 * 정확히 같은 시작 시각이 있으면 그것(여럿이면 앞쪽), 없으면 근거 시각 이전에서 가장 가까운 구간, 근거 시각이 가장 이른 구간보다 앞이면 가장 이른 구간.
 * 시각을 가진 구간이 하나도 없으면 -1.
 */
export function findSegmentIndex(starts: (number | null)[], sec: number): number {
  let earliest = -1;
  let closestBefore = -1;
  starts.forEach((start, index) => {
    if (start === null) return;
    if (earliest === -1 || start < (starts[earliest] as number)) earliest = index;
    if (start <= sec && (closestBefore === -1 || start > (starts[closestBefore] as number))) closestBefore = index;
  });
  return closestBefore !== -1 ? closestBefore : earliest;
}

export interface TranscriptBlock {
  /** 줄머리 시각(초). 시각이 없는 앞머리 줄은 null */
  start: number | null;
  text: string;
}

const LINE_STAMP = /^\s*\[(\d{1,3}:\d{2}(?::\d{2})?(?:\.\d+)?)\]/;
/** 한 줄 전사문의 발화 경계: 공백 뒤에 "[시각] 화자:" 가 이어질 때만(말 속 "[00:12] 쯤 … 시간:" 은 경계가 아니다). 백엔드 agenda_chunker 와 같은 식 */
const INLINE_BOUNDARY = /[ \t]+(?=\[\s*\d{1,3}:\d{2}(?::\d{2})?\s*\]\s*[^\s:：[\]\n.,!?。]{1,20}\s*[:：])/g;

/** 발화 블록 머리의 "[시각]" 과 나머지를 나눈다(시각을 굵게 보이기 위함). 시각이 없으면 stamp 는 빈 문자열 */
export function splitStamp(text: string): { stamp: string; rest: string } {
  const match = /^(\s*\[[^\]\n]*\])([\s\S]*)$/.exec(text);
  return match ? { stamp: match[1], rest: match[2] } : { stamp: "", rest: text };
}

/** 인용문 대조용: 글자·숫자만 남긴다(공백·구두점 무시, 백엔드 대조와 같은 취지) */
export function normalizeForMatch(text: string | null | undefined): string {
  return (text ?? "").replace(/[^\p{L}\p{N}]+/gu, "");
}

/**
 * 근거 시각·인용문으로 렌더링된 발화 번호를 찾는다(1:1 대응, 못 찾으면 -1).
 * 1) 시작 시각이 같은 발화: 하나면 그것, 여럿이면 인용문이 든 것(없으면 첫 번째)
 * 2) 시각으로 못 찾고 인용문이 있으면 인용문 텍스트 검색(여럿이면 근거 시각에 가장 가까운 것)
 * 3) 둘 다 못 찾으면 시각 이전 가장 가까운 발화(68-2 의 기존 동작). 시각도 없고 인용문도 못 찾으면 -1(안내)
 */
export function resolveEvidenceIndex(starts: (number | null)[], texts: string[], sec: number | null, quote: string | null | undefined): number {
  const q = normalizeForMatch(quote);
  const holdsQuote = (i: number) => q !== "" && normalizeForMatch(texts[i]).includes(q);
  if (sec !== null) {
    const exact = starts.flatMap((start, i) => (start === sec ? [i] : []));
    if (exact.length === 1) return exact[0];
    if (exact.length > 1) return exact.find(holdsQuote) ?? exact[0];
  }
  if (q !== "") {
    const found = texts.flatMap((_, i) => (holdsQuote(i) ? [i] : []));
    if (found.length > 0) {
      if (sec === null) return found[0];
      const dist = (i: number) => (starts[i] === null ? Infinity : Math.abs((starts[i] as number) - sec));
      return found.reduce((best, i) => (dist(i) < dist(best) ? i : best));
    }
  }
  return sec !== null ? findSegmentIndex(starts, sec) : -1;
}

/**
 * 구간 목록이 없는 전사문 본문을 "[mm:ss] 화자: 내용" 줄 단위 블록으로 나눈다. 시각이 붙은 줄이 새 블록을 시작하고,
 * 시각이 없는 줄은 앞 블록에 이어 붙인다(첫 줄이 시각 없이 시작하면 시각 없는 블록).
 * 줄바꿈 없이 한 줄로 저장된 본문은 "[시각] 화자:" 경계(백엔드 B-1a 규칙과 동일)에서 먼저 나눈다. 표시용 변환이며 저장 데이터는 바꾸지 않는다.
 * 시각이 붙은 줄이 하나도 없으면 빈 배열(줄 단위 이동을 지원하지 않는 본문).
 */
export function parseTranscriptBlocks(text: string): TranscriptBlock[] {
  const blocks: TranscriptBlock[] = [];
  const lines = text.replace(INLINE_BOUNDARY, "\n").split("\n");
  lines.forEach((line, index) => {
    const withBreak = index < lines.length - 1 ? `${line}\n` : line;
    const stamp = LINE_STAMP.exec(line);
    if (stamp) blocks.push({ start: parseSeconds(stamp[1]), text: withBreak });
    else if (blocks.length === 0) blocks.push({ start: null, text: withBreak });
    else blocks[blocks.length - 1].text += withBreak;
  });
  return blocks.some((block) => block.start !== null) ? blocks : [];
}
