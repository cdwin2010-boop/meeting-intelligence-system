"use client";

/*
 * 회의록 질의응답 서랍 (작업 89-2a, 계약 docs/contracts/meeting-chat-v1.json)
 * - 열기 버튼은 서버가 준 허용 동작 ask_meeting 이 있을 때만 그린다(직급을 다시 판단하지 않는다).
 * - PC: 오른쪽 서랍, 모바일: 바텀시트. 뒤 화면(전사문)을 계속 누를 수 있는 비모달 창이다(근거를 눌러 전사문으로 이동하려고).
 * - 답변은 서버가 검증을 마친 뒤 한 번에 오므로 "근거 확인 중" 다음에 완성된 답변을 보인다.
 * - 질문·답변은 이 컴포넌트의 메모리에만 있다(최대 4턴). 저장소·콘솔 로그에 남기지 않고 새로고침하면 사라진다.
 * - 서랍을 닫으면 진행 중 요청을 중단하고, 같은 회의록 안에서는 대화를 유지한다. 회의록이 바뀌면(key) 통째로 사라진다.
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { Badge, Button } from "@/components/mono";
import { can, MEETING_ACTION } from "@/lib/v2/actions";
import { isAbortError } from "@/lib/v2/errors";
import {
  askMeeting,
  chatErrorText,
  ChatError,
  HISTORY_MAX_TURNS,
  QUESTION_MAX,
  questionLength,
  type ChatAnswer,
  type ChatCitation,
  type ChatErrorText,
  type QuickPrompt,
} from "@/lib/v2/meeting-chat";

/** 추천 질문 칩: 누르면 quickPrompt 로 전송한다 */
const QUICK_CHIPS: { key: QuickPrompt; label: string }[] = [
  { key: "decisions", label: "결정사항" },
  { key: "my_tasks", label: "내 업무" },
  { key: "schedule", label: "일정" },
];

interface Turn {
  id: number;
  question: string;
  answer: ChatAnswer;
}

interface MeetingChatProps {
  meetingId: number;
  /** 서버가 내려준 회의록 단위 허용 동작 */
  allowed: readonly string[] | undefined;
  /** 근거(발화) 클릭: 전사문 위치 이동과 음성 이동은 부모가 한다 */
  onCitation: (citation: ChatCitation, trigger: HTMLElement) => void;
}

