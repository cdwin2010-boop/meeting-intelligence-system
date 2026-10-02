"use client";

/*
 * 화자 확정 패널(회의록 상세): 전사문의 화자 표기마다 같은 고객사 계정 또는 미등록 이름(글자)을 지정한다.
 * - GET /api/meetings/{id}/speakers 로 화자 표기와 저장된 매핑을 불러와 처음부터 그 값으로 보여 준다
 * - 저장은 PUT 전체 교체. 성공하면 onSaved 로 상세(업무 원장)·전사문을 새로 받게 한다
 * - 권한은 서버가 판정한다(조회 403 이면 안내만, 저장 400·403·409·서버 오류는 서버 문구 표시, 입력값 유지)
 * - 계정 목록(GET /api/accounts) 조회가 실패해도 오류·다시 시도만 보이고 이름 직접 입력은 계속 쓸 수 있다
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { Button, StatusDot } from "@/components/mono";
import { listAccounts, type AccountOption } from "@/lib/v2/accounts";
import { ApiError, isAbortError } from "@/lib/v2/errors";
import { getSpeakers, saveSpeakers, type SpeakerInput, type SpeakerMapping } from "@/lib/v2/meetings";
import { RANK_LABEL } from "@/lib/v2/types";

/** 미등록 이름 최대 글자 수(화면 기준) */
export const SPEAKER_NAME_MAX = 30;

type Choice = { mode: "none" } | { mode: "account"; accountId: number } | { mode: "name"; name: string };

type LoadState =
  | { kind: "loading" }
  | { kind: "forbidden"; message: string }
  | { kind: "error"; message: string }
  | { kind: "ready"; labels: string[] };

type AccountsState = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; accounts: AccountOption[] };

type Notice = { tone: "ready" | "error"; text: string } | null;

const selectClass =
  "mn-focus h-10 w-full rounded-mn-control border border-mn-border bg-mn-bg px-3 text-sm text-mn-text outline-none disabled:opacity-50";

const errorMessage = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

/** 저장된 매핑 → 화면 선택값 */
function toChoices(speakers: SpeakerMapping[]): Record<string, Choice> {
  const result: Record<string, Choice> = {};
  for (const sp of speakers) {
    result[sp.label] = sp.accountId !== null ? { mode: "account", accountId: sp.accountId } : { mode: "name", name: sp.name ?? "" };
  }
  return result;
}

const choiceValue = (choice: Choice) => (choice.mode === "account" ? `account:${choice.accountId}` : choice.mode);

