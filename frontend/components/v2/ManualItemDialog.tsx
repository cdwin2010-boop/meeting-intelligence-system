"use client";

/*
 * 수기 업무 등록 팝업(업무 원장 머리 "업무 추가", 관리자 이상에게만 보인다. 실제 권한은 서버: 지시자·총괄).
 * - 업무명(필수), 담당자(같은 고객사 계정 선택), 기한(날짜 또는 "미확정"). 담당자·기한을 비우면 AI 추출 업무처럼 "보완 필요"가 된다.
 * - 근거는 서버가 "등록자 직권 지정"으로 둔다(등록자·시각은 변경 이력에 남는다). 서버 오류 문구는 그대로 보여 주고 입력값은 유지한다.
 */
import { useEffect, useId, useRef, useState } from "react";

import { Modal, StatusDot } from "@/components/mono";
import { listAccounts, type AccountOption } from "@/lib/v2/accounts";
import { isAbortError } from "@/lib/v2/errors";
import { createManualItem, type ActionItem, type ManualItemInput } from "@/lib/v2/meetings";
import { RANK_LABEL } from "@/lib/v2/types";

const errorMessage = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

interface ManualItemDialogProps {
  meetingId: number;
  open: boolean;
  onClose: () => void;
  /** 등록 성공 뒤 화면 갱신(끝나면 팝업이 닫힌다) */
  onCreated: (item: ActionItem) => Promise<void>;
}

export function ManualItemDialog({ meetingId, open, onClose, onCreated }: ManualItemDialogProps) {
  const titleId = useId();
  const assigneeId = useId();
  const dueId = useId();
  const undeterminedId = useId();
  const [title, setTitle] = useState("");
  const [assignee, setAssignee] = useState("");
  const [due, setDue] = useState("");
  const [undetermined, setUndetermined] = useState(false);
  const [accounts, setAccounts] = useState<AccountOption[]>([]);
  const [accountsError, setAccountsError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const runRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!open) return;
    setTitle("");
    setAssignee("");
    setDue("");
    setUndetermined(false);
    setError(null);
    setAccountsError(null);
    setSaving(false);
    const controller = new AbortController();
    listAccounts(controller.signal)
      .then(setAccounts)
      .catch((err) => {
        if (!isAbortError(err)) setAccountsError(errorMessage(err, "계정 목록을 불러오지 못했습니다."));
      });
    return () => {
      controller.abort();
      runRef.current?.abort();
    };
  }, [open]);

  async function onConfirm() {
    if (saving || title.trim() === "") return;
    runRef.current?.abort();
    const controller = new AbortController();
    runRef.current = controller;
    setSaving(true);
    setError(null);
    const input: ManualItemInput = { title: title.trim() };
    if (assignee) input.assigneeId = Number(assignee);
    if (undetermined) input.dueUndetermined = true;
    else if (due) input.dueDate = due;
    let created: ActionItem;
    try {
      created = await createManualItem(meetingId, input, controller.signal);
    } catch (err) {
      if (isAbortError(err)) return;
      setError(errorMessage(err, "서버 오류"));
      setSaving(false);
      return;
    }
    try {
      await onCreated(created);
    } catch {
      // 등록은 끝났으므로 닫고, 화면 갱신 실패는 상세 화면이 안내한다
    }
    setSaving(false);
    onClose();
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="업무 추가"
      description={"근거는 \"등록자 직권 지정\"으로 자동 표시됩니다. 담당자·기한을 비우면 보완 필요로 남습니다."}
      confirmLabel="업무 등록"
      cancelLabel="취소"
      initialFocus="cancel"
      onConfirm={() => void onConfirm()}
      confirmDisabled={title.trim() === ""}
      confirmLoading={saving}
    >
      <div className="mt-3 flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <label htmlFor={titleId} className="text-sm font-medium">
            업무명 <span className="font-normal text-mn-muted">(필수)</span>
          </label>
          <input
            id={titleId}
            type="text"
            value={title}
            disabled={saving}
            onChange={(event) => {
              setError(null);
              setTitle(event.target.value);
            }}
            className="mn-focus h-10 w-full rounded-mn-control border border-mn-border bg-mn-bg px-3 text-sm text-mn-text outline-none disabled:opacity-50"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={assigneeId} className="text-sm font-medium">
            담당자 <span className="font-normal text-mn-muted">(같은 고객사 계정)</span>
          </label>
          <select
            id={assigneeId}
            value={assignee}
            disabled={saving}
            onChange={(event) => setAssignee(event.target.value)}
            className="mn-focus h-10 w-full rounded-mn-control border border-mn-border bg-mn-bg px-3 text-sm text-mn-text outline-none disabled:opacity-50"
          >
            <option value="">선택 안 함</option>
            {accounts.map((a) => (
              <option key={a.id} value={String(a.id)}>
                {a.name} · {RANK_LABEL[a.rank] ?? a.rank}
              </option>
            ))}
          </select>
          {accountsError ? (
            <p role="alert" className="text-xs">
              <StatusDot tone="error" label={`계정 목록을 불러오지 못했습니다 · ${accountsError}`} />
            </p>
          ) : null}
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={dueId} className="text-sm font-medium">
            기한
          </label>
          <div className="flex flex-wrap items-center gap-3">
            <input
              id={dueId}
              type="date"
              value={due}
              disabled={saving || undetermined}
              onChange={(event) => setDue(event.target.value)}
              className="mn-focus h-10 w-[170px] rounded-mn-control border border-mn-border bg-mn-bg px-3 font-mn-mono text-sm text-mn-text outline-none disabled:opacity-50"
            />
            <label htmlFor={undeterminedId} className="flex items-center gap-2 text-sm">
              <input
                id={undeterminedId}
                type="checkbox"
                checked={undetermined}
                disabled={saving}
                onChange={(event) => {
                  setUndetermined(event.target.checked);
                  if (event.target.checked) setDue("");
                }}
              />
              미확정
            </label>
          </div>
        </div>
        {error ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={`등록하지 못했습니다 · ${error}`} />
          </p>
        ) : null}
      </div>
    </Modal>
  );
}
