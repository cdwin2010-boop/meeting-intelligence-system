"use client";

import Link from "next/link";
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Button, Modal, StatusDot } from "@/components/mono";
import { ApiError, GpuGuardError, isAbortError, uploadMeetingAudio } from "@/lib/api";
import type { SttEngine, UploadReceipt } from "@/lib/types";
import { UploadProgress } from "./UploadProgress";

const ALLOWED_EXTENSIONS = [".mp3", ".m4a", ".wav"];
const TITLE_MAX = 200;

type FieldErrors = Partial<Record<"file" | "title" | "startedAt", string>>;

/** datetime-local 값("2026-09-29T14:00") → KST 오프셋을 붙인 ISO 8601("2026-09-29T14:00:00+09:00") */
function toKstIso(local: string): string {
  const withSeconds = local.length === 16 ? `${local}:00` : local; // 초가 없으면 ":00" 보충
  return `${withSeconds}+09:00`;
}

const extensionOf = (name: string) => /\.[A-Za-z0-9]+$/.exec(name)?.[0].toLowerCase() ?? "";

/** 제출 전 검사: 비어 있거나 형식이 틀린 칸마다 이유를 돌려준다 */
function validate(file: File | null, title: string, startedAtLocal: string): FieldErrors {
  const errors: FieldErrors = {};
  if (!file) errors.file = "음성 파일을 선택해 주세요.";
  else if (!ALLOWED_EXTENSIONS.includes(extensionOf(file.name))) {
    errors.file = "지원하지 않는 파일 형식입니다(mp3, m4a, wav).";
  }
  const trimmed = title.trim();
  if (!trimmed) errors.title = "회의 제목을 입력해 주세요.";
  else if (trimmed.length > TITLE_MAX) errors.title = `회의 제목은 ${TITLE_MAX}자 이하로 입력해 주세요.`;
  if (!startedAtLocal) errors.startedAt = "회의 일시를 입력해 주세요.";
  return errors;
}

/** 서버/목 오류 → 사용자 안내 문구 */
function messageFor(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "invalid_input":
        return "입력값을 확인해 주세요. 파일, 회의 제목, 회의 일시가 올바른지 확인한 뒤 다시 제출해 주세요.";
      case "too_large":
        return "파일이 너무 큽니다.";
      case "unsupported_type":
        return "지원하지 않는 파일 형식입니다(mp3, m4a, wav).";
      default:
        break;
    }
  }
  // 타임아웃·네트워크 끊김·서버 오류
  return "업로드하지 못했습니다. 네트워크 연결을 확인한 뒤 다시 시도해 주세요.";
}

const inputClass =
  "mn-focus h-10 w-full rounded-mn-control border border-mn-border bg-mn-surface px-3 text-sm text-mn-text outline-none disabled:opacity-50";

