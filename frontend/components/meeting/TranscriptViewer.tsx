"use client";

import { useEffect, useId, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Button, Modal } from "@/components/mono";
import { downloadTextFile, transcriptFileName } from "@/lib/download-text";
import { fitRect, offsetFromCenter, resizeRect, type Limits, type Rect } from "@/lib/resize-rect";
import { applySpeakerNames, type SpeakerNames } from "@/lib/speaker-names";

interface TranscriptViewerProps {
  transcriptText: string | null;
  /** 다운로드 파일명에 쓴다: "{제목}_전사원문.txt" / "{제목}_전사원문_이름적용.txt" (제목이 비면 회의 ID) */
  meetingTitle: string;
  meetingId: string;
  /** v1.9.9: 저장된 화자 이름. 모달 표시와 "이름 적용 다운로드"에만 적용하고 원본은 그대로 둔다 */
  speakerNames?: SpeakerNames;
}

/* ------------------------------------------------------------------ *
 * 창 크기 조절 (네 변 + 네 모서리)
 * 끄는 변만 움직이고 반대쪽 변은 고정한다. 창은 공용 Modal이 화면 가운데에 두므로,
 * 새 위치(Rect)를 계산한 뒤 "가운데에서 얼마나 밀지"(translate)와 본문 크기로 바꿔 적용한다.
 * 계산은 lib/resize-rect.ts 순수 함수(단위 테스트 있음). 조절 전(null)에는 기존 기본 크기를 그대로 쓴다.
 * ------------------------------------------------------------------ */
type Layout = { width: number; height: number; x: number; y: number };
/** 조절 방향: x/y 각각 -1(왼/위 변), 0(안 움직임), 1(오른/아래 변) + 커서·위치 클래스 */
type Edge = { x: -1 | 0 | 1; y: -1 | 0 | 1; className: string };

const MIN_BODY_WIDTH = 288; // 18rem: 다운로드 버튼 두 개가 두 줄로 접혀도 읽을 수 있는 폭
const MIN_BODY_HEIGHT = 256; // 16rem: 다운로드 줄 + 원문 몇 줄 + 닫기 버튼
const VIEWPORT_MARGIN = 16; // Modal 바깥 여백 p-4 와 같게

// 끌 수 있는 곳: 변 4개(두께 8px) + 모서리 4개(16px, 변보다 위). 창(relative)의 테두리에 걸쳐 놓인다.
const EDGES: Edge[] = [
  { x: 0, y: -1, className: "inset-x-4 -top-1 h-2 cursor-ns-resize" },
  { x: 0, y: 1, className: "inset-x-4 -bottom-1 h-2 cursor-ns-resize" },
  { x: -1, y: 0, className: "inset-y-4 -left-1 w-2 cursor-ew-resize" },
  { x: 1, y: 0, className: "inset-y-4 -right-1 w-2 cursor-ew-resize" },
  { x: -1, y: -1, className: "-left-1 -top-1 size-4 cursor-nwse-resize" },
  { x: 1, y: 1, className: "-right-1 -bottom-1 size-4 cursor-nwse-resize" },
  { x: 1, y: -1, className: "-right-1 -top-1 size-4 cursor-nesw-resize" },
  { x: -1, y: 1, className: "-left-1 -bottom-1 size-4 cursor-nesw-resize" },
];

/** 스크롤바를 뺀 화면 크기 (Modal 바깥 영역 fixed inset-0 과 같은 기준) */
const viewport = () => ({ width: document.documentElement.clientWidth, height: document.documentElement.clientHeight });

/**
 * "전사 원문 보기" 버튼 + 원문 모달. 원문이 없으면(null) 버튼을 잠그고 이유를 글자로 보여 준다.
 * 모달 맨 위에 다운로드 두 가지: 원본(화자N 그대로) / 이름 적용. 둘 다 글자 치환 외에는 가공하지 않는다.
 * 창의 네 변·네 모서리를 끌어 가로·세로 크기를 조절할 수 있다(반대쪽 변 고정, 원문 상자가 함께 늘고 줄며 넘치면 상자 안에서 스크롤).
 */
