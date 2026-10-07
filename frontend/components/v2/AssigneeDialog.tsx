"use client";

/*
 * 업무 담당자 지정·변경 대화상자(회의록 상세 업무 원장).
 * - 열 때마다 GET /api/accounts 로 같은 고객사 활성 계정을 불러와 한 명을 고른다(이름 글자만으로는 지정 불가)
 * - 저장은 PATCH /api/action-items/{id} {assigneeId}. 성공하면 서버가 준 업무로 onSaved 를 부르고 닫는다
 * - 권한·확정된 업무 제한은 서버가 판정한다. 400·403·409·서버 오류는 서버 문구를 보여 주고 선택값은 그대로 둔다
 * - 닫거나 다시 시도하면 이전 요청은 AbortController 로 취소한다
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { Button, Modal, StatusDot } from "@/components/mono";
import { listAccounts, type AccountOption } from "@/lib/v2/accounts";
import { updateAssignee } from "@/lib/v2/action-items";
import { isAbortError } from "@/lib/v2/errors";
import type { ActionItem } from "@/lib/v2/meetings";
import { RANK_LABEL } from "@/lib/v2/types";

type AccountsState = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; accounts: AccountOption[] };

const errorMessage = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

interface AssigneeDialogProps {
  /** 대상 업무. null 이면 닫힘 */
  item: ActionItem | null;
  onClose: () => void;
  onSaved: (item: ActionItem) => void;
}

export function AssigneeDialog({ item, onClose, onSaved }: AssigneeDialogProps) {
  const selectId = useId();
  const [accounts, setAccounts] = useState<AccountsState>({ kind: "loading" });
  const [selected, setSelected] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const accountsRef = useRef<AbortController | null>(null);
  const saveRef = useRef<AbortController | null>(null);

  const loadAccounts = useCallback(async () => {
    accountsRef.current?.abort();
    const controller = new AbortController();
    accountsRef.current = controller;
    setAccounts({ kind: "loading" });
    try {
      setAccounts({ kind: "ready", accounts: await listAccounts(controller.signal) });
    } catch (err) {
      if (isAbortError(err)) return;
      setAccounts({ kind: "error", message: errorMessage(err, "계정 목록을 불러오지 못했습니다.") });
    }
  }, []);

  // 열릴 때(대상 업무가 바뀔 때) 선택값을 현재 담당자로 맞추고 계정 목록을 새로 받는다
  const itemId = item?.id ?? null;
  const currentAssigneeId = item?.assignee?.id ?? null;
  useEffect(() => {
    if (itemId === null) return;
    setSelected(currentAssigneeId !== null ? String(currentAssigneeId) : "");
    setError(null);
    setSaving(false);
    void loadAccounts();
    return () => {
      accountsRef.current?.abort();
      saveRef.current?.abort();
    };
  }, [itemId, currentAssigneeId, loadAccounts]);

  async function onConfirm() {
    if (!item || !selected || saving) return;
    saveRef.current?.abort();
    const controller = new AbortController();
    saveRef.current = controller;
    setSaving(true);
    setError(null);
    try {
      const saved = await updateAssignee(item.id, Number(selected), controller.signal);
      setSaving(false);
      onSaved(saved);
    } catch (err) {
      if (isAbortError(err)) return;
      // 선택값은 그대로 두고 서버 문구를 보여 준다
      setError(errorMessage(err, "저장하지 못했습니다."));
      setSaving(false);
    }
  }

  const accountList = accounts.kind === "ready" ? accounts.accounts : [];
  const unchanged = selected !== "" && Number(selected) === currentAssigneeId;

  return (
    <Modal
      open={item !== null}
      onClose={onClose}
      title={item?.assignee ? "담당자 변경" : "담당자 지정"}
      description={
        <>
          <span className="text-mn-text">{item?.title || "(업무명 없음)"}</span>
          <span> · 현재 담당자 {item?.assignee?.name ?? "없음"}</span>
        </>
      }
      confirmLabel="담당자 저장"
      cancelLabel="취소"
      onConfirm={() => void onConfirm()}
      confirmDisabled={accounts.kind !== "ready" || selected === "" || unchanged}
      confirmLoading={saving}
    >
      <div className="mt-3 flex flex-col gap-3">
        {accounts.kind === "loading" ? (
          <p role="status" className="text-sm text-mn-muted">
            계정 목록을 불러오는 중…
          </p>
        ) : accounts.kind === "error" ? (
          <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-mn-card border border-mn-border p-3">
            <span className="text-sm">
              <StatusDot tone="error" label={`계정 목록을 불러오지 못했습니다 · ${accounts.message}`} />
            </span>
            <Button size="sm" onClick={() => void loadAccounts()}>
              다시 시도
            </Button>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <label htmlFor={selectId} className="text-sm font-medium">
              담당자 <span className="font-normal text-mn-muted">(같은 고객사 계정)</span>
            </label>
            <select
              id={selectId}
              value={selected}
              disabled={saving}
              onChange={(event) => {
                setError(null);
                setSelected(event.target.value);
              }}
              className="mn-focus h-10 w-full rounded-mn-control border border-mn-control bg-mn-bg px-3 text-sm text-mn-text outline-none disabled:opacity-50"
            >
              <option value="" disabled>
                계정을 선택하세요
              </option>
              {accountList.map((a) => (
                <option key={a.id} value={String(a.id)}>
                  {a.name} · {RANK_LABEL[a.rank] ?? a.rank}
                </option>
              ))}
            </select>
          </div>
        )}
        {error ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={`저장하지 못했습니다 · ${error}`} />
          </p>
        ) : null}
      </div>
    </Modal>
  );
}