export function UploadForm() {
  const id = useId();
  const fileId = `${id}-file`;
  const titleId = `${id}-title`;
  const startedAtId = `${id}-started-at`;

  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [startedAtLocal, setStartedAtLocal] = useState("");
  const [engine, setEngine] = useState<SttEngine>("gemini-api");

  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [receipt, setReceipt] = useState<UploadReceipt | null>(null);
  const [gpuGuardMinutes, setGpuGuardMinutes] = useState<number | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  // 화면을 떠나면 진행 중인 업로드를 끊는다
  useEffect(() => () => abortRef.current?.abort(), []);

  /** 실제 제출. GPU 가드 경고에서 다시 제출할 때는 engine/forceLocal을 덮어써서 부른다. */
  const submit = useCallback(
    async (override: { engine?: SttEngine; forceLocal?: boolean } = {}) => {
      // 이중 제출 방지: 상태(state)는 다음 렌더에야 바뀌므로 ref로 즉시 막는다
      if (abortRef.current || receipt) return;

      const errors = validate(file, title, startedAtLocal);
      setFieldErrors(errors);
      setFormError(null);
      if (Object.keys(errors).length > 0 || !file) return;

      const controller = new AbortController();
      abortRef.current = controller;
      setUploading(true);

      try {
        const result = await uploadMeetingAudio(
          {
            file,
            title: title.trim(),
            startedAt: toKstIso(startedAtLocal),
            engine: override.engine ?? engine,
            forceLocal: override.forceLocal ?? false,
          },
          controller.signal,
        );
        setReceipt(result); // 접수 완료 → 폼 잠금, 진행 카드 표시
      } catch (error: unknown) {
        if (isAbortError(error)) {
          setFormError("업로드를 취소했습니다."); // 사용자가 Cancel upload로 끊은 경우
        } else if (error instanceof GpuGuardError) {
          setGpuGuardMinutes(error.expectedMinutes);
        } else {
          setFormError(messageFor(error));
        }
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setUploading(false);
      }
    },
    [receipt, file, title, startedAtLocal, engine],
  );

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void submit();
  };

  const handleCancelUpload = () => abortRef.current?.abort();

  const closeGpuGuard = useCallback(() => setGpuGuardMinutes(null), []);

  const switchToGemini = () => {
    setGpuGuardMinutes(null);
    setEngine("gemini-api");
    void submit({ engine: "gemini-api" });
  };

  const proceedLocal = () => {
    setGpuGuardMinutes(null);
    void submit({ engine: "faster-whisper", forceLocal: true });
  };

  const locked = uploading || receipt !== null;

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-4 py-12">
      <nav>
        <Link href="/" className="mn-focus rounded-mn-control text-sm text-mn-muted hover:text-mn-text">
          ← 회의 목록으로
        </Link>
      </nav>

      <header>
        <h1 className="text-[32px] font-semibold leading-10 tracking-tight text-mn-text">음성 등록</h1>
        <p className="mt-1 text-sm text-mn-muted">
          회의 음성을 올리면 전사와 액션아이템 추출이 뒤에서 진행됩니다.
        </p>
      </header>

      <form
        noValidate
        onSubmit={handleSubmit}
        aria-busy={uploading}
        className="rounded-mn-card border border-mn-border bg-mn-surface p-6"
      >
        {/* fieldset disabled: 업로드 중·접수 후에는 모든 입력을 한 번에 잠근다 */}
        <fieldset disabled={locked} className="flex flex-col gap-5">
          <div className="flex flex-col gap-2">
            <label htmlFor={fileId} className="text-sm font-medium text-mn-text">
              음성 파일
            </label>
            <input
              id={fileId}
              type="file"
              accept=".mp3,.m4a,.wav"
              aria-invalid={fieldErrors.file ? true : undefined}
              aria-describedby={`${fileId}-hint${fieldErrors.file ? ` ${fileId}-error` : ""}`}
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              className="mn-focus rounded-mn-control text-sm text-mn-text file:mr-3 file:h-8 file:cursor-pointer file:rounded-mn-control file:border file:border-mn-border file:bg-transparent file:px-3 file:text-[13px] file:text-mn-text disabled:opacity-50"
            />
            <p id={`${fileId}-hint`} className="text-xs text-mn-muted">
              mp3, m4a, wav 파일
            </p>
            {fieldErrors.file ? (
              <p id={`${fileId}-error`}>
                <StatusDot tone="error" label={fieldErrors.file} />
              </p>
            ) : null}
          </div>

          <div className="flex flex-col gap-2">
            <label htmlFor={titleId} className="text-sm font-medium text-mn-text">
              회의 제목
            </label>
            <input
              id={titleId}
              type="text"
              value={title}
              maxLength={TITLE_MAX}
              aria-invalid={fieldErrors.title ? true : undefined}
              aria-describedby={fieldErrors.title ? `${titleId}-error` : undefined}
              onChange={(event) => setTitle(event.target.value)}
              className={inputClass}
            />
            {fieldErrors.title ? (
              <p id={`${titleId}-error`}>
                <StatusDot tone="error" label={fieldErrors.title} />
              </p>
            ) : null}
          </div>

          <div className="flex flex-col gap-2">
            <label htmlFor={startedAtId} className="text-sm font-medium text-mn-text">
              회의 일시 (KST)
            </label>
            <input
              id={startedAtId}
              type="datetime-local"
              value={startedAtLocal}
              aria-invalid={fieldErrors.startedAt ? true : undefined}
              aria-describedby={fieldErrors.startedAt ? `${startedAtId}-error` : undefined}
              onChange={(event) => setStartedAtLocal(event.target.value)}
              className={`${inputClass} font-mn-mono [color-scheme:dark]`}
            />
            {fieldErrors.startedAt ? (
              <p id={`${startedAtId}-error`}>
                <StatusDot tone="error" label={fieldErrors.startedAt} />
              </p>
            ) : null}
          </div>

          <fieldset className="flex flex-col gap-2">
            <legend className="mb-2 text-sm font-medium text-mn-text">전사 엔진</legend>
            {(
              [
                { value: "gemini-api", label: "Gemini API (기본)" },
                { value: "faster-whisper", label: "로컬 Faster-Whisper (기밀 회의용)" },
              ] as const
            ).map((option) => (
              <label key={option.value} className="flex items-center gap-2 text-sm text-mn-text">
                <input
                  type="radio"
                  name="engine"
                  value={option.value}
                  checked={engine === option.value}
                  onChange={() => setEngine(option.value)}
                  className="mn-focus size-4 accent-mn-accent"
                />
                {option.label}
              </label>
            ))}
          </fieldset>
        </fieldset>

        {formError ? (
          <p role="alert" className="mt-5">
            <StatusDot tone="error" label={formError} />
          </p>
        ) : null}

        {receipt === null ? (
          <div className="mt-6 flex justify-end gap-2">
            {uploading ? (
              <Button variant="secondary" onClick={handleCancelUpload}>
                Cancel upload
              </Button>
            ) : null}
            <Button type="submit" variant="primary" disabled={uploading}>
              {uploading ? "Uploading…" : "Upload"}
            </Button>
          </div>
        ) : null}
      </form>

      {receipt ? <UploadProgress receipt={receipt} /> : null}

      {/* GPU 가드 경고: 버튼 3개가 필요해 Modal의 확인 버튼 대신 children에 둔다.
          tone="default"는 첫 포커스 가능 요소에 포커스하므로 "취소"를 맨 앞에 둔다. */}
      <Modal
        open={gpuGuardMinutes !== null}
        onClose={closeGpuGuard}
        title="로컬 처리 시간 경고"
        description={`예상 처리 시간 약 ${gpuGuardMinutes ?? 0}분. 로컬 GPU로는 오래 걸릴 수 있습니다.`}
      >
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <Button variant="secondary" onClick={closeGpuGuard}>
            취소
          </Button>
          <Button variant="secondary" onClick={proceedLocal}>
            그래도 로컬로 진행
          </Button>
          <Button variant="primary" onClick={switchToGemini}>
            Gemini API로 전환해 업로드
          </Button>
        </div>
      </Modal>
    </div>
  );
}