export function TranscriptViewer({ transcriptText, meetingTitle, meetingId, speakerNames = {} }: TranscriptViewerProps) {
  const [open, setOpen] = useState(false);
  const [layout, setLayout] = useState<Layout | null>(null);
  const [dragging, setDragging] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  // 창 테두리·여백·제목이 차지하는 크기(창 크기 - 본문 크기). 끌기를 시작할 때 실제로 잰다.
  const chromeRef = useRef({ width: 0, height: 0 });
  const hintId = useId();
  const namedHintId = useId();
  const unavailable = transcriptText === null;
  // null·빈 문자열(공백만 있는 경우 포함)이면 받을 내용이 없으므로 다운로드 버튼을 숨긴다
  const downloadable = transcriptText !== null && transcriptText.trim() !== "";
  // 저장된 이름이 없으면 이름 적용본 = 원본이라 버튼을 잠근다
  const hasNames = Object.keys(speakerNames).length > 0;

  // 창을 열면 처음 포커스를 "닫기"로 옮긴다 (공용 Modal은 첫 포커스 요소 = "원본 다운로드"에 준다).
  // 자식(Modal)의 effect가 부모보다 먼저 실행되므로, Modal이 여는 버튼을 기억하고 첫 포커스를 준 뒤 여기서 덮어쓴다.
  // → 닫으면 포커스는 그대로 "전사 원문 보기"로 돌아간다.
  useEffect(() => {
    if (open) closeRef.current?.focus();
  }, [open]);

  const openViewer = () => {
    setLayout(null); // 열 때마다 기존 기본 크기·가운데 위치로 시작
    setOpen(true);
  };

  const limits = (): Limits => {
    const { width, height } = viewport();
    return {
      viewportWidth: width,
      viewportHeight: height,
      margin: VIEWPORT_MARGIN,
      minWidth: MIN_BODY_WIDTH + chromeRef.current.width,
      minHeight: MIN_BODY_HEIGHT + chromeRef.current.height,
    };
  };

  /** 창 위치(Rect) → 본문 크기 + 가운데에서 민 거리 */
  const applyRect = (rect: Rect) => {
    const { width, height } = viewport();
    const offset = offsetFromCenter(rect, width, height);
    setLayout({
      width: rect.right - rect.left - chromeRef.current.width,
      height: rect.bottom - rect.top - chromeRef.current.height,
      x: offset.x,
      y: offset.y,
    });
  };

  const dialogRect = (): Rect | null => {
    const dialog = bodyRef.current?.closest<HTMLElement>('[role="dialog"]');
    if (!dialog) return null;
    const { left, top, right, bottom } = dialog.getBoundingClientRect();
    return { left, top, right, bottom };
  };

  /**
   * 변·모서리를 누르면 그때의 창 위치를 기준으로 끄는 변만 움직인다.
   * setPointerCapture: 포인터가 창·브라우저 밖으로 나가도 놓을 때까지 이벤트를 계속 받는다.
   * preventDefault: 끄는 동안 글자 선택을 막고, 이어지는 mousedown(바깥 클릭으로 닫기 판정)도 생기지 않게 한다.
   */
  const startResize = (edge: Edge) => (event: ReactPointerEvent<HTMLDivElement>) => {
    const body = bodyRef.current;
    const start = dialogRect();
    if (!body || !start || event.button !== 0) return;
    event.preventDefault();
    const bodyBox = body.getBoundingClientRect();
    chromeRef.current = {
      width: start.right - start.left - bodyBox.width,
      height: start.bottom - start.top - bodyBox.height,
    };
    const handle = event.currentTarget;
    handle.setPointerCapture(event.pointerId);
    setDragging(true);
    const origin = { x: event.clientX, y: event.clientY };

    const onMove = (move: PointerEvent) => {
      applyRect(resizeRect(start, edge, move.clientX - origin.x, move.clientY - origin.y, limits()));
    };
    const onEnd = () => {
      setDragging(false);
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onEnd);
      handle.removeEventListener("pointercancel", onEnd);
      handle.removeEventListener("lostpointercapture", onEnd);
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onEnd);
    handle.addEventListener("pointercancel", onEnd);
    handle.addEventListener("lostpointercapture", onEnd);
  };

  // 크기를 조절한 뒤 브라우저 창이 작아지면: 조절한 창을 화면 안으로 다시 맞춘다
  // (applyRect·limits·dialogRect 는 ref·상수·DOM만 읽으므로 의존성은 open·resized 로 충분)
  const resized = layout !== null;
  useEffect(() => {
    if (!open || !resized) return;
    const onWindowResize = () => {
      const rect = dialogRect();
      if (rect) applyRect(fitRect(rect, limits()));
    };
    window.addEventListener("resize", onWindowResize);
    return () => window.removeEventListener("resize", onWindowResize);
  }, [open, resized]); // eslint-disable-line react-hooks/exhaustive-deps

  const downloadOriginal = () => {
    if (!downloadable) return;
    downloadTextFile(transcriptFileName(meetingTitle, meetingId, "original"), transcriptText);
  };

  const downloadNamed = () => {
    if (!downloadable || !hasNames) return;
    // 다운로드 직전에 적용: 화자N → 이름 치환만 하고 줄바꿈 등은 그대로
    downloadTextFile(
      transcriptFileName(meetingTitle, meetingId, "named"),
      applySpeakerNames(transcriptText, speakerNames),
    );
  };

  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button
        variant="secondary"
        size="sm"
        disabled={unavailable}
        aria-describedby={unavailable ? hintId : undefined}
        onClick={openViewer}
      >
        전사 원문 보기
      </Button>
      {unavailable ? (
        <span id={hintId} className="text-xs text-mn-muted">
          전사 전이거나 원문이 없습니다
        </span>
      ) : null}

      {/*
        확인 버튼이 필요 없는 읽기 전용 모달이라 닫기 버튼은 children에 둔다 (ESC·바깥 클릭으로도 닫힘).
        panelClassName: 기본 폭 제한(max-w-md)을 풀고 relative로 둬서 크기 조절 손잡이가 창 테두리에 붙게 한다.
      */}
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="전사 원문"
        panelClassName="relative w-auto max-w-none"
        panelStyle={layout ? { translate: `${layout.x}px ${layout.y}px` } : undefined}
      >
        {/*
          본문 영역. 조절 전: 기존 기본 크기(폭 = 기존 창 폭 28rem에서 여백·테두리를 뺀 값, 높이 = 내용, 화면 한도 안).
          조절 후: 계산한 크기(항상 화면 여백 안). 원문 상자만 줄어들어 위쪽 다운로드 버튼과 닫기 버튼은 잘리지 않는다.
        */}
        <div
          ref={bodyRef}
          className={`flex flex-col ${dragging ? "select-none" : ""} ${
            layout ? "" : "max-h-[calc(100dvh_-_118px)] w-[min(calc(25rem_-_2px),calc(100vw_-_82px))]"
          }`}
          style={layout ? { width: layout.width, height: layout.height } : undefined}
        >
          {downloadable ? (
            <div className="flex shrink-0 flex-wrap items-center gap-2">
              <Button variant="secondary" size="sm" aria-label="원본 다운로드 (.txt 파일)" onClick={downloadOriginal}>
                원본 다운로드
              </Button>
              <Button
                variant="secondary"
                size="sm"
                aria-label="이름 적용 다운로드 (.txt 파일)"
                aria-describedby={hasNames ? undefined : namedHintId}
                disabled={!hasNames}
                onClick={downloadNamed}
              >
                이름 적용 다운로드
              </Button>
              {hasNames ? null : (
                <span id={namedHintId} className="text-xs text-mn-muted">
                  저장된 화자 이름이 없어 원본과 같습니다
                </span>
              )}
            </div>
          ) : null}
          {/*
            tabIndex=0: 키보드로도 스크롤할 수 있게 포커스를 받는 영역.
            조절 전 높이는 기존과 같다(기본 60vh, 최소 12rem, 최대 = 화면 높이 - 18rem). 조절 후에는 남는 공간을 채운다.
          */}
          <div
            tabIndex={0}
            role="region"
            aria-label="전사 원문"
            className={`mn-focus mt-2 overflow-y-auto whitespace-pre-wrap rounded-mn-control border border-mn-border bg-mn-bg p-3 font-mn-mono text-xs leading-5 text-mn-text ${
              layout
                ? "min-h-0 flex-1"
                : "h-[min(60vh,calc(100dvh_-_18rem))] min-h-[min(12rem,calc(100dvh_-_18rem))] max-h-[calc(100dvh_-_18rem)]"
            }`}
          >
            {transcriptText === null ? null : applySpeakerNames(transcriptText, speakerNames)}
          </div>
          <div className="mt-4 flex shrink-0 justify-end">
            <Button ref={closeRef} variant="secondary" onClick={() => setOpen(false)}>
              닫기
            </Button>
          </div>
        </div>

        {/* 크기 조절 손잡이: 마우스·터치 전용 장식이라 스크린리더에서 숨긴다 (키보드 사용자는 기본 크기 + 상자 스크롤) */}
        {EDGES.map((edge) => (
          <div
            key={`${edge.x},${edge.y}`}
            aria-hidden="true"
            data-edge={`${edge.y === -1 ? "top" : edge.y === 1 ? "bottom" : ""}${edge.x === -1 ? "left" : edge.x === 1 ? "right" : ""}`}
            onPointerDown={startResize(edge)}
            className={`absolute touch-none ${edge.className}`}
          />
        ))}
      </Modal>
    </div>
  );
}
