"use client";

/*
 * 시스템 관리 · 계정 관리(작업 82-2). 이미 있는 /api/admin/accounts 만 쓴다.
 * - 목록(검색·쪽 나눔): 로그인 ID·이름·이메일·사용권한·상태·소속 부서·시스템 관리자 여부. 768 미만은 카드로 바뀐다.
 * - 등록·수정(이름·이메일·사용권한)·활성/비활성·비밀번호 초기화·시스템 관리자 지정/해제. 삭제는 없다(비활성화).
 * - 서버 규칙 거부(자기 자신 비활성화·마지막 시스템 관리자 해제 등)는 서버 문구를 팝업 안에 그대로 보인다.
 * - 비밀번호는 상태에만 잠깐 두고 요청 뒤 비운다(로그·저장소에 남기지 않는다).
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { Button, Modal, StatusDot } from "@/components/mono";
import { Card, Chip, messageOf, SelectField, TextField, inputClass } from "@/components/v2/admin/AdminUi";
import {
  ACCOUNT_PAGE_SIZE,
  createAccount,
  listAccounts,
  patchAccount,
  resetPassword,
  setSystemAdmin,
  type AdminAccount,
  type AdminAccountPage,
} from "@/lib/v2/admin";
import { isAbortError } from "@/lib/v2/errors";
import { RANK_LABEL, type Rank } from "@/lib/v2/types";

const RANK_OPTIONS = (Object.keys(RANK_LABEL) as Rank[]).map((rank) => ({ value: rank, label: RANK_LABEL[rank] }));

type LoadState = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; data: AdminAccountPage };

type Dialog =
  | { kind: "create" }
  | { kind: "edit"; account: AdminAccount }
  | { kind: "password"; account: AdminAccount }
  | { kind: "active"; account: AdminAccount }
  | { kind: "sysadmin"; account: AdminAccount }
  | null;

function departmentsText(account: AdminAccount): string {
  if (account.departments.length === 0) return "소속 없음";
  return account.departments.map((d) => (d.role === "head" ? `${d.name}(부서장)` : d.name)).join(", ");
}

function StatusChips({ account }: { account: AdminAccount }) {
  return (
    <span className="flex flex-wrap gap-2">
      <Chip tone={account.isActive ? "success" : "muted"}>{account.isActive ? "활성" : "비활성"}</Chip>
      {account.isSystemAdmin ? <Chip tone="info">시스템 관리자</Chip> : null}
    </span>
  );
}

function RowActions({ account, onOpen }: { account: AdminAccount; onOpen: (dialog: Dialog) => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      <Button size="sm" onClick={() => onOpen({ kind: "edit", account })} aria-label={`${account.name} 수정`}>
        수정
      </Button>
      <Button size="sm" onClick={() => onOpen({ kind: "active", account })} aria-label={`${account.name} ${account.isActive ? "비활성화" : "활성화"}`}>
        {account.isActive ? "비활성화" : "활성화"}
      </Button>
      <Button size="sm" onClick={() => onOpen({ kind: "password", account })} aria-label={`${account.name} 비밀번호 초기화`}>
        비밀번호 초기화
      </Button>
      <Button size="sm" onClick={() => onOpen({ kind: "sysadmin", account })} aria-label={`${account.name} 시스템 관리자 ${account.isSystemAdmin ? "해제" : "지정"}`}>
        {account.isSystemAdmin ? "시스템 관리자 해제" : "시스템 관리자 지정"}
      </Button>
    </div>
  );
}

export default function AdminAccountsPage() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ kind: "loading" });
    try {
      const data = await listAccounts({ q: query, page, size: ACCOUNT_PAGE_SIZE }, controller.signal);
      if (!controller.signal.aborted) setState({ kind: "ready", data });
    } catch (error) {
      if (isAbortError(error)) return;
      setState({ kind: "error", message: messageOf(error, "계정 목록을 불러오지 못했습니다.") });
    }
  }, [query, page]);

  useEffect(() => {
    void load();
    return () => controllerRef.current?.abort();
  }, [load]);

  async function afterChange(message: string) {
    setNotice(message);
    await load();
  }

  const data = state.kind === "ready" ? state.data : null;
  const lastPage = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1;

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <form
          role="search"
          aria-label="계정 검색"
          className="flex min-w-0 flex-1 flex-wrap items-center gap-2 md:max-w-lg"
          onSubmit={(event) => {
            event.preventDefault();
            setPage(1);
            setQuery(searchInput.trim());
          }}
        >
          <label htmlFor="admin-account-search" className="sr-only">
            로그인 ID·이름·이메일 검색
          </label>
          <input
            id="admin-account-search"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="로그인 ID·이름·이메일 검색"
            className={`${inputClass} flex-1`}
          />
          <Button type="submit">검색</Button>
        </form>
        <Button variant="primary" onClick={() => setDialog({ kind: "create" })}>
          계정 등록
        </Button>
      </div>

      {notice ? (
        <p role="status" className="text-sm">
          <StatusDot tone="ready" label={notice} />
        </p>
      ) : null}

      {state.kind === "loading" ? (
        <p role="status" className="text-sm text-mn-muted">
          계정 목록을 불러오는 중…
        </p>
      ) : null}
      {state.kind === "error" ? (
        <Card>
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={state.message} />
          </p>
          <Button size="sm" className="mt-3" onClick={() => void load()}>
            다시 시도
          </Button>
        </Card>
      ) : null}

      {data ? (
        <>
          <p className="text-sm text-mn-muted" aria-live="polite">
            전체 <span className="font-mn-mono text-mn-text">{data.total}</span>명
          </p>
          {data.items.length === 0 ? (
            <Card>
              <p className="text-sm text-mn-muted">조건에 맞는 계정이 없습니다.</p>
            </Card>
          ) : (
            <>
              {/* 768 이상: 표 */}
              <div className="hidden overflow-hidden rounded-mn-card border border-mn-border md:block">
                <table aria-label="계정 목록" className="w-full table-fixed text-left text-sm">
                  <thead className="bg-mn-surface text-xs text-mn-muted">
                    <tr>
                      <th scope="col" className="w-[13%] px-3 py-3 font-medium">로그인 ID</th>
                      <th scope="col" className="w-[12%] px-3 py-3 font-medium">이름</th>
                      <th scope="col" className="w-[17%] px-3 py-3 font-medium">이메일</th>
                      <th scope="col" className="w-[10%] px-3 py-3 font-medium">사용권한</th>
                      <th scope="col" className="w-[15%] px-3 py-3 font-medium">상태</th>
                      <th scope="col" className="w-[15%] px-3 py-3 font-medium">소속 부서</th>
                      <th scope="col" className="px-3 py-3 font-medium">관리</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((account) => (
                      <tr key={account.id} className="border-t border-mn-border align-top">
                        <td className="break-words px-3 py-3 font-mn-mono text-[13px]">{account.loginId}</td>
                        <td className="break-words px-3 py-3">{account.name}</td>
                        <td className="break-words px-3 py-3">{account.email || "—"}</td>
                        <td className="px-3 py-3">{RANK_LABEL[account.rank] ?? account.rank}</td>
                        <td className="px-3 py-3">
                          <StatusChips account={account} />
                        </td>
                        <td className="break-words px-3 py-3">{departmentsText(account)}</td>
                        <td className="px-3 py-3">
                          <RowActions account={account} onOpen={setDialog} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {/* 768 미만: 카드 */}
              <ul aria-label="계정 목록(카드)" className="flex flex-col gap-3 md:hidden">
                {data.items.map((account) => (
                  <li key={account.id} className="min-w-0 rounded-mn-card border border-mn-border bg-mn-surface p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="break-words text-sm font-medium">{account.name}</span>
                      <StatusChips account={account} />
                    </div>
                    <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs text-mn-muted">
                      <dt>로그인 ID</dt>
                      <dd className="break-words font-mn-mono text-mn-text">{account.loginId}</dd>
                      <dt>이메일</dt>
                      <dd className="break-words text-mn-text">{account.email || "—"}</dd>
                      <dt>사용권한</dt>
                      <dd className="text-mn-text">{RANK_LABEL[account.rank] ?? account.rank}</dd>
                      <dt>소속 부서</dt>
                      <dd className="break-words text-mn-text">{departmentsText(account)}</dd>
                    </dl>
                    <div className="mt-3">
                      <RowActions account={account} onOpen={setDialog} />
                    </div>
                  </li>
                ))}
              </ul>
              <nav aria-label="쪽 이동" className="flex items-center justify-between gap-3">
                <Button size="sm" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                  이전
                </Button>
                <span className="font-mn-mono text-xs text-mn-muted">
                  {page} / {lastPage}
                </span>
                <Button size="sm" disabled={page >= lastPage} onClick={() => setPage((p) => p + 1)}>
                  다음
                </Button>
              </nav>
            </>
          )}
        </>
      ) : null}

      <CreateDialog open={dialog?.kind === "create"} onClose={() => setDialog(null)} onDone={(name) => afterChange(`계정을 등록했습니다: ${name}`)} />
      <EditDialog
        account={dialog?.kind === "edit" ? dialog.account : null}
        onClose={() => setDialog(null)}
        onDone={(name) => afterChange(`계정을 수정했습니다: ${name}`)}
      />
      <PasswordDialog
        account={dialog?.kind === "password" ? dialog.account : null}
        onClose={() => setDialog(null)}
        onDone={(name) => afterChange(`비밀번호를 초기화했습니다: ${name}`)}
      />
      <ToggleDialog
        dialog={dialog?.kind === "active" || dialog?.kind === "sysadmin" ? dialog : null}
        onClose={() => setDialog(null)}
        onDone={afterChange}
      />
    </>
  );
}

