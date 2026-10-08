/*
 * 회의록 자료 종류(작업 73-1). 서버에는 영문 코드를 보내고 화면에는 한글 라벨을 쓴다.
 * audio=음성 파일(기본), transcript_txt=자료 파일(txt, STT 없이 전사문으로 읽음). 응답에 필드가 없으면 audio 로 본다.
 */
export type SourceKind = "audio" | "transcript_txt";

export const SOURCE_KINDS: readonly SourceKind[] = ["audio", "transcript_txt"];

export const SOURCE_KIND_LABEL: Record<SourceKind, string> = { audio: "음성 파일", transcript_txt: "자료 파일(txt)" };

export const sourceKindOf = (value: string | null | undefined): SourceKind => (value === "transcript_txt" ? "transcript_txt" : "audio");

export const isTranscriptTxtName = (name: string) => name.trim().toLowerCase().endsWith(".txt");
