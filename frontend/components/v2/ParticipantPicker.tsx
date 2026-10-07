"use client";

/*
 * 참석자 선택(올리기 화면): GET /api/accounts 로 같은 고객사 계정을 불러와 체크박스로 여러 명 고른다.
 * - 선택은 선택 사항이다. 목록을 못 불러와도 오류만 보여 주고 올리기는 막지 않는다(다시 시도 가능)
 * - 화면을 떠나거나 다시 시도하면 이전 요청은 AbortController 로 취소한다
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { Button, StatusDot } from "@/components/mono";
import { listAccounts, type AccountOption } from "@/lib/v2/accounts";
import { isAbortError } from "@/lib/v2/errors";
import { RANK_LABEL } from "@/lib/v2/types";

type LoadState = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; accounts: AccountOption[] };

interface ParticipantPickerProps {
  selected: number[];
  onChange: (ids: number[]) => void;
  disabled?: boolean;
}

export function ParticipantPicker({ selected, onChange, disabled = false }: ParticipantPickerProps) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ kind: "loading" });
    try {
      setState({ kind: "ready", accounts: await listAccounts(controller.signal) });
    } catch (error) {
      if (isAbortError(error)) return;
      setState({ kind: "error", message: error instanceof Error && error.message ? error.message : "목록을 불러오지 못했습니다." });
    }
  }, []);

  useEffect(() => {
    void load();
    return () => controllerRef.current?.abort();
  }, [load]);

  function toggle(id: number, checked: boolean) {
    onChange(checked ? [...selected, id] : selected.filter((v) => v !== id));
  }

  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-2 text-sm font-medium">
        참석자 <span className="font-normal text-mn-muted">(선택)</span>
      </legend>
      {state.kind === "loading" ? (
        <p role="status" className="text-sm text-mn-muted">
          참석자 목록을 불러오는 중…
        </p>
      ) : state.kind === "error" ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-mn-card border border-mn-border p-3">
          <span className="text-sm">
            <StatusDot tone="error" label={`참석자 목록을 불러오지 못했습니다 · ${state.message}`} />
          </span>
          <Button size="sm" onClick={() => void load()}>
            목록 다시 불러오기
          </Button>
        </div>
      ) : state.accounts.length === 0 ? (
        <p className="text-sm text-mn-muted">선택할 수 있는 계정이 없습니다.</p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {state.accounts.map((account) => {
            const checked = selected.includes(account.id);
            return (
              <li key={account.id}>
                {/* 체크박스를 라벨 안에 숨겨 칩 모양으로 보여 준다. 포커스 링은 라벨에 */}
                <label
                  className={[
                    "inline-flex h-8 cursor-pointer items-center gap-2 rounded-mn-control border px-3 text-[13px]",
                    "has-[:focus-visible]:[box-shadow:var(--mn-focus-ring)] has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-50",
                    checked ? "border-mn-accent bg-mn-elevated text-mn-text" : "border-mn-control text-mn-muted hover:text-mn-text",
                  ].join(" ")}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={disabled}
                    onChange={(event) => toggle(account.id, event.target.checked)}
                    className="sr-only"
                  />
                  <span aria-hidden="true" className="font-mn-mono">
                    {checked ? "✓" : "+"}
                  </span>
                  {account.name} · {RANK_LABEL[account.rank] ?? account.rank}
                </label>
              </li>
            );
          })}
        </ul>
      )}
      <span className="text-xs text-mn-muted">같은 회사 계정만 고를 수 있습니다. 고르지 않아도 올릴 수 있습니다.</span>
    </fieldset>
  );
}
