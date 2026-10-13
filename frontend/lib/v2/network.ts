/*
 * 모바일 올리기 Wi-Fi 안내 판정(작업 88-2). 판정 코드는 이 파일 한 곳에 둔다.
 * - navigator.connection.type 이 "wifi" 또는 "ethernet" 이면 안내 없이 진행한다.
 * - "cellular" 이거나 값을 알 수 없으면(API 미지원·undefined·"unknown"·"none"·"other" 등) 안내를 항상 띄운다(요금 방지 우선).
 * 테스트에서는 navigator 대신 가짜 객체를 넘기거나, 브라우저 테스트에서 navigator.connection 을 주입한다.
 * 다른 파일을 import 하지 않아 Node 내장 test runner 로 바로 검증할 수 있다(lib/network.test.mjs).
 */

export interface ConnectionSource {
  connection?: { type?: string | null } | null;
}

/** 요금 걱정이 없다고 보는 연결 종류(소문자 비교) */
const UNMETERED_TYPES: readonly string[] = ["wifi", "ethernet"];

/** navigator.connection.type 값(없으면 undefined). 읽다가 예외가 나도 undefined */
export function connectionTypeOf(source?: ConnectionSource | null): string | undefined {
  try {
    const type = source?.connection?.type;
    return typeof type === "string" ? type : undefined;
  } catch {
    return undefined;
  }
}

/** 연결 종류만 보고: 안내가 필요한가. wifi·ethernet 만 false */
export function wifiNoticeRequiredFor(type: string | null | undefined): boolean {
  if (typeof type !== "string") return true;
  return !UNMETERED_TYPES.includes(type.trim().toLowerCase());
}

/** 지금 기기에서 올리기 직전에 Wi-Fi 안내가 필요한가. source 를 생략하면 현재 브라우저의 navigator 를 쓴다 */
export function shouldShowWifiNotice(source?: ConnectionSource | null): boolean {
  const target = source === undefined ? (typeof navigator === "undefined" ? null : (navigator as ConnectionSource)) : source;
  return wifiNoticeRequiredFor(connectionTypeOf(target));
}
