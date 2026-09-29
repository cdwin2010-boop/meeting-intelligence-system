"use client";

import { useId, useState } from "react";
import { Button, Modal } from "@/components/mono";

/** "전사 원문 보기" 버튼 + 원문 모달. 원문이 없으면(null) 버튼을 잠그고 이유를 글자로 보여 준다. */
export function TranscriptViewer({ transcriptText }: { transcriptText: string | null }) {
  const [open, setOpen] = useState(false);
  const hintId = useId();
  const unavailable = transcriptText === null;

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