/* ---------------- 등록 ---------------- */
function CreateDialog({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: (name: string) => Promise<void> }) {
  const [loginId, setLoginId] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [rank, setRank] = useState<Rank>("staff");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setLoginId("");
    setName("");
    setEmail("");
    setRank("staff");
    setPassword("");
    setConfirm("");
    setError(null);
    setSaving(false);
  }, [open]);

  const blocked = !loginId.trim() || !name.trim() || !password;

  async function submit() {
    if (saving || blocked) return;
    if (password !== confirm) {
      setError("비밀번호와 비밀번호 확인이 서로 다릅니다.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const created = await createAccount({ loginId: loginId.trim(), name: name.trim(), email: email.trim(), rank, password });
      setPassword("");
      setConfirm("");
      await onDone(created.name);
      onClose();
    } catch (err) {
      if (isAbortError(err)) return;
      setError(messageOf(err, "계정을 등록하지 못했습니다."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="계정 등록"
      description="새 계정을 만듭니다. 초기 비밀번호는 사용자에게 따로 전달하세요."
      confirmLabel="등록"
      cancelLabel="취소"
      onConfirm={() => void submit()}
      confirmDisabled={blocked}
      confirmLoading={saving}
    >
      <div className="mt-3 flex flex-col gap-3">
        <TextField label="로그인 ID" value={loginId} onChange={setLoginId} autoComplete="off" required disabled={saving} />
        <TextField label="이름" value={name} onChange={setName} autoComplete="off" required disabled={saving} />
        <TextField label="이메일" type="email" value={email} onChange={setEmail} autoComplete="off" disabled={saving} />
        <SelectField label="사용권한" value={rank} onChange={(value) => setRank(value as Rank)} options={RANK_OPTIONS} disabled={saving} />
        <TextField
          label="초기 비밀번호"
          type="password"
          value={password}
          onChange={setPassword}
          autoComplete="new-password"
          required
          disabled={saving}
          hint="서버 정책에 따른 최소 글자 수 이상이어야 합니다(기본 10자)."
        />
        <TextField label="비밀번호 확인" type="password" value={confirm} onChange={setConfirm} autoComplete="new-password" required disabled={saving} />
        {error ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={error} />
          </p>
        ) : null}
      </div>
    </Modal>
  );
}

/* ---------------- 수정 ---------------- */
function EditDialog({
  account,
  onClose,
  onDone,
}: {
  account: AdminAccount | null;
  onClose: () => void;
  onDone: (name: string) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [rank, setRank] = useState<Rank>("staff");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const accountId = account?.id;

  useEffect(() => {
    if (!account) return;
    setName(account.name);
    setEmail(account.email);
    setRank(account.rank);
    setError(null);
    setSaving(false);
    // account 객체가 아니라 id 가 바뀔 때만 입력값을 다시 채운다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountId]);

  async function submit() {
    if (!account || saving || !name.trim()) return;
    const patch: { name?: string; email?: string; rank?: Rank } = {};
    if (name.trim() !== account.name) patch.name = name.trim();
    if (email.trim() !== account.email) patch.email = email.trim();
    if (rank !== account.rank) patch.rank = rank;
    if (Object.keys(patch).length === 0) {
      onClose();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await patchAccount(account.id, patch);
      await onDone(name.trim());
      onClose();
    } catch (err) {
      if (isAbortError(err)) return;
      setError(messageOf(err, "계정을 수정하지 못했습니다."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={account !== null}
      onClose={onClose}
      title="계정 수정"
      description={account ? `로그인 ID ${account.loginId}` : undefined}
      confirmLabel="저장"
      cancelLabel="취소"
      onConfirm={() => void submit()}
      confirmDisabled={!name.trim()}
      confirmLoading={saving}
    >
      <div className="mt-3 flex flex-col gap-3">
        <TextField label="이름" value={name} onChange={setName} autoComplete="off" required disabled={saving} />
        <TextField label="이메일" type="email" value={email} onChange={setEmail} autoComplete="off" disabled={saving} />
        <SelectField label="사용권한" value={rank} onChange={(value) => setRank(value as Rank)} options={RANK_OPTIONS} disabled={saving} />
        {error ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={error} />
          </p>
        ) : null}
      </div>
    </Modal>
  );
}

/* ---------------- 비밀번호 초기화 ---------------- */
function PasswordDialog({
  account,
  onClose,
  onDone,
}: {
  account: AdminAccount | null;
  onClose: () => void;
  onDone: (name: string) => Promise<void>;
}) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const accountId = account?.id;

  useEffect(() => {
    if (!account) return;
    setPassword("");
    setConfirm("");
    setError(null);
    setSaving(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountId]);

  async function submit() {
    if (!account || saving || !password) return;
    if (password !== confirm) {
      setError("새 비밀번호와 확인이 서로 다릅니다.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await resetPassword(account.id, password);
      setPassword("");
      setConfirm("");
      await onDone(account.name);
      onClose();
    } catch (err) {
      if (isAbortError(err)) return;
      setError(messageOf(err, "비밀번호를 초기화하지 못했습니다."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={account !== null}
      onClose={onClose}
      title="비밀번호 초기화"
      description={account ? `대상 ${account.name} (${account.loginId})` : undefined}
      confirmLabel="초기화"
      cancelLabel="취소"
      onConfirm={() => void submit()}
      confirmDisabled={!password}
      confirmLoading={saving}
    >
      <div className="mt-3 flex flex-col gap-3">
        <TextField label="새 비밀번호" type="password" value={password} onChange={setPassword} autoComplete="new-password" required disabled={saving} />
        <TextField label="새 비밀번호 확인" type="password" value={confirm} onChange={setConfirm} autoComplete="new-password" required disabled={saving} />
        {error ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={error} />
          </p>
        ) : null}
      </div>
    </Modal>
  );
}

/* ---------------- 활성/비활성·시스템 관리자 지정/해제(확인 창) ---------------- */
function ToggleDialog({
  dialog,
  onClose,
  onDone,
}: {
  dialog: { kind: "active" | "sysadmin"; account: AdminAccount } | null;
  onClose: () => void;
  onDone: (message: string) => Promise<void>;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = dialog ? `${dialog.kind}-${dialog.account.id}` : null;

  useEffect(() => {
    setSaving(false);
    setError(null);
  }, [key]);

  if (!dialog) {
    return <Modal open={false} onClose={onClose} title="" />;
  }
  const { account } = dialog;
  const active = dialog.kind === "active";
  const title = active ? (account.isActive ? "계정 비활성화" : "계정 활성화") : account.isSystemAdmin ? "시스템 관리자 해제" : "시스템 관리자 지정";
  const confirmLabel = active ? (account.isActive ? "비활성화하기" : "활성화하기") : account.isSystemAdmin ? "시스템 관리자 해제하기" : "시스템 관리자로 지정하기";
  const notice = active
    ? account.isActive
      ? "비활성화하면 이 계정은 로그인할 수 없습니다. 기존 기록은 그대로 남습니다."
      : "활성화하면 이 계정은 다시 로그인할 수 있습니다."
    : account.isSystemAdmin
      ? "해제하면 이 계정은 시스템 관리 기능을 쓸 수 없습니다. 자기 자신이나 마지막 시스템 관리자는 해제할 수 없습니다."
      : "시스템 관리자는 회의록 내용을 볼 수 없는 전용 계정이어야 하며, 사용권한이 담당자인 계정만 지정할 수 있습니다.";

  async function run() {
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      if (active) {
        await patchAccount(account.id, { isActive: !account.isActive });
        await onDone(`${account.name} 계정을 ${account.isActive ? "비활성화" : "활성화"}했습니다.`);
      } else {
        await setSystemAdmin(account.id, !account.isSystemAdmin);
        await onDone(`${account.name} 계정의 시스템 관리자 ${account.isSystemAdmin ? "지정을 해제" : "지정을 완료"}했습니다.`);
      }
      onClose();
    } catch (err) {
      if (isAbortError(err)) return;
      setError(messageOf(err, "처리하지 못했습니다."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      tone={active && account.isActive ? "danger" : "default"}
      title={title}
      description={`대상 ${account.name} (${account.loginId})`}
      confirmLabel={confirmLabel}
      cancelLabel="취소"
      onConfirm={() => void run()}
      confirmLoading={saving}
    >
      <div className="mt-3 flex flex-col gap-3">
        <p className="text-sm text-mn-text">{notice}</p>
        {error ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={error} />
          </p>
        ) : null}
      </div>
    </Modal>
  );
}
