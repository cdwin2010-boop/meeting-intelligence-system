"use client";

import { useId, useState } from "react";
import { Button, Modal } from "@/components/mono";
import { downloadTextFile, toSafeFileBaseName } from "@/lib/download-text";

interface TranscriptViewerProps {
  transcriptText: string | null;
  /** 다운로드 파일명에 쓴다: "{제목}_전사원문.txt" (제목이 비면 회의 ID) */
  meetingTitle: string;
  meetingId: string;
}

/**
 * "전사 원문 보기" 버튼 + 원문 모달. 원문이 없으면(null) 버튼을 잠그고 이유를 글자로 보여 준다.
 * 원문이 비어 있지 않으면 "전사 원문 다운로드" 버튼도 보여 준다(화면에 받아 온 텍스트를 그대로 .txt로 저장).
 */
export function TranscriptViewer({ transcriptText, meetingTitle, meetingId }: TranscriptViewerProps) {
  const [open, setOpen] = useState(false);
  const hintId = useId();
  const unavailable = transcriptText === null;
  // null·빈 문자열(공백만 있는 경우 포함)이면 받을 내용이 없으므로 버튼을 숨긴다
  const downloadable = transcriptText !== null && transcriptText.trim() !== "";

  const handleDownload = () => {
    if (!downloadable) return;
    downloadTextFile(`${toSafeFileBaseName(meetingTitle, meetingId)}_전사원문.txt`, transcriptText);
  };

  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button
        variant="secondary"
        size="sm"
        disabled={unavailable}
        aria-describedby={unavailable ? hintId : undefined}
        onClick={() => setOpen(true)}
      >
        전사 원문 보기
      </Button>
      {downloadable ? (
        <Button variant="secondary" size="sm" aria-label="전사 원문 다운로드 (.txt 파일)" onClick={handleDownload}>
          전사 원문 다운로드
        </Button>
      ) : null}
      {unavailable ? (
        <span id={hintId} className="text-xs text-mn-muted">
          전사 전이거나 원문이 없습니다
        </span>
      ) : null}

      {/* 확인 버튼이 필요 없는 읽기 전용 모달이라 닫기 버튼은 children에 둔다 (ESC·백드롭 클릭으로도 닫힘) */}
      <Modal open={open} onClose={() => setOpen(false)} title="전사 원문">
        {/* tabIndex=0: 키보드로도 스크롤할 수 있게 포커스를 받는 영역 */}
        <div
          tabIndex={0}
          role="region"
          aria-label="전사 원문"
          className="mn-focus mt-2 max-h-[60vh] overflow-y-auto whitespace-pre-wrap rounded-mn-control border border-mn-border bg-mn-bg p-3 font-mn-mono text-xs leading-5 text-mn-text"
        >
          {transcriptText}
        </div>
        <div className="mt-4 flex justify-end">
          <Button variant="secondary" onClick={() => setOpen(false)}>
            닫기
          </Button>
        </div>
      </Modal>
    </div>
  );
}
