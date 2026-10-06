"use client";

/*
 * 수정 회의록 업로드 팝업(관리자 이상에게만 메뉴가 보인다. 실제 권한은 서버: 지시자·총괄, 거부되면 서버 문구).
 * 흐름: 파일 선택 → "미리보기"(아무것도 저장하지 않음) → 확인 → "변경 적용"(한 번에 전부 또는 아무것도).
 * - 미리보기는 업무별 변경 전·후, 건너뛴 업무와 사유, 새 행("등록자 직권 지정"), 참석자 변경(열람 영향 포함), 5개 항목 변경, 경고를 구역별로 보여 준다.
 * - 동명이인(ambiguities)은 항목마다 후보 계정을 고르게 하고, 모두 고르면 같은 파일을 선택값(choices)과 함께 다시 미리보기해 결과를 맞춘다.
 *   "변경 적용"은 마지막 미리보기가 적용 가능(canApply)일 때만 켜진다.
 * - 오류가 있으면(errors) 오류 목록만 보여 주고 적용할 수 없다. 서버 문구(400·403·409·413)는 그대로 보여 주고 선택값은 유지한다.
 * - 파일 형식·용량·행 수 제한은 화면에 쓰지 않고 서버 판정에 맡긴다.
 */
import { useEffect, useId, useRef, useState } from "react";

import { Button, Modal, StatusDot } from "@/components/mono";
import { listAccounts, type AccountOption } from "@/lib/v2/accounts";
import { ApiError, isAbortError } from "@/lib/v2/errors";
import {
  applyUpload,
  MINUTES_LABEL,
  previewUpload,
  type ItemChange,
  type MinutesField,
  type UploadApplyResult,
  type UploadIssue,
  type UploadPreview,
} from "@/lib/v2/meetings";

const errorMessage = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

/** 오류 본문 {detail:{errors:[…]}} 에서 행 오류 목록을 꺼낸다(없으면 빈 목록) */
function bodyErrors(error: unknown): UploadIssue[] {
  if (!(error instanceof ApiError)) return [];
  const body = error.body as { detail?: { errors?: unknown } } | undefined;
  const list = body?.detail?.errors;
  return Array.isArray(list) ? (list as UploadIssue[]) : [];
}

const SNAKE_TO_CAMEL: Record<string, MinutesField> = {
  purpose: "purpose", discussion: "discussion", decisions: "decisions", risks: "risks", next_agenda: "nextAgenda", nextAgenda: "nextAgenda",
};

const where = (issue: UploadIssue) => (issue.row ? `${issue.sheet} ${issue.row}행` : issue.sheet);

interface UploadUpdateDialogProps {
  meetingId: number;
  open: boolean;
  onClose: () => void;
  /** 적용 성공 뒤 화면 갱신(끝나면 팝업이 닫힌다) */
  onApplied: (result: UploadApplyResult) => Promise<void>;
}

