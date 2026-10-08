"use client";

/*
 * 사유 입력 확인 팝업(위험 작업 공용): 업무 종결·삭제, 회의록 보류·직권 종료·삭제가 같이 쓰고, 재개는 사유 없이(requireReason=false) 쓴다.
 * - 동작 이름(제목)과 대상(회의록 제목 또는 업무명), 안내 문구, 사유 입력칸(글자 수 표시, 최대 2000자)과 취소·확정 버튼
 * - 사유가 비었거나 공백뿐이면 확정 버튼 비활성(내용은 판단하지 않음). 앞뒤 공백을 지워 보낸다
 * - 서버 오류(403·404·409·422 등)는 팝업 안에 서버 문구, 입력값은 그대로. 성공하면 onDone(화면 갱신)을 기다린 뒤 닫는다
 * - tone="danger": 취소에 먼저 포커스, 확인 버튼은 구체적 동작 이름, Esc·배경 클릭으로 닫기, 닫히면 연 버튼으로 포커스 복귀(Modal)
 */
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { Modal, StatusDot } from "@/components/mono";
import { isAbortError } from "@/lib/v2/errors";
import { REASON_MAX } from "@/lib/v2/meetings";

const errorMessage = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

export interface ReasonChoice {
  legend: string;
  options: { value: string; label: string }[];
  /** 고르지 않고 확인했을 때 알림 문구 */
  requiredMessage: string;
}

export interface ReasonAction {
  /** 팝업 제목(동작 이름). 예) "회의록 보류" */
  title: string;
  /** 대상 이름(회의록 제목 또는 업무명) */
  target: string;
  /** 확인 버튼 이름. 예) "보류하기" */
  confirmLabel: string;
  /** 추가 안내 문구(예: 확정 전 업무도 함께 종결됩니다) */
  notice?: ReactNode;
  /** 사유 입력 필수 여부(재개만 false) */
  requireReason: boolean;
  /** 필수 선택 그룹(예: 업무 종결의 종결 구분). 없으면 그리지 않는다 */
  choice?: ReasonChoice;
  /** 서버 요청. reason 은 앞뒤 공백을 지운 값(사유 없는 동작은 빈 문자열), choice 는 고른 값(choice 를 쓰는 동작만) */
  run: (reason: string, signal: AbortSignal, choice: string) => Promise<void>;
}

interface ReasonDialogProps {
  /** null 이면 닫힘 */
  action: ReasonAction | null;
  onClose: () => void;
  /** 성공 뒤 화면 갱신(끝나면 닫힌다) */
  onDone: () => Promise<void>;
}

export function ReasonDialog({ action, onClose, onDone }: ReasonDialogProps) {
  const reasonId = useId();
  const counterId = useId();
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [choice, setChoice] = useState("");
  const choiceName = useId();
  const firstChoiceRef = useRef<HTMLInputElement | null>(null);
  const runRef = useRef<AbortController | null>(null);

  // 열 때마다 입력을 비우고, 닫히면 진행 중인 요청을 취소한다
  const open = action !== null;
  const actionTitle = action?.title;
  const actionTarget = action?.target;
  useEffect(() => {
    if (!open) return;
    setReason("");
    setChoice("");
    setError(null);
    setSaving(false);
    return () => runRef.current?.abort();
  }, [open, actionTitle, actionTarget]);

  const trimmed = reason.trim();
  const blocked = action?.requireReason === true && trimmed === "";

  async function onConfirm() {
    if (!action || saving || blocked) return;
    // 필수 선택을 안 했으면 서버를 부르지 않고 알림 + 첫 선택지로 포커스
    if (action.choice && !choice) {
      setError(action.choice.requiredMessage);
      firstChoiceRef.current?.focus();
      return;
    }
    runRef.current?.abort();
    const controller = new AbortController();
    runRef.current = controller;
    setSaving(true);
    setError(null);
    try {
      await action.run(action.requireReason ? trimmed : "", controller.signal, choice);
    } catch (err) {
      if (isAbortError(err)) return;
      // 입력값은 그대로 두고 서버 문구를 보여 준다
      setError(`${action.title}하지 못했습니다 · ${errorMessage(err, "서버 오류")}`);
      setSaving(false);
      return;
    }
    try {
      await onDone();
    } catch {
      // 처리는 됐으므로 닫고, 화면 갱신 실패는 상세 화면이 안내한다
    }
    setSaving(false);
    onClose();
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      tone="danger"
      title={action?.title ?? ""}
      description={
        <>
          <span>대상 </span>
          <span className="text-mn-text">{action?.target}</span>
        </>
      }
      confirmLabel={action?.confirmLabel ?? ""}
      cancelLabel="취소"
      onConfirm={() => void onConfirm()}
      confirmDisabled={blocked}
      confirmLoading={saving}
    >
      <div className="mt-3 flex flex-col gap-3">
        {action?.notice ? <p className="text-sm text-mn-text">{action.notice}</p> : null}
        {action?.choice ? (
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-2 text-sm font-medium">
              {action.choice.legend} <span className="font-normal text-mn-muted">(필수)</span>
            </legend>
            <div className="flex flex-wrap gap-2">
              {action.choice.options.map((option, index) => {
                const checked = choice === option.value;
                return (
                  <label
                    key={option.value}
                    className={[
                      "inline-flex min-h-8 cursor-pointer items-center gap-2 rounded-mn-control border px-3 py-1 text-[13px]",
                      "has-[:focus-visible]:[box-shadow:var(--mn-focus-ring)] has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-50",
                      checked ? "border-mn-accent bg-mn-elevated font-semibold text-mn-text" : "border-mn-control text-mn-muted hover:text-mn-text",
                    ].join(" ")}
                  >
                    <input
                      ref={index === 0 ? firstChoiceRef : undefined}
                      type="radio"
                      name={choiceName}
                      value={option.value}
                      checked={checked}
                      disabled={saving}
                      onChange={() => {
                        setError(null);
                        setChoice(option.value);
                      }}
                      className="sr-only"
                    />
                    <span aria-hidden="true" className="font-mn-mono">
                      {checked ? "●" : "○"}
                    </span>
                    {option.label}
                  </label>
                );
              })}
            </div>
          </fieldset>
        ) : null}
        {action?.requireReason ? (
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <label htmlFor={reasonId} className="text-sm font-medium">
                사유 <span className="font-normal text-mn-muted">(필수)</span>
              </label>
              <span id={counterId} className="font-mn-mono text-[13px] text-mn-muted">
                {reason.length} / {REASON_MAX}
              </span>
            </div>
            <textarea
              id={reasonId}
              value={reason}
              maxLength={REASON_MAX}
              rows={4}
              disabled={saving}
              aria-describedby={counterId}
              placeholder="처리 사유를 적어 주세요"
              onChange={(event) => {
                setError(null);
                setReason(event.target.value);
              }}
              className="mn-focus w-full rounded-mn-control border border-mn-control bg-mn-bg px-3 py-2 text-sm text-mn-text outline-none placeholder:text-mn-muted disabled:opacity-50"
            />
          </div>
        ) : null}
        {error ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={error} />
          </p>
        ) : null}
      </div>
    </Modal>
  );
}
