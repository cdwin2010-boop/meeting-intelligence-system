/*
 * 회의록 질의응답 클라이언트 (작업 89-2a, 계약 docs/contracts/meeting-chat-v1.json)
 * - POST /api/meetings/{id}/chat 은 SSE(meta → delta* → citations → done | error)지만, 서버가 답변을 검증한 뒤 한 번에 내보내므로
 *   본문을 모두 받은 뒤 해석해 완성된 답변 하나로 돌려준다(화면은 그 전까지 "근거 확인 중").
 * - 질문·답변은 어디에도 저장하지 않는다(저장소·콘솔 로그 금지). 이 모듈은 로그를 남기지 않는다.
 * - 화면이 준 signal 로 취소하면 AbortError 를 그대로 던진다(감싸지 않는다).
 */
import { parseErrorBody } from "./errors";
import { API_V2_BASE_URL } from "./http";
import { getToken } from "./token";

export const QUESTION_MAX = 500;
export const HISTORY_MAX_TURNS = 4;
/** 서버가 고정으로 내려주는 "근거 없음" 답변(계약 fixedAnswer) */
export const FIXED_ANSWER = "본 회의록에서 확인할 수 없는 내용입니다";
/** 서버 제한 시간(QA_TIMEOUT_SECONDS 기본 60초)보다 조금 길게. 환경값으로 바꿀 수 있다 */
const CHAT_TIMEOUT_MS = Number(process.env.NEXT_PUBLIC_QA_TIMEOUT_MS ?? "75000") || 75_000;

export type QuickPrompt = "decisions" | "my_tasks" | "schedule";

export interface ChatCitation {
  refId: string;
  /** 전사 발화 순번(요약 근거면 null) */
  utteranceId: number | null;
  speaker: string;
  /** 발화 시작 초. null 이면 전사문으로 이동할 수 없는 근거(요약·업무) */
  startSec: number | null;
  /** 예: "화자2/01:15", "회의록·결정사항" */
  label: string;
}

export interface ChatAnswer {
  text: string;
  citations: ChatCitation[];
  /** 확정 전 업무(또는 확정 대기 회의록) 내용이 답변 자료에 들어 있음 */
  confirmedPendingIncluded: boolean;
  grounded: boolean;
  noAnswer: boolean;
}

export interface ChatHistoryTurn {
  question: string;
  answer: string;
}

export type ChatErrorKind = "gpu_busy" | "llm_unavailable" | "timeout" | "network" | "status";

/** 질의 실패. kind 로 안내 문구를 고른다 */
export class ChatError extends Error {
  constructor(
    public readonly kind: ChatErrorKind,
    message: string,
    public readonly status?: number,
    /** Retry-After 헤더(초). 없으면 undefined */
    public readonly retryAfterSec?: number,
  ) {
    super(message);
    this.name = "ChatError";
  }
}

export interface SseEvent {
  event: string;
  data: unknown;
}

/** SSE 본문 → 이벤트 목록(빈 줄로 구분, event:/data: 줄). 해석할 수 없는 블록은 건너뛴다 */
export function parseSse(text: string): SseEvent[] {
  const events: SseEvent[] = [];
  for (const block of text.replace(/\r\n/g, "\n").split("\n\n")) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (data.length === 0) continue;
    try {
      events.push({ event, data: JSON.parse(data.join("\n")) });
    } catch {
      // 깨진 블록은 무시
    }
  }
  return events;
}

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);

function readCitations(value: unknown): ChatCitation[] {
  if (!isRecord(value) || !Array.isArray(value.items)) return [];
  return value.items.filter(isRecord).map((it) => ({
    refId: String(it.refId ?? ""),
    utteranceId: typeof it.utteranceId === "number" ? it.utteranceId : null,
    speaker: typeof it.speaker === "string" ? it.speaker : "",
    startSec: typeof it.startSec === "number" && Number.isFinite(it.startSec) && it.startSec >= 0 ? it.startSec : null,
    label: typeof it.label === "string" ? it.label : "",
  }));
}

/** 이벤트 목록 → 완성된 답변. error 이벤트나 done 없는 끊긴 스트림은 ChatError */
export function answerFromEvents(events: SseEvent[]): ChatAnswer {
  const err = events.find((e) => e.event === "error");
  if (err) {
    const code = isRecord(err.data) ? String(err.data.code ?? "") : "";
    if (code === "llm_unavailable") throw new ChatError("llm_unavailable", "답변 엔진을 사용할 수 없습니다.");
    throw new ChatError("timeout", "응답이 지연되고 있습니다.");
  }
  const done = events.find((e) => e.event === "done");
  if (!done) throw new ChatError("network", "연결이 끊겼습니다."); // 끝 이벤트 없이 끝남 = 연결 끊김
  const meta = events.find((e) => e.event === "meta");
  const text = events
    .filter((e) => e.event === "delta")
    .map((e) => (isRecord(e.data) && typeof e.data.text === "string" ? e.data.text : ""))
    .join("");
  const d = isRecord(done.data) ? done.data : {};
  const noAnswer = d.noAnswer === true;
  const grounded = d.grounded === true;
  const fixed = noAnswer || !grounded; // 고정 문구 상태: 서버 문구를 그대로 보이고 근거는 숨긴다
  return {
    text: fixed ? text.trim() || FIXED_ANSWER : text,
    citations: fixed ? [] : readCitations(events.find((e) => e.event === "citations")?.data),
    confirmedPendingIncluded: isRecord(meta?.data) && meta.data.confirmedPendingIncluded === true,
    grounded,
    noAnswer,
  };
}