export function SpeakerPanel({ meetingId, onSaved }: { meetingId: number; onSaved: () => Promise<void> }) {
  const baseId = useId();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [accounts, setAccounts] = useState<AccountsState>({ kind: "loading" });
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  // 저장된 매핑의 계정 이름(계정 목록을 못 불러와도 선택값을 보여 주기 위해)
  const [knownNames, setKnownNames] = useState<Record<number, string>>({});
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const speakersRef = useRef<AbortController | null>(null);
  const accountsRef = useRef<AbortController | null>(null);
  const saveRef = useRef<AbortController | null>(null);

  const applySaved = useCallback((speakers: SpeakerMapping[]) => {
    setChoices(toChoices(speakers));
    setKnownNames(Object.fromEntries(speakers.filter((s) => s.accountId !== null).map((s) => [s.accountId as number, s.accountName ?? ""])));
  }, []);

  const loadSpeakers = useCallback(async () => {
    speakersRef.current?.abort();
    const controller = new AbortController();
    speakersRef.current = controller;
    setState({ kind: "loading" });
    try {
      const data = await getSpeakers(meetingId, controller.signal);
      applySaved(data.speakers);
      // 저장된 표기가 전사문 목록에 없더라도 지우지 않고 함께 보여 준다
      const labels = [...data.labels, ...data.speakers.map((s) => s.label).filter((l) => !data.labels.includes(l))];
      setState({ kind: "ready", labels });
    } catch (error) {
      if (isAbortError(error)) return;
      if (error instanceof ApiError && error.status === 403) setState({ kind: "forbidden", message: error.message });
      else setState({ kind: "error", message: errorMessage(error, "화자 정보를 불러오지 못했습니다.") });
    }
  }, [meetingId, applySaved]);

  const loadAccounts = useCallback(async () => {
    accountsRef.current?.abort();
    const controller = new AbortController();
    accountsRef.current = controller;
    setAccounts({ kind: "loading" });
    try {
      setAccounts({ kind: "ready", accounts: await listAccounts(controller.signal) });
    } catch (error) {
      if (isAbortError(error)) return;
      setAccounts({ kind: "error", message: errorMessage(error, "계정 목록을 불러오지 못했습니다.") });
    }
  }, []);

  useEffect(() => {
    void loadSpeakers();
    void loadAccounts();
    return () => {
      speakersRef.current?.abort();
      accountsRef.current?.abort();
      saveRef.current?.abort();
    };
  }, [loadSpeakers, loadAccounts]);

  function setChoice(label: string, value: string) {
    setNotice(null);
    setChoices((prev) => {
      const current = prev[label] ?? { mode: "none" };
      let next: Choice;
      if (value === "none") next = { mode: "none" };
      else if (value === "name") next = { mode: "name", name: current.mode === "name" ? current.name : "" };
      else next = { mode: "account", accountId: Number(value.slice("account:".length)) };
      return { ...prev, [label]: next };
    });
  }

  function setName(label: string, name: string) {
    setNotice(null);
    setChoices((prev) => ({ ...prev, [label]: { mode: "name", name } }));
  }

  async function onSave() {
    if (state.kind !== "ready" || saving) return;
    const body: SpeakerInput[] = [];
    for (const label of state.labels) {
      const choice = choices[label] ?? { mode: "none" };
      if (choice.mode === "account") body.push({ label, accountId: choice.accountId });
      else if (choice.mode === "name") {
        const name = choice.name.trim();
        if (!name) {
          setNotice({ tone: "error", text: `${label}: 미등록 이름을 입력하세요.` });
          return;
        }
        body.push({ label, name: name.slice(0, SPEAKER_NAME_MAX) });
      }
    }
    saveRef.current?.abort();
    const controller = new AbortController();
    saveRef.current = controller;
    setSaving(true);
    setNotice(null);
    try {
      const saved = await saveSpeakers(meetingId, body, controller.signal);
      applySaved(saved.speakers);
      const auto = saved.autoAssignedItemIds.length;
      try {
        await onSaved();
        setNotice({ tone: "ready", text: auto > 0 ? `저장했습니다 · 담당자 자동 지정 ${auto}건` : "저장했습니다" });
      } catch {
        setNotice({ tone: "error", text: "저장했지만 화면을 새로 고치지 못했습니다. 페이지를 새로고침하세요." });
      }
    } catch (error) {
      if (isAbortError(error)) return;
      // 입력값은 그대로 둔다
      setNotice({ tone: "error", text: `저장하지 못했습니다 · ${errorMessage(error, "서버 오류")}` });
    } finally {
      setSaving(false);
    }
  }

  const accountList = accounts.kind === "ready" ? accounts.accounts : [];

  return (
    <section aria-label="화자 확정" className="flex flex-col gap-4 rounded-mn-card border border-mn-border bg-mn-surface p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold tracking-tight">화자 확정</h2>
        <span className="text-xs text-mn-muted">계정이 없는 사람은 이름만 남기며 “이름(미등록)”으로 표시합니다.</span>
      </div>

      {state.kind === "loading" ? (
        <p role="status" className="text-sm text-mn-muted">
          화자 정보를 불러오는 중…
        </p>
      ) : state.kind === "forbidden" ? (
        <p className="text-sm text-mn-muted">화자를 지정할 권한이 없습니다. ({state.message})</p>
      ) : state.kind === "error" ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-sm">
            <StatusDot tone="error" label={`화자 정보를 불러오지 못했습니다 · ${state.message}`} />
          </span>
          <Button size="sm" onClick={() => void loadSpeakers()}>
            다시 시도
          </Button>
        </div>
      ) : state.labels.length === 0 ? (
        <p className="text-sm text-mn-muted">전사문에서 찾은 화자 표기가 없습니다.</p>
      ) : (
        <>
          {accounts.kind === "error" ? (
            <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-mn-card border border-mn-border p-3">
              <span className="text-sm">
                <StatusDot tone="error" label={`계정 목록을 불러오지 못했습니다 · ${accounts.message}`} />
              </span>
              <Button size="sm" onClick={() => void loadAccounts()}>
                계정 목록 다시 불러오기
              </Button>
            </div>
          ) : null}

          <ul className="flex flex-col divide-y divide-mn-border">
            {state.labels.map((label, index) => {
              const choice = choices[label] ?? { mode: "none" };
              const selectId = `${baseId}-s${index}`;
              const nameId = `${baseId}-n${index}`;
              // 저장된 계정이 지금 목록에 없으면(목록 조회 실패 등) 그 계정도 선택지로 남긴다
              const savedOnly =
                choice.mode === "account" && !accountList.some((a) => a.id === choice.accountId)
                  ? [{ id: choice.accountId, name: knownNames[choice.accountId] || `계정 ${choice.accountId}` }]
                  : [];
              return (
                <li key={label} className="grid gap-2 py-3 sm:grid-cols-[120px_minmax(0,1fr)_minmax(0,1fr)] sm:items-center">
                  <label htmlFor={selectId} className="font-mn-mono text-[13px]">
                    {label}
                  </label>
                  <select
                    id={selectId}
                    value={choiceValue(choice)}
                    disabled={saving}
                    onChange={(event) => setChoice(label, event.target.value)}
                    className={selectClass}
                  >
                    <option value="none">지정 안 함</option>
                    {accountList.map((a) => (
                      <option key={a.id} value={`account:${a.id}`}>
                        {a.name} · {RANK_LABEL[a.rank] ?? a.rank}
                      </option>
                    ))}
                    {savedOnly.map((a) => (
                      <option key={a.id} value={`account:${a.id}`}>
                        {a.name}
                      </option>
                    ))}
                    <option value="name">계정 없음 · 이름 직접 입력</option>
                  </select>
                  {choice.mode === "name" ? (
                    <div className="flex items-center gap-2">
                      <label htmlFor={nameId} className="sr-only">
                        {label} 미등록 이름
                      </label>
                      <input
                        id={nameId}
                        type="text"
                        value={choice.name}
                        maxLength={SPEAKER_NAME_MAX}
                        disabled={saving}
                        placeholder="이름 (최대 30자)"
                        onChange={(event) => setName(label, event.target.value)}
                        className={`${selectClass} placeholder:text-mn-muted`}
                      />
                      <span className="shrink-0 text-xs text-mn-muted">(미등록)</span>
                    </div>
                  ) : (
                    <span aria-hidden="true" />
                  )}
                </li>
              );
            })}
          </ul>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <div aria-live="polite" className="text-sm">
              {notice ? (
                notice.tone === "error" ? (
                  <span role="alert">
                    <StatusDot tone="error" label={notice.text} />
                  </span>
                ) : (
                  <StatusDot tone="ready" label={notice.text} />
                )
              ) : null}
            </div>
            <Button variant="primary" disabled={saving} onClick={() => void onSave()}>
              {saving ? "저장 중…" : "화자 저장"}
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
