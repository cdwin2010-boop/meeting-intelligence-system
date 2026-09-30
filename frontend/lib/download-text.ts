/*
 * 텍스트를 브라우저에서 파일로 저장합니다 (서버 호출 없음 → 목/실서버 모드에서 똑같이 동작).
 */

// 윈도우 파일명에 쓸 수 없는 문자: \ / : * ? " < > |
const INVALID_FILENAME_CHARS = /[\\/:*?"<>|]/g;

/** 파일명에 쓸 수 없는 문자를 지운다. 지우고 나서 비면 fallback(예: 회의 ID)을 쓴다. */
export function toSafeFileBaseName(name: string, fallback: string): string {
  const cleaned = name.replace(INVALID_FILENAME_CHARS, "").trim();
  return cleaned || fallback.replace(INVALID_FILENAME_CHARS, "").trim() || "download";
}

/**
 * 전사 원문 다운로드 파일명.
 *  original → "{제목}_전사원문.txt" (화자N 원본)
 *  named    → "{제목}_전사원문_이름적용.txt" (화자 이름 적용본)
 */
export function transcriptFileName(title: string, meetingId: string, kind: "original" | "named"): string {
  const base = `${toSafeFileBaseName(title, meetingId)}_전사원문`;
  return kind === "named" ? `${base}_이름적용.txt` : `${base}.txt`;
}

/**
 * text를 UTF-8 .txt 파일로 내려받는다. 내용은 가공하지 않는다(줄바꿈 추가·변환 없음).
 * 맨 앞 BOM(﻿): 윈도우 메모장이 UTF-8로 알아보게 해 한글이 깨지지 않게 한다.
 */
export function downloadTextFile(fileName: string, text: string): void {
  const blob = new Blob(["﻿", text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.style.display = "none";
  document.body.appendChild(link);
  link.click();
  link.remove();
  // 클릭 직후 바로 해제하면 일부 브라우저에서 다운로드가 시작되기 전에 끊길 수 있어 한 박자 뒤에 정리
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
