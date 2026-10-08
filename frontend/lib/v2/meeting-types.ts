/*
 * 회의 유형(작업 69-2). 서버에는 영문 코드를 보내고, 화면에는 한글 라벨을 쓴다. 올리기·상세 화면이 함께 쓴다.
 * 유형이 없는 이전 회의록은 "미지정"으로 보인다.
 */
export type MeetingType = "regular" | "irregular" | "project" | "external" | "other";

/** 화면에 보이는 순서 */
export const MEETING_TYPES: readonly MeetingType[] = ["regular", "irregular", "project", "external", "other"];

export const MEETING_TYPE_LABEL: Record<MeetingType, string> = {
  regular: "정기회의",
  irregular: "비정기회의",
  project: "프로젝트 회의",
  external: "외부(영업·상담) 회의",
  other: "기타",
};

export const MEETING_TYPE_UNSET_LABEL = "미지정";

/** 상세 응답의 meetingType → 한글 라벨. 없거나(null·undefined) 모르는 코드면 "미지정" */
export function meetingTypeLabel(code: string | null | undefined): string {
  return (code && MEETING_TYPE_LABEL[code as MeetingType]) || MEETING_TYPE_UNSET_LABEL;
}
