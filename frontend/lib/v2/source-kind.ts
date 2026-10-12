/*
 * 회의록 자료 종류(작업 73-1, 84-1). 서버에는 영문 코드를 보내고 화면에는 한글 라벨을 쓴다.
 * audio=음성 파일(기본), transcript_txt=자료 파일(txt), transcript_xlsx=자료 파일(엑셀 .xlsx). 자료 파일은 STT 없이 전사문으로 읽는다.
 * 응답에 필드가 없거나 모르는 값이면 audio 로 본다.
 */
export type SourceKind = "audio" | "transcript_txt" | "transcript_xlsx";

export const SOURCE_KINDS: readonly SourceKind[] = ["audio", "transcript_txt", "transcript_xlsx"];

export const SOURCE_KIND_LABEL: Record<SourceKind, string> = {
  audio: "음성 파일",
  transcript_txt: "자료 파일(txt)",
  transcript_xlsx: "자료 파일(엑셀)",
};

export const sourceKindOf = (value: string | null | undefined): SourceKind =>
  value === "transcript_txt" || value === "transcript_xlsx" ? value : "audio";

/** STT 를 거치지 않는 자료 파일 종류인가 */
export const isTranscriptKind = (kind: SourceKind): boolean => kind === "transcript_txt" || kind === "transcript_xlsx";

/** 자료 종류별 허용 확장자(점 제외). 음성은 서버 설정을 따르므로 null */
export const transcriptExtOf = (kind: SourceKind): "txt" | "xlsx" | null =>
  kind === "transcript_txt" ? "txt" : kind === "transcript_xlsx" ? "xlsx" : null;

export const isTranscriptTxtName = (name: string) => name.trim().toLowerCase().endsWith(".txt");

export const isTranscriptXlsxName = (name: string) => name.trim().toLowerCase().endsWith(".xlsx");

/** 파일 이름이 그 자료 종류의 확장자인가 */
export const matchesTranscriptExt = (kind: SourceKind, name: string): boolean =>
  kind === "transcript_txt" ? isTranscriptTxtName(name) : kind === "transcript_xlsx" ? isTranscriptXlsxName(name) : true;
