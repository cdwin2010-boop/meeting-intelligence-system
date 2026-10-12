/*
 * 처리 3단계 표시(작업 84-1). 서버가 기존 값(작업 상태·전사문 저장 사건)에서 파생해 주는 processing.stage 와 자료 종류로 만든다.
 *  1 준비·변환   음성 파일은 음성을 글자로 바꾸는 단계. 자료 파일(txt·엑셀)은 "음성 변환 건너뜀"
 *  2 업무 추출   전사문에서 업무·회의록 5개 항목을 뽑는 단계
 *  3 완료 정리   결과 저장·상태 확정(처리 중에는 항상 대기, 이 단계가 끝나면 처리 완료)
 * stage: convert(변환 단계) | extract(추출 단계) | done(완료). 필드가 없는 서버 응답이면 null.
 */
import type { SourceKind } from "./source-kind";
import { isTranscriptKind } from "./source-kind";

export type StageState = "done" | "current" | "pending" | "failed" | "skipped";

export interface StageView {
  key: "convert" | "extract" | "finalize";
  number: 1 | 2 | 3;
  label: string;
  state: StageState;
  /** 상태 글자(색과 함께 글자로도 구분) */
  stateLabel: string;
}

const STATE_LABEL: Record<StageState, string> = {
  done: "완료",
  current: "진행 중",
  pending: "대기",
  failed: "여기서 멈춤",
  skipped: "건너뜀",
};

export type StageInput = {
  /** 회의록 상태: processing(처리 중) | failed(실패) 만 단계 표시를 쓴다 */
  status: "processing" | "failed";
  stage: string | null | undefined;
  sourceKind: SourceKind;
};

/** 처리 중·실패 회의록의 3단계. 단계 정보가 없는 서버 응답(stage=null)이면 처리 중만 추정해 보이고 실패는 null(단계를 말하지 않는다) */
export function buildStages({ status, stage, sourceKind }: StageInput): StageView[] | null {
  const transcript = isTranscriptKind(sourceKind);
  const known = stage === "convert" || stage === "extract" || stage === "done";
  if (!known && status === "failed") return null;

  // 어느 단계가 지금(또는 멈춘) 단계인가: 자료 파일은 변환을 건너뛰므로 항상 2단계부터
  const at: 1 | 2 = transcript || stage === "extract" || stage === "done" ? 2 : 1;
  const mark = (n: 1 | 2): StageState => {
    if (n === 1 && transcript) return "skipped";
    if (n < at) return "done";
    if (n === at) return status === "failed" ? "failed" : "current";
    return "pending";
  };
  const first: StageView = {
    key: "convert",
    number: 1,
    label: transcript ? "음성 변환 건너뜀(자료 파일)" : "준비·변환(음성을 글자로)",
    state: mark(1),
    stateLabel: "",
  };
  const second: StageView = { key: "extract", number: 2, label: "업무 추출", state: mark(2), stateLabel: "" };
  const third: StageView = { key: "finalize", number: 3, label: "완료 정리", state: "pending", stateLabel: "" };
  return [first, second, third].map((view) => ({ ...view, stateLabel: STATE_LABEL[view.state] }));
}

/** 실패 때 "어느 단계에서 멈췄는지" 한 줄 */
export function stoppedAtText(stages: StageView[] | null): string | null {
  const failed = stages?.find((s) => s.state === "failed");
  return failed ? `${failed.number}단계(${failed.key === "convert" ? "준비·변환" : "업무 추출"})에서 멈췄습니다.` : null;
}