/** 서버의 오류 응답(스트림 시작 전, JSON) → ChatError */
async function errorFromResponse(response: Response): Promise<ChatError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  const { message } = parseErrorBody(response.status, body);
  const detail = isRecord(body) ? body.detail : undefined;
  const code = isRecord(detail) ? String(detail.code ?? "") : "";
  const retry = Number(response.headers.get("Retry-After"));
  const retryAfterSec = Number.isFinite(retry) && retry > 0 ? Math.ceil(retry) : undefined;
  if (response.status === 503 && code === "gpu_busy") return new ChatError("gpu_busy", message, 503, retryAfterSec);
  if (response.status === 503) return new ChatError("llm_unavailable", message, 503);
  return new ChatError("status", message, response.status, retryAfterSec);
}

/** 질문 1건. 대화 이력은 호출자가 화면 메모리에서 넘긴다(최대 4턴, 서버는 저장하지 않는다) */
export async function askMeeting(
  meetingId: number,
  input: { question: string; history: ChatHistoryTurn[]; quickPrompt: QuickPrompt | null },
  signal?: AbortSignal,
): Promise<ChatAnswer> {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, CHAT_TIMEOUT_MS);
  const onOuterAbort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  else signal?.addEventListener("abort", onOuterAbort, { once: true });

  const headers: Record<string, string> = { Accept: "text/event-stream", "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  try {
    const response = await fetch(`${API_V2_BASE_URL}/meetings/${meetingId}/chat`, {
      method: "POST",
      headers,
      cache: "no-store",
      signal: controller.signal,
      body: JSON.stringify({ question: input.question, history: input.history.slice(-HISTORY_MAX_TURNS), quickPrompt: input.quickPrompt }),
    });
    if (!response.ok) throw await errorFromResponse(response);
    return answerFromEvents(parseSse(await response.text()));
  } catch (error) {
    if (signal?.aborted) throw error; // 화면이 취소함 → AbortError 그대로
    if (error instanceof ChatError) throw error;
    if (timedOut) throw new ChatError("timeout", "응답이 지연되고 있습니다.");
    throw new ChatError("network", "연결이 끊겼습니다.");
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onOuterAbort);
  }
}

export interface ChatErrorText {
  /** 원인 한 줄 */
  cause: string;
  /** 조치 한 줄 */
  action: string;
}

/** 실패 → 안내 문구(원인 한 줄 + 조치 한 줄). 질문·답변 원문은 들어가지 않는다 */
export function chatErrorText(error: ChatError): ChatErrorText {
  switch (error.kind) {
    case "gpu_busy":
      return {
        cause: "음성 변환이 진행 중입니다.",
        action: error.retryAfterSec ? `잠시 후 다시 시도해 주세요. (약 ${error.retryAfterSec}초 후)` : "잠시 후 다시 시도해 주세요.",
      };
    case "llm_unavailable":
      return { cause: "답변 엔진을 사용할 수 없습니다.", action: "관리자에게 문의하세요." };
    case "timeout":
    case "network":
      return { cause: "응답이 지연되고 있습니다.", action: "다시 시도해 주세요." };
    default:
      break;
  }
  switch (error.status) {
    case 409:
      return { cause: "정리된 회의록이 아직 없습니다.", action: "처리가 끝난 뒤 다시 질문해 주세요." };
    case 429:
      return { cause: "질문이 너무 잦습니다.", action: "잠시 후 다시 시도해 주세요." };
    case 403:
      return { cause: error.message, action: "삭제된 회의록에는 질문할 수 없습니다." };
    case 404:
      return { cause: error.message, action: "회의록 목록에서 다시 열어 주세요." };
    case 422:
      return { cause: error.message, action: "질문을 확인한 뒤 다시 보내 주세요." };
    default:
      return { cause: error.message || "질문을 처리하지 못했습니다.", action: "잠시 후 다시 시도해 주세요." };
  }
}

/** 입력 글자 수(사용자가 보는 글자 = 코드포인트 기준, 서버의 길이 검사와 같다) */
export const questionLength = (text: string): number => Array.from(text.trim()).length;