function CitationList({ citations, onCitation }: { citations: ChatCitation[]; onCitation: MeetingChatProps["onCitation"] }) {
  if (citations.length === 0) return null;
  return (
    <div className="mt-3 flex flex-col gap-2">
      <h4 className="text-[13px] font-medium text-mn-muted">근거</h4>
      <ul aria-label="근거 목록" className="flex flex-wrap gap-2">
        {citations.map((c, index) => (
          <li key={`${c.refId}-${index}`}>
            {c.startSec !== null ? (
              <button
                type="button"
                data-testid="chat-citation"
                onClick={(event) => onCitation(c, event.currentTarget)}
                className="mn-focus rounded-mn-control border border-mn-control px-2 py-1 font-mn-mono text-[13px] text-mn-text hover:bg-mn-elevated"
              >
                {c.label}
              </button>
            ) : (
              <span data-testid="chat-citation-static" className="inline-block rounded-mn-control border border-mn-border px-2 py-1 text-[13px] text-mn-muted">
                {c.label}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function MeetingChat({ meetingId, allowed, onCitation }: MeetingChatProps) {
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ChatErrorText | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const openerRef = useRef<HTMLButtonElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const nextId = useRef(1);
  // 화면 메모리에만 두는 대화(서버 요청의 history 로도 쓴다). 최대 4턴
  const turnsRef = useRef<Turn[]>([]);
  turnsRef.current = turns;

  const cancel = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
  }, []);

  // 화면을 떠나면 진행 중 요청 중단
  useEffect(() => cancel, [cancel]);

  // 열리면 입력창에 포커스
  useEffect(() => {
    if (open) textareaRef.current?.focus();
  }, [open]);

  // 전송이 끝나 입력창이 다시 켜지면 포커스를 돌려준다(잠긴 동안에는 포커스를 줄 수 없다)
  useEffect(() => {
    if (open && !pending) textareaRef.current?.focus();
  }, [open, pending]);

  // 새 답변·진행 표시가 생기면 맨 아래로
  useEffect(() => {
    const list = listRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [turns, pending, error]);

  const send = useCallback(
    async (rawQuestion: string, quickPrompt: QuickPrompt | null) => {
      const question = rawQuestion.trim();
      if (!question || controllerRef.current) return;
      if (questionLength(question) > QUESTION_MAX) return;
      const controller = new AbortController();
      controllerRef.current = controller;
      setPending(true);
      setError(null);
      setNotice(null);
      try {
        const history = turnsRef.current.slice(-HISTORY_MAX_TURNS).map((t) => ({ question: t.question.slice(0, 1000), answer: t.answer.text.slice(0, 1000) }));
        const answer = await askMeeting(meetingId, { question, history, quickPrompt }, controller.signal);
        const turn: Turn = { id: nextId.current++, question, answer };
        setTurns((prev) => [...prev, turn].slice(-HISTORY_MAX_TURNS));
        setInput("");
      } catch (e) {
        if (isAbortError(e)) {
          setNotice("질문을 취소했습니다.");
        } else {
          setError(chatErrorText(e instanceof ChatError ? e : new ChatError("status", "질문을 처리하지 못했습니다.")));
        }
        setInput(question); // 다시 시도할 수 있게 입력을 돌려놓는다
      } finally {
        if (controllerRef.current === controller) controllerRef.current = null;
        setPending(false);
      }
    },
    [meetingId],
  );

  const close = useCallback(() => {
    cancel();
    setOpen(false);
    openerRef.current?.focus(); // 닫으면 여는 버튼으로 포커스 복귀
  }, [cancel]);

  if (!can(allowed, MEETING_ACTION.askMeeting)) return null;

  const length = questionLength(input);
  const tooLong = length > QUESTION_MAX;

  function handleCitation(citation: ChatCitation, trigger: HTMLElement) {
    onCitation(citation, trigger);
    // 좁은 화면에서는 시트가 전사문을 가리므로 접는다(대화는 유지)
    if (window.matchMedia("(max-width: 767px)").matches) {
      cancel();
      setOpen(false);
    }
  }

  return (
    <>
      <Button
        ref={openerRef}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => (open ? close() : setOpen(true))}
        className="max-md:min-h-11"
      >
        질문하기
      </Button>
      {open ? (
        <section
          id={panelId}
          role="dialog"
          aria-label="회의록 질의응답"
          data-testid="chat-panel"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.stopPropagation();
              close();
            }
          }}
          className="fixed inset-x-0 bottom-0 z-40 flex max-h-[80dvh] flex-col border border-mn-border bg-mn-surface max-md:rounded-t-mn-card md:inset-x-auto md:inset-y-0 md:right-0 md:max-h-none md:w-[420px] md:border-y-0 md:border-r-0"
        >
          <header className="flex items-center justify-between gap-3 border-b border-mn-border px-4 py-3">
            <div className="flex flex-col gap-1">
              <h2 className="text-base font-semibold tracking-tight text-mn-text">회의록 질문</h2>
              <Badge>범위: 이 회의록</Badge>
            </div>
            <Button size="sm" onClick={close} className="max-md:min-h-11">
              닫기
            </Button>
          </header>

          <div ref={listRef} className="flex min-h-[160px] flex-1 flex-col gap-4 overflow-y-auto px-4 py-4" aria-live="polite">
            {turns.length === 0 && !pending && !error ? (
              <p className="text-sm text-mn-muted">이 회의록의 요약·결정·업무를 근거로 답합니다. 아래 추천 질문을 누르거나 직접 질문해 보세요.</p>
            ) : null}
            {turns.map((turn) => (
              <div key={turn.id} className="flex flex-col gap-2" data-testid="chat-turn">
                <p className="self-end rounded-mn-card bg-mn-elevated px-3 py-2 text-sm text-mn-text [overflow-wrap:anywhere]">{turn.question}</p>
                <article aria-label="답변" className="rounded-mn-card border border-mn-border bg-mn-bg p-3" data-testid="chat-answer">
                  {turn.answer.confirmedPendingIncluded ? (
                    <div className="mb-2" data-testid="chat-pending-badge">
                      <Badge>확정 전 내용 포함</Badge>
                    </div>
                  ) : null}
                  <p className="whitespace-pre-line text-sm leading-6 text-mn-text [overflow-wrap:anywhere]">{turn.answer.text}</p>
                  <CitationList citations={turn.answer.citations} onCitation={handleCitation} />
                </article>
              </div>
            ))}
            {pending ? (
              <div role="status" className="flex items-center justify-between gap-3 rounded-mn-card border border-mn-border px-3 py-2" data-testid="chat-pending">
                <span className="text-sm text-mn-muted">근거 확인 중…</span>
                <Button size="sm" onClick={cancel} className="max-md:min-h-11">
                  취소
                </Button>
              </div>
            ) : null}
            {error ? (
              <p role="alert" className="flex flex-col gap-1 rounded-mn-card border border-mn-border px-3 py-2 text-sm" data-testid="chat-error">
                <span className="font-medium text-mn-text">{error.cause}</span>
                <span className="text-mn-muted">{error.action}</span>
              </p>
            ) : null}
            {notice ? (
              <p role="status" className="text-sm text-mn-muted">
                {notice}
              </p>
            ) : null}
          </div>

          <div className="flex flex-col gap-3 border-t border-mn-border px-4 py-3">
            <ul aria-label="추천 질문" className="flex flex-wrap gap-2">
              {QUICK_CHIPS.map((chip) => (
                <li key={chip.key}>
                  <button
                    type="button"
                    disabled={pending}
                    onClick={() => void send(chip.label, chip.key)}
                    className="mn-focus rounded-full border border-mn-control px-3 py-1 text-[13px] text-mn-text hover:bg-mn-elevated disabled:cursor-not-allowed disabled:opacity-50 max-md:min-h-11"
                  >
                    {chip.label}
                  </button>
                </li>
              ))}
            </ul>
            <p className="text-[13px] text-mn-muted">이 회의록 안의 내용만 답변합니다. 다른 회의나 프로젝트 전체에 대한 질문은 답하지 않습니다.</p>
            <form
              className="flex flex-col gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                void send(input, null);
              }}
            >
              <label htmlFor={`${panelId}-q`} className="sr-only">
                질문
              </label>
              <textarea
                id={`${panelId}-q`}
                ref={textareaRef}
                rows={3}
                value={input}
                disabled={pending}
                aria-invalid={tooLong}
                aria-describedby={`${panelId}-count`}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                    event.preventDefault();
                    void send(input, null);
                  }
                }}
                placeholder="이 회의록에 대해 질문하세요"
                className="mn-focus w-full resize-none rounded-mn-control border border-mn-control bg-mn-bg px-3 py-2 text-sm text-mn-text outline-none placeholder:text-mn-muted disabled:opacity-50"
              />
              <div className="flex items-center justify-between gap-3">
                <span id={`${panelId}-count`} className="text-[13px]" data-testid="chat-count">
                  {tooLong ? (
                    <span role="alert" className="text-mn-text">
                      질문은 {QUESTION_MAX}자까지 입력할 수 있습니다. <span className="font-mn-mono">({length}/{QUESTION_MAX})</span>
                    </span>
                  ) : (
                    <span className="font-mn-mono text-mn-muted">
                      {length}/{QUESTION_MAX}
                    </span>
                  )}
                </span>
                <Button type="submit" variant="primary" disabled={pending || tooLong || input.trim() === ""} className="max-md:min-h-11">
                  보내기
                </Button>
              </div>
            </form>
          </div>
        </section>
      ) : null}
    </>
  );
}
