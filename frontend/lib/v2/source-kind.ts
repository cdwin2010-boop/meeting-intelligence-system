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

/** 서버 기본 허용 음성 확장자(ALLOWED_AUDIO_EXTENSIONS 기본값). 자동 선택용 추정이며 실제 허용 판정은 서버가 한다 */
const AUDIO_EXTS = ["m4a", "mp3", "mp4", "ogg", "wav", "webm"];

/** 파일 이름의 확장자로 자료 종류를 추정한다(작업 85-2). .xlsx → 엑셀, .txt → txt, 음성 확장자 → 음성, 그 밖은 null */
export function kindForFileName(name: string): SourceKind | null {
  const dot = name.trim().toLowerCase().lastIndexOf(".");
  const ext = dot >= 0 ? name.trim().toLowerCase().slice(dot + 1) : "";
  if (ext === "xlsx") return "transcript_xlsx";
  if (ext === "txt") return "transcript_txt";
  return AUDIO_EXTS.includes(ext) ? "audio" : null;
}

/** 자료 종류와 파일 확장자가 어긋날 때 올리기 전에 보이는 안내. 어긋나지 않으면(또는 추정 불가) null */
export function kindMismatchHint(kind: SourceKind, name: string): string | null {
  const detected = kindForFileName(name);
  if (detected === null || detected === kind) return null;
  if (detected === "transcript_xlsx") return "엑셀 파일입니다. 자료 종류를 엑셀로 바꿔 주세요.";
  if (detected === "transcript_txt") return "텍스트 파일입니다. 자료 종류를 텍스트로 바꿔 주세요.";
  return "음성 파일입니다. 자료 종류를 음성 파일로 바꿔 주세요.";
}