export function UploadUpdateDialog({ meetingId, open, onClose, onApplied }: UploadUpdateDialogProps) {
  const fileId = useId();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<UploadPreview | null>(null);
  const [choices, setChoices] = useState<Record<string, number>>({});
  const [accounts, setAccounts] = useState<Map<number, string>>(new Map());
  const [busy, setBusy] = useState<"preview" | "apply" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorList, setErrorList] = useState<UploadIssue[]>([]);
  const runRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 열 때마다 처음 상태로. 계정 이름(담당자 표시용)은 실패해도 계정 번호로 보여 주면 되므로 조용히 넘어간다
  useEffect(() => {
    if (!open) return;
    setFile(null);
    setPreview(null);
    setChoices({});
    setError(null);
    setErrorList([]);
    setBusy(null);
    const controller = new AbortController();
    listAccounts(controller.signal)
      .then((list: AccountOption[]) => setAccounts(new Map(list.map((a) => [a.id, a.name]))))
      .catch(() => undefined);
    return () => {
      controller.abort();
      runRef.current?.abort();
    };
  }, [open]);

  async function runPreview(target: File, picked: Record<string, number>) {
    runRef.current?.abort();
    const controller = new AbortController();
    runRef.current = controller;
    setBusy("preview");
    setError(null);
    setErrorList([]);
    try {
      setPreview(await previewUpload(meetingId, target, picked, controller.signal));
    } catch (err) {
      if (isAbortError(err)) return;
      setPreview(null);
      setError(errorMessage(err, "서버 오류"));
    } finally {
      setBusy((b) => (b === "preview" ? null : b));
    }
  }

  async function onApply() {
    if (!file || !preview?.canApply || busy) return;
    runRef.current?.abort();
    const controller = new AbortController();
    runRef.current = controller;
    setBusy("apply");
    setError(null);
    setErrorList([]);
    let result: UploadApplyResult;
    try {
      result = await applyUpload(meetingId, file, choices, controller.signal);
    } catch (err) {
      if (isAbortError(err)) return;
      // 선택값·미리보기는 그대로 두고 서버 문구(오류 목록 포함)를 보여 준다
      setError(errorMessage(err, "서버 오류"));
      setErrorList(bodyErrors(err));
      setBusy(null);
      return;
    }
    try {
      await onApplied(result);
    } catch {
      // 적용은 끝났으므로 닫고, 화면 갱신 실패는 상세 화면이 안내한다
    }
    setBusy(null);
    onClose();
  }

  function onPickChoice(key: string, value: string) {
    const next = { ...choices };
    if (value === "") delete next[key];
    else next[key] = Number(value);
    setChoices(next);
    // 모든 동명이인을 고르면 선택값으로 미리보기를 다시 만든다(참석자·담당자 결과가 맞춰진다)
    if (file && preview && preview.ambiguities.every((a) => next[a.key] !== undefined)) void runPreview(file, next);
  }

  const name = (id: number | null | undefined) => (id === null || id === undefined ? "없음" : accounts.get(id) ?? `계정 #${id}`);
  const change = (before: ItemChange, after: ItemChange) => {
    const lines: string[] = [];
    if ("title" in after) lines.push(`업무명: ${before.title ?? "—"} → ${after.title ?? "—"}`);
    if ("assigneeId" in after) lines.push(`담당자: ${name(before.assigneeId)} → ${name(after.assigneeId)}`);
    if ("dueDate" in after || "dueUndetermined" in after) {
      const text = (c: ItemChange) => (c.dueUndetermined ? "미확정" : c.dueDate ?? "—");
      lines.push(`기한: ${text({ ...before, dueUndetermined: before.dueUndetermined ?? false })} → ${text(after)}`);
    }
    return lines;
  };

  const hasErrors = (preview?.errors.length ?? 0) > 0;
  const pendingChoices = preview ? preview.ambiguities.filter((a) => choices[a.key] === undefined).length : 0;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="수정 회의록 업로드"
      description="다운로드한 엑셀을 고쳐 올리면 미리보기로 변경 내용을 확인한 뒤 적용합니다. 파일에 없는 업무는 삭제되지 않습니다."
      confirmLabel="변경 적용"
      cancelLabel="취소"
      initialFocus="cancel"
      onConfirm={() => void onApply()}
      confirmDisabled={!preview || !preview.canApply || hasErrors || pendingChoices > 0 || busy === "preview"}
      confirmLoading={busy === "apply"}
      panelClassName="w-full max-w-3xl"
    >
      <div className="mt-3 flex max-h-[62vh] flex-col gap-4 overflow-y-auto pr-1">
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={fileId} className="text-sm font-medium">
            수정한 파일
          </label>
          <input
            id={fileId}
            ref={fileInputRef}
            type="file"
            disabled={busy !== null}
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              setPreview(null);
              setChoices({});
              setError(null);
              setErrorList([]);
            }}
            className="mn-focus min-w-0 text-sm text-mn-text"
          />
          <Button size="sm" disabled={!file || busy !== null} onClick={() => file && void runPreview(file, {})}>
            {busy === "preview" ? "확인 중…" : "미리보기"}
          </Button>
        </div>

        {error ? (
          <div role="alert" className="flex flex-col gap-2 text-sm">
            <StatusDot tone="error" label={error} />
            {errorList.length > 0 ? (
              <ul className="list-disc pl-5">
                {errorList.map((issue, index) => (
                  <li key={index}>
                    <span className="font-mn-mono text-[13px] text-mn-muted">{where(issue)}</span> {issue.message}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        {preview && hasErrors ? (
          <section aria-label="파일 오류" className="flex flex-col gap-2">
            <h3 className="text-sm font-medium">
              <StatusDot tone="error" label={`오류 ${preview.errors.length}건 · 적용할 수 없습니다`} />
            </h3>
            <ul className="list-disc pl-5 text-sm">
              {preview.errors.map((issue, index) => (
                <li key={index}>
                  <span className="font-mn-mono text-[13px] text-mn-muted">{where(issue)}</span> {issue.message}
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {preview && !hasErrors ? (
          <>
            {preview.ambiguities.length > 0 ? (
              <section aria-label="동명이인 선택" className="flex flex-col gap-3">
                <h3 className="text-sm font-medium">
                  동명이인 선택 <span className="font-normal text-mn-muted">· 모두 선택해야 적용할 수 있습니다{pendingChoices > 0 ? ` (남은 ${pendingChoices}건)` : ""}</span>
                </h3>
                {preview.ambiguities.map((amb) => (
                  <div key={amb.key} className="flex flex-wrap items-center gap-2 text-sm">
                    <label htmlFor={`${fileId}-${amb.key}`}>
                      {amb.name} <span className="text-mn-muted">({where({ sheet: amb.sheet, row: amb.row, message: "" })})</span>
                    </label>
                    <select
                      id={`${fileId}-${amb.key}`}
                      value={choices[amb.key] !== undefined ? String(choices[amb.key]) : ""}
                      disabled={busy !== null}
                      onChange={(event) => onPickChoice(amb.key, event.target.value)}
                      className="mn-focus h-9 rounded-mn-control border border-mn-border bg-mn-bg px-2 text-sm text-mn-text outline-none disabled:opacity-50"
                    >
                      <option value="">계정을 선택하세요</option>
                      {amb.candidates.map((c) => (
                        <option key={c.id} value={String(c.id)}>
                          {c.name} · {c.loginId}
                        </option>
                      ))}
                    </select>
                  </div>
                ))}
              </section>
            ) : null}

            {preview.warnings.length > 0 ? (
              <section aria-label="경고" className="flex flex-col gap-2">
                <h3 className="text-sm font-medium">경고 {preview.warnings.length}건</h3>
                <ul className="list-disc pl-5 text-sm">
                  {preview.warnings.map((issue, index) => (
                    <li key={index}>
                      <span className="font-mn-mono text-[13px] text-mn-muted">{where(issue)}</span> {issue.message}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            <section aria-label="업무 변경" className="flex flex-col gap-2">
              <h3 className="text-sm font-medium">업무 갱신 {preview.items.updates.length}건</h3>
              {preview.items.updates.length === 0 ? (
                <p className="text-sm text-mn-muted">갱신할 업무가 없습니다.</p>
              ) : (
                <ul className="flex flex-col gap-2">
                  {preview.items.updates.map((u) => (
                    <li key={u.itemId} className="rounded-mn-card border border-mn-border p-3 text-sm">
                      <p>
                        {u.title || "(업무명 없음)"} <span className="font-mn-mono text-[13px] text-mn-muted">#{u.itemId} · {u.row}행</span>
                      </p>
                      {change(u.before, u.after).map((line) => (
                        <p key={line} className="mt-1 whitespace-pre-line text-mn-muted">
                          {line}
                        </p>
                      ))}
                    </li>
                  ))}
                </ul>
              )}
              {preview.items.unchanged.length > 0 ? (
                <p className="text-xs text-mn-muted">변경 없음 {preview.items.unchanged.length}건</p>
              ) : null}
            </section>

            {preview.items.skipped.length > 0 ? (
              <section aria-label="건너뛴 업무" className="flex flex-col gap-2">
                <h3 className="text-sm font-medium">건너뛴 업무 {preview.items.skipped.length}건</h3>
                <ul className="flex flex-col gap-1 text-sm">
                  {preview.items.skipped.map((s) => (
                    <li key={s.itemId}>
                      {s.title || "(업무명 없음)"} <span className="font-mn-mono text-[13px] text-mn-muted">#{s.itemId} · {s.row}행</span>
                      <span className="text-mn-muted"> · {s.reason}</span>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            {preview.items.added.length > 0 ? (
              <section aria-label="새 업무" className="flex flex-col gap-2">
                <h3 className="text-sm font-medium">새 업무 {preview.items.added.length}건 · 목록 맨 아래에 추가됩니다</h3>
                <ul className="flex flex-col gap-1 text-sm">
                  {preview.items.added.map((n) => (
                    <li key={n.row}>
                      {n.title || "(업무명 없음)"} <span className="font-mn-mono text-[13px] text-mn-muted">{n.row}행</span>
                      <span className="text-mn-muted">
                        {" "}· 담당자 {name(n.assigneeId)} · 기한 {n.dueUndetermined ? "미확정" : n.dueDate ?? "—"} · 근거 {n.evidence}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            {preview.participants ? (
              <section aria-label="참석자 변경" className="flex flex-col gap-2">
                <h3 className="text-sm font-medium">참석자 변경</h3>
                <p className="text-sm">
                  <span className="text-mn-muted">변경 전 </span>
                  {preview.participants.before.join(", ") || "—"}
                </p>
                <p className="text-sm">
                  <span className="text-mn-muted">변경 후 </span>
                  {preview.participants.after.join(", ") || "—"}
                </p>
                {preview.participants.removed.length > 0 ? (
                  <ul className="flex flex-col gap-1 text-sm">
                    {preview.participants.removed.map((r) => (
                      <li key={r.accountId}>
                        {r.name} <span className="text-mn-muted">· 빠짐 · 열람 영향 {r.viewImpact}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </section>
            ) : null}

            {Object.keys(preview.minutes).length > 0 ? (
              <section aria-label="회의록 항목 변경" className="flex flex-col gap-2">
                <h3 className="text-sm font-medium">회의록 항목 변경</h3>
                <ul className="flex flex-col gap-2 text-sm">
                  {Object.entries(preview.minutes).map(([key, value]) => (
                    <li key={key} className="rounded-mn-card border border-mn-border p-3">
                      <p className="text-xs text-mn-muted">{MINUTES_LABEL[SNAKE_TO_CAMEL[key] ?? "purpose"] ?? key}</p>
                      <p className="mt-1 whitespace-pre-line">
                        <span className="text-mn-muted">변경 전 </span>
                        {value.before}
                      </p>
                      <p className="mt-1 whitespace-pre-line">
                        <span className="text-mn-muted">변경 후 </span>
                        {value.after}
                      </p>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </>
        ) : null}
      </div>
    </Modal>
  );
}
