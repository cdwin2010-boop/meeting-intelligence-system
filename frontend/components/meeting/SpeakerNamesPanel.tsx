"use client";

import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";
import { Button, StatusDot } from "@/components/mono";
import { ApiError, isAbortError, saveSpeakerNames } from "@/lib/api";
import {
  SPEAKER_NAME_MAX,
  duplicateSpeakerNames,
  normalizeSpeakerNames,
  type SpeakerNames,
} from "@/lib/speaker-names";

interface SpeakerNamesPanelProps {
  meetingId: string;
  /** 전사 원문·담당자에서 찾은 화자 표기 + 이미 저장된 키 (번호순) */
  labels: string[];
  /** 서버에 저장된 현재 매핑 */
  saved: SpeakerNames;
  /** 저장 성공 시 서버가 돌려준 매핑 → 담당자·전사 원문 표시가 바로 바뀐다 */
  onSaved: (names: SpeakerNames) => void;
}

type SaveState = { kind: "idle" } | { kind: "saving" } | { kind: "saved" } | { kind: "error"; message: string };

const inputClass =
  "mn-focus h-10 w-full rounded-mn-control border border-mn-border bg-mn-surface px-3 text-sm text-mn-text outline-none disabled:opacity-50";

/** 화자 표기마다 이름 입력칸을 두고, 매핑 전체를 한 번에 저장한다(전체 교체). 빈 칸은 이름 지정 해제. */
export function SpeakerNamesPanel({ meetingId, labels, saved, onSaved }: SpeakerNamesPanelProps) {
  const headingId = useId();
  const [draft, setDraft] = useState<Record<string, string>>(() => ({ ...saved }));
  const [state, setState] = useState<SaveState>({ kind: "idle" });
  const abortRef = useRef<AbortController | null>(null);

  // 화면을 떠나면 진행 중인 저장 요청을 취소
  useEffect(() => () => abortRef.current?.abort(), []);

  // 보낼 값: 화면에 보이는 화자만 (빈 칸도 그대로 보내 서버가 삭제하게 한다)
  const request = useMemo(() => {
    const speakers: SpeakerNames = {};
    for (const label of labels) speakers[label] = draft[label] ?? "";
    return speakers;
  }, [labels, draft]);

  // 같은 이름을 여러 화자에 지정하면 경고만 한다 (저장은 허용)
  const duplicates = useMemo(() => duplicateSpeakerNames(normalizeSpeakerNames(request) ?? {}), [request]);

  if (labels.length === 0) {
    return (
      <section aria-labelledby={headingId} className="flex flex-col gap-2">
        <h2 id={headingId} className="text-xl font-semibold leading-7 text-mn-text">
          화자 이름
        </h2>
        <p className="text-sm text-mn-muted">전사 원문과 담당자에서 &quot;화자N&quot; 표기를 찾지 못했습니다.</p>
      </section>
    );
  }

  const saving = state.kind === "saving";

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (saving) return;
    // 서버와 같은 규칙으로 먼저 검사해 불필요한 요청을 막는다 (최종 판정은 서버)
    if (normalizeSpeakerNames(request) === null) {
      setState({ kind: "error", message: `이름은 ${SPEAKER_NAME_MAX}자 이하로 입력해 주세요.` });
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ kind: "saving" });
    try {
      const names = await saveSpeakerNames(meetingId, request, controller.signal);
      abortRef.current = null;
      setDraft({ ...names }); // 서버가 정리한 값(공백 제거·빈 값 삭제)으로 맞춘다
      onSaved(names);
      setState({ kind: "saved" });
    } catch (error: unknown) {
      if (isAbortError(error)) return;
      const message =
        error instanceof ApiError && error.code === "invalid_input"
          ? `이름은 ${SPEAKER_NAME_MAX}자 이하로 입력해 주세요.`
          : error instanceof ApiError && error.code === "not_found"
            ? "회의를 찾을 수 없습니다."
            : "저장하지 못했습니다. 다시 시도해 주세요.";
      setState({ kind: "error", message });
    }
  };

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 id={headingId} className="text-xl font-semibold leading-7 text-mn-text">
          화자 이름
        </h2>
        <p className="text-sm text-mn-muted">
          이름을 지정하면 담당자와 전사 원문 화면에 바로 반영됩니다. 빈 칸은 &quot;화자N&quot; 그대로 표시되며, 다운로드
          파일은 원본 그대로입니다.
        </p>
      </div>

      <form
        onSubmit={handleSubmit}
        aria-busy={saving}
        className="flex flex-col gap-4 rounded-mn-card border border-mn-border bg-mn-surface p-6"
      >
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {labels.map((label) => {
            const inputId = `${headingId}-${label}`;
            return (
              <div key={label} className="flex items-center gap-3">
                <label htmlFor={inputId} className="w-16 shrink-0 font-mn-mono text-sm text-mn-muted">
                  {label}
                </label>
                <input
                  id={inputId}
                  type="text"
                  value={draft[label] ?? ""}
                  maxLength={SPEAKER_NAME_MAX}
                  placeholder="이름/직함"
                  disabled={saving}
                  onChange={(event) => {
                    const value = event.target.value;
                    setDraft((current) => ({ ...current, [label]: value }));
                    if (state.kind !== "idle") setState({ kind: "idle" });
                  }}
                  className={inputClass}
                />
              </div>
            );
          })}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="secondary" size="sm" disabled={saving}>
            화자 이름 저장
          </Button>
          {/* 저장 결과는 스크린리더에도 읽힌다 */}
          <p role="status" aria-live="polite">
            {state.kind === "saving" ? <StatusDot tone="building" label="저장 중…" /> : null}
            {state.kind === "saved" ? <StatusDot tone="ready" label="저장했습니다" /> : null}
            {state.kind === "error" ? <StatusDot tone="error" label={state.message} /> : null}
          </p>
        </div>

        {duplicates.length > 0 ? (
          <p className="text-sm text-mn-muted">
            같은 이름이 여러 화자에 지정되어 있습니다: {duplicates.join(", ")} (저장은 가능합니다)
          </p>
        ) : null}
      </form>
    </section>
  );
}
