/*
 * 등록 보류 목록 한도 판정(작업 88-2). 순수 함수만 둔다(다른 파일을 import 하지 않아 Node test runner 로 검증: lib/pending-limits.test.mjs).
 * 한도 값 자체는 lib/v2/pending-uploads.ts 의 상수 한 곳(PENDING_LIMITS)에서 정한다.
 */

export interface PendingLimits {
  /** 최대 보류 건수 */
  maxCount: number;
  /** 보류 파일 합계 용량 상한(바이트) */
  maxTotalBytes: number;
}

export type LimitViolation =
  | { kind: "count"; maxCount: number }
  | { kind: "size"; maxBytes: number; currentBytes: number; incomingBytes: number };

/** 새 파일을 넣을 수 있는가. 넘으면 어떤 한도인지 돌려준다(건수를 먼저 본다) */
export function checkPendingLimits(existingSizes: readonly number[], incomingBytes: number, limits: PendingLimits): LimitViolation | null {
  if (existingSizes.length + 1 > limits.maxCount) return { kind: "count", maxCount: limits.maxCount };
  const currentBytes = existingSizes.reduce((sum, size) => sum + size, 0);
  if (currentBytes + incomingBytes > limits.maxTotalBytes) {
    return { kind: "size", maxBytes: limits.maxTotalBytes, currentBytes, incomingBytes };
  }
  return null;
}

const MB = 1024 * 1024;
/** 용량 표시(MB, 소수 한 자리) */
export function formatMb(bytes: number): string {
  return `${(bytes / MB).toFixed(1)}MB`;
}

/** 사용자에게 보이는 한국어 안내(어떤 한도를 넘었는지 표시) */
export function describeViolation(violation: LimitViolation): string {
  if (violation.kind === "count") {
    return `보류는 최대 ${violation.maxCount}건까지 저장할 수 있습니다. 보류 목록에서 파일을 정리한 뒤 다시 시도해 주세요.`;
  }
  return (
    `보류 파일 합계가 한도 ${Math.round(violation.maxBytes / MB)}MB를 넘습니다` +
    `(저장됨 ${formatMb(violation.currentBytes)} + 이 파일 ${formatMb(violation.incomingBytes)}). ` +
    "보류 목록에서 파일을 정리한 뒤 다시 시도해 주세요."
  );
}
