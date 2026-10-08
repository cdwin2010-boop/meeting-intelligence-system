"use client";

/*
 * 파일 선택 공용 부품(수정 회의록 업로드 팝업·회의록 올리기 화면이 같이 쓴다).
 * - 별도 버튼 박스("선택" 버튼, 고른 뒤 "파일 다시 선택")와 상태 글자를 보여 준다.
 *   고르기 전: 흐린 "선택한 파일 없음" / 고른 뒤: 파일 이름(길면 말줄임, 전체 이름은 title)과 크기
 * - 브라우저 기본 파일 입력은 화면에서 숨기되(sr-only) 접근성 이름(inputLabel)으로 스크린 리더·테스트가 찾을 수 있고, 버튼이 같은 입력을 연다.
 *   버튼은 공용 Button 이라 Enter·Space 와 포커스 링이 그대로 동작한다.
 * - 파일 형식·용량 제한 값은 화면에 쓰지 않는다(accept 는 호출부가 넘긴 값 그대로, 판정은 서버).
 * - 값은 호출부가 가진다(file/onPick). 끌어다 놓기처럼 다른 경로로 file 이 바뀌어도 같은 상태 글자가 보인다.
 */
import { useId, useRef } from "react";

import { Button } from "@/components/mono";

/** 파일 크기 표시(B·KB·MB) */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface FilePickerProps {
  file: File | null;
  /** 파일을 골랐을 때(선택창을 취소하면 부르지 않는다) */
  onPick: (file: File) => void;
  /** 고르기 전 버튼 이름. 예) "수정할 파일 선택" */
  buttonLabel: string;
  /** 고른 뒤 버튼 이름(기본 "파일 다시 선택") */
  pickedButtonLabel?: string;
  /** 숨긴 입력의 접근성 이름. 예) "수정한 파일" */
  inputLabel: string;
  accept?: string;
  disabled?: boolean;
  /** 입력에 연결할 설명 요소 id(선택) */
  describedBy?: string;
  className?: string;
  /** true 면 긴 파일 이름을 말줄임 없이 영역 안에서 줄바꿈한다(올리기 화면). 기본은 한 줄 말줄임(수정 회의록 업로드 팝업 등 기존 화면) */
  wrapName?: boolean;
}

export function FilePicker({
  file,
  onPick,
  buttonLabel,
  pickedButtonLabel = "파일 다시 선택",
  inputLabel,
  accept,
  disabled = false,
  describedBy,
  className = "",
  wrapName = false,
}: FilePickerProps) {
  const statusId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <div className={`flex flex-wrap items-center gap-3 ${wrapName ? "min-w-0 max-w-full" : ""} ${className}`}>
      <input
        ref={inputRef}
        type="file"
        aria-label={inputLabel}
        aria-describedby={describedBy}
        accept={accept}
        tabIndex={-1}
        disabled={disabled}
        onChange={(event) => {
          const picked = event.target.files?.[0] ?? null;
          // 같은 파일을 고쳐 다시 골라도 변경 알림이 오도록 입력값을 비운다(고른 파일은 호출부 상태에 있다)
          event.target.value = "";
          if (picked) onPick(picked);
        }}
        className="sr-only"
      />
      <Button variant="secondary" aria-describedby={statusId} disabled={disabled} onClick={() => inputRef.current?.click()}>
        {file ? pickedButtonLabel : buttonLabel}
      </Button>
      <span id={statusId} aria-live="polite" className={`flex min-w-0 gap-2 text-sm ${wrapName ? "max-w-full items-baseline" : "items-baseline"}`}>
        {file ? (
          <>
            <span title={file.name} className={`min-w-0 text-mn-text ${wrapName ? "[overflow-wrap:anywhere]" : "truncate"}`}>
              {file.name}
            </span>
            <span className="shrink-0 font-mn-mono text-[13px] text-mn-muted">{formatFileSize(file.size)}</span>
          </>
        ) : (
          <span className="text-mn-muted">선택한 파일 없음</span>
        )}
      </span>
    </div>
  );
}
