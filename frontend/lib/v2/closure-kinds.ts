/*
 * 업무 종결 구분(작업 70-1). 서버에는 영문 코드를 보내고 화면에는 한글 라벨을 쓴다. 팝업과 상태 칩이 함께 쓴다.
 * 구분이 없는 종결 업무(이전에 종결됐거나 구분 없이 종결된 것)는 칩에 기존 "종결"로 보인다.
 */
export type ClosureKind = "completed" | "forced";

export const CLOSURE_KINDS: readonly ClosureKind[] = ["completed", "forced"];

/** 종결 팝업의 선택지 라벨 */
export const CLOSURE_KIND_OPTION_LABEL: Record<ClosureKind, string> = { completed: "정상 완료", forced: "직권 종료" };

/** 종결 업무의 상태 칩 라벨. 구분 없음(null·undefined·모르는 값)은 "종결" */
export const CLOSURE_KIND_CHIP_LABEL: Record<ClosureKind, string> = { completed: "완료", forced: "직권 종료" };
export const CLOSED_WITHOUT_KIND_LABEL = "종결";

export function closedChipLabel(kind: string | null | undefined): string {
  return (kind && CLOSURE_KIND_CHIP_LABEL[kind as ClosureKind]) || CLOSED_WITHOUT_KIND_LABEL;
}
