"use client";

/*
 * 시스템 관리 · 부서 관리(작업 82-2). 이미 있는 /api/admin/departments 만 쓴다.
 * - 목록(상위·하위 부서를 들여쓰기로 표시)·등록·이름 변경·구성원 추가/제거·부서장 지정. 부서 삭제 버튼은 없다(API 도 없음).
 * - 규칙 거부(부서장은 관리자 이상·부서장은 소속 제거 불가·이름 중복 등)는 서버 문구를 그대로 보인다.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { Button, Modal, StatusDot } from "@/components/mono";
import { Card, Chip, messageOf, SelectField, TextField } from "@/components/v2/admin/AdminUi";
import {
  addDepartmentMember,
  createDepartment,
  listAccounts,
  listDepartments,
  removeDepartmentMember,
  renameDepartment,
  setDepartmentHead,
  type AdminAccount,
  type AdminDepartment,
  type AdminMember,
} from "@/lib/v2/admin";
import { isAbortError } from "@/lib/v2/errors";
import { RANK_LABEL } from "@/lib/v2/types";

type LoadState = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; items: AdminDepartment[] };

type Dialog =
  | { kind: "create" }
  | { kind: "rename"; department: AdminDepartment }
  | { kind: "add"; department: AdminDepartment }
  | { kind: "remove"; department: AdminDepartment; member: AdminMember }
  | { kind: "head"; department: AdminDepartment; member: AdminMember }
  | null;

/** 부서 목록을 상위 → 하위 순서로 펼치고 깊이를 붙인다(순환·누락 방어: 방문한 부서는 한 번만) */
function flatten(items: AdminDepartment[]): { department: AdminDepartment; depth: number }[] {
  const byParent = new Map<number | null, AdminDepartment[]>();
  const ids = new Set(items.map((d) => d.id));
  for (const d of items) {
    const parent = d.parentId !== null && ids.has(d.parentId) ? d.parentId : null;
    byParent.set(parent, [...(byParent.get(parent) ?? []), d]);
  }
  const out: { department: AdminDepartment; depth: number }[] = [];
  const seen = new Set<number>();
  const walk = (parent: number | null, depth: number) => {
    for (const d of byParent.get(parent) ?? []) {
      if (seen.has(d.id)) continue;
      seen.add(d.id);
      out.push({ department: d, depth });
      walk(d.id, depth + 1);
    }
  };
  walk(null, 0);
  for (const d of items) if (!seen.has(d.id)) out.push({ department: d, depth: 0 });
  return out;
}

export default function AdminDepartmentsPage() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [dialog, setDialog] = useState<Dialog>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState((prev) => (prev.kind === "ready" ? prev : { kind: "loading" }));
    try {
      const items = await listDepartments(controller.signal);
      if (!controller.signal.aborted) setState({ kind: "ready", items });
    } catch (error) {
      if (isAbortError(error)) return;
      setState({ kind: "error", message: messageOf(error, "부서 목록을 불러오지 못했습니다.") });
    }
  }, []);

  useEffect(() => {
    void load();
    return () => controllerRef.current?.abort();
  }, [load]);

  async function afterChange(message: string) {
    setNotice(message);
    await load();
  }

  const items = state.kind === "ready" ? state.items : [];

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-mn-muted">부서·소속·부서장을 관리합니다. 부서는 삭제하지 않습니다.</p>
        <Button variant="primary" onClick={() => setDialog({ kind: "create" })}>
          부서 등록
        </Button>
      </div>

      {notice ? (
        <p role="status" className="text-sm">
          <StatusDot tone="ready" label={notice} />
        </p>
      ) : null}
      {state.kind === "loading" ? (
        <p role="status" className="text-sm text-mn-muted">
          부서 목록을 불러오는 중…
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

      {state.kind === "ready" && items.length === 0 ? (
        <Card>
          <p className="text-sm text-mn-muted">등록된 부서가 없습니다. 부서를 등록해 주세요.</p>
        </Card>
      ) : null}

      <ul aria-label="부서 목록" className="flex flex-col gap-3">
        {flatten(items).map(({ department, depth }) => (
          <li key={department.id} style={{ marginLeft: `${Math.min(depth, 4) * 16}px` }} className="min-w-0">
            <Card label={`부서 ${department.name}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <h2 className="break-words text-base font-semibold">{department.name}</h2>
                  {department.kind === "executive" ? <Chip tone="info">임원 그룹</Chip> : null}
                  <span className="text-xs text-mn-muted">
                    부서장 {department.head ? department.head.name : "없음"} · 구성원 {department.members.length}명
                  </span>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" onClick={() => setDialog({ kind: "rename", department })} aria-label={`${department.name} 이름 변경`}>
                    이름 변경
                  </Button>
                  <Button size="sm" onClick={() => setDialog({ kind: "add", department })} aria-label={`${department.name} 구성원 추가`}>
                    구성원 추가
                  </Button>
                </div>
              </div>
              {department.members.length > 0 ? (
                <ul className="mt-3 flex flex-col divide-y divide-mn-border border-t border-mn-border">
                  {department.members.map((member) => (
                    <li key={member.accountId} className="flex flex-wrap items-center justify-between gap-2 py-2">
                      <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
                        <span className="break-words">{member.name}</span>
                        <span className="text-xs text-mn-muted">{RANK_LABEL[member.rank] ?? member.rank}</span>
                        {member.role === "head" ? <Chip tone="success">부서장</Chip> : null}
                        {!member.isActive ? <Chip tone="muted">비활성</Chip> : null}
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {member.role !== "head" ? (
                          <Button size="sm" onClick={() => setDialog({ kind: "head", department, member })} aria-label={`${member.name} 부서장 지정`}>
                            부서장 지정
                          </Button>
                        ) : null}
                        <Button size="sm" onClick={() => setDialog({ kind: "remove", department, member })} aria-label={`${member.name} 소속 제거`}>
                          소속 제거
                        </Button>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-3 text-xs text-mn-muted">구성원이 없습니다.</p>
              )}
            </Card>
          </li>
        ))}
      </ul>

      <CreateDepartmentDialog
        open={dialog?.kind === "create"}
        departments={items}
        onClose={() => setDialog(null)}
        onDone={(name) => afterChange(`부서를 등록했습니다: ${name}`)}
      />
      <RenameDialog
        department={dialog?.kind === "rename" ? dialog.department : null}
        onClose={() => setDialog(null)}
        onDone={(name) => afterChange(`부서 이름을 변경했습니다: ${name}`)}
      />
      <AddMemberDialog
        department={dialog?.kind === "add" ? dialog.department : null}
        onClose={() => setDialog(null)}
        onDone={(message) => afterChange(message)}
      />
      <ConfirmDialog dialog={dialog?.kind === "remove" || dialog?.kind === "head" ? dialog : null} onClose={() => setDialog(null)} onDone={afterChange} />
    </>
  );
}

/* ---------------- 등록 ---------------- */
function CreateDepartmentDialog({
  open,
  departments,
  onClose,
  onDone,
}: {
  open: boolean;
  departments: AdminDepartment[];
  onClose: () => void;
  onDone: (name: string) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [parentId, setParentId] = useState("");
  const [executive, setExecutive] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setName("");
    setParentId("");
    setExecutive(false);
    setError(null);
    setSaving(false);
  }, [open]);

  async function submit() {
    if (saving || !name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await createDepartment({ name: name.trim(), parentId: parentId ? Number(parentId) : null, isExecutiveGroup: executive });
      await onDone(name.trim());
      onClose();
    } catch (err) {
      if (isAbortError(err)) return;
      setError(messageOf(err, "부서를 등록하지 못했습니다."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="부서 등록"
      confirmLabel="등록"
      cancelLabel="취소"
      onConfirm={() => void submit()}
      confirmDisabled={!name.trim()}
      confirmLoading={saving}
    >
      <div className="mt-3 flex flex-col gap-3">
        <TextField label="부서 이름" value={name} onChange={setName} autoComplete="off" required disabled={saving} />
        <SelectField
          label="상위 부서"
          value={parentId}
          onChange={setParentId}
          disabled={saving}
          options={[{ value: "", label: "없음(최상위)" }, ...departments.map((d) => ({ value: String(d.id), label: d.name }))]}
        />
        <label className="flex min-h-10 items-center gap-2 text-sm">
          <input type="checkbox" checked={executive} disabled={saving} onChange={(event) => setExecutive(event.target.checked)} className="mn-focus size-4" />
          임원 그룹으로 등록
        </label>
        {error ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={error} />
          </p>
        ) : null}
      </div>
    </Modal>
  );
}

/* ---------------- 이름 변경 ---------------- */
function RenameDialog({
  department,
  onClose,
  onDone,
}: {
  department: AdminDepartment | null;
  onClose: () => void;
  onDone: (name: string) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const departmentId = department?.id;

  useEffect(() => {
    if (!department) return;
    setName(department.name);
    setError(null);
    setSaving(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [departmentId]);

  async function submit() {
    if (!department || saving || !name.trim()) return;
    if (name.trim() === department.name) {
      onClose();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await renameDepartment(department.id, name.trim());
      await onDone(name.trim());
      onClose();
    } catch (err) {
      if (isAbortError(err)) return;
      setError(messageOf(err, "이름을 변경하지 못했습니다."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={department !== null}
      onClose={onClose}
      title="부서 이름 변경"
      confirmLabel="변경"
      cancelLabel="취소"
      onConfirm={() => void submit()}
      confirmDisabled={!name.trim()}
      confirmLoading={saving}
    >
      <div className="mt-3 flex flex-col gap-3">
        <TextField label="부서 이름" value={name} onChange={setName} autoComplete="off" required disabled={saving} />
        {error ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={error} />
          </p>
        ) : null}
      </div>
    </Modal>
  );
}

/* ---------------- 구성원 추가(계정 검색 후 선택) ---------------- */
function AddMemberDialog({
  department,
  onClose,
  onDone,
}: {
  department: AdminDepartment | null;
  onClose: () => void;
  onDone: (message: string) => Promise<void>;
}) {
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<AdminAccount[] | null>(null);
  const [chosen, setChosen] = useState<AdminAccount | null>(null);
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const departmentId = department?.id;

  useEffect(() => {
    if (!department) return;
    setSearch("");
    setResults(null);
    setChosen(null);
    setError(null);
    setSaving(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [departmentId]);

  async function runSearch() {
    setSearching(true);
    setError(null);
    try {
      const page = await listAccounts({ q: search.trim(), page: 1, size: 20 });
      const already = new Set(department?.members.map((m) => m.accountId) ?? []);
      setResults(page.items.filter((a) => a.isActive && !already.has(a.id)));
    } catch (err) {
      if (isAbortError(err)) return;
      setError(messageOf(err, "계정을 검색하지 못했습니다."));
    } finally {
      setSearching(false);
    }
  }

  async function submit() {
    if (!department || !chosen || saving) return;
    setSaving(true);
    setError(null);
    try {
      await addDepartmentMember(department.id, chosen.id);
      await onDone(`${chosen.name}을(를) ${department.name} 부서에 추가했습니다.`);
      onClose();
    } catch (err) {
      if (isAbortError(err)) return;
      setError(messageOf(err, "구성원을 추가하지 못했습니다."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={department !== null}
      onClose={onClose}
      title="구성원 추가"
      description={department ? `대상 부서 ${department.name}` : undefined}
      confirmLabel="추가"
      cancelLabel="취소"
      onConfirm={() => void submit()}
      confirmDisabled={!chosen}
      confirmLoading={saving}
    >
      <div className="mt-3 flex flex-col gap-3">
        <div className="flex min-w-0 items-end gap-2">
          <div className="min-w-0 flex-1">
            <TextField label="계정 검색(이름·로그인 ID)" value={search} onChange={setSearch} autoComplete="off" disabled={saving} />
          </div>
          <Button onClick={() => void runSearch()} disabled={searching || saving}>
            검색
          </Button>
        </div>
        {results !== null ? (
          results.length === 0 ? (
            <p className="text-sm text-mn-muted">추가할 수 있는 계정이 없습니다.</p>
          ) : (
            <ul aria-label="검색 결과" className="flex max-h-56 flex-col gap-1 overflow-y-auto">
              {results.map((account) => {
                const selected = chosen?.id === account.id;
                return (
                  <li key={account.id}>
                    <button
                      type="button"
                      aria-pressed={selected}
                      onClick={() => setChosen(account)}
                      className={[
                        "mn-focus flex w-full min-w-0 flex-wrap items-center justify-between gap-2 rounded-mn-control border px-3 py-2 text-left text-sm",
                        selected ? "border-mn-accent bg-mn-elevated font-medium" : "border-mn-control text-mn-muted hover:text-mn-text",
                      ].join(" ")}
                    >
                      <span className="break-words">{account.name}</span>
                      <span className="text-xs">
                        <span className="font-mn-mono">{account.loginId}</span> · {RANK_LABEL[account.rank] ?? account.rank}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )
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

/* ---------------- 소속 제거·부서장 지정(확인 창) ---------------- */
function ConfirmDialog({
  dialog,
  onClose,
  onDone,
}: {
  dialog: { kind: "remove" | "head"; department: AdminDepartment; member: AdminMember } | null;
  onClose: () => void;
  onDone: (message: string) => Promise<void>;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = dialog ? `${dialog.kind}-${dialog.department.id}-${dialog.member.accountId}` : null;

  useEffect(() => {
    setSaving(false);
    setError(null);
  }, [key]);

  if (!dialog) return <Modal open={false} onClose={onClose} title="" />;
  const { department, member } = dialog;
  const removing = dialog.kind === "remove";

  async function run() {
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      if (removing) {
        await removeDepartmentMember(department.id, member.accountId);
        await onDone(`${member.name}의 ${department.name} 소속을 제거했습니다.`);
      } else {
        await setDepartmentHead(department.id, member.accountId);
        await onDone(`${member.name}을(를) ${department.name} 부서장으로 지정했습니다.`);
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
      tone={removing ? "danger" : "default"}
      title={removing ? "소속 제거" : "부서장 지정"}
      description={`${department.name} · ${member.name}`}
      confirmLabel={removing ? "소속 제거하기" : "부서장으로 지정하기"}
      cancelLabel="취소"
      onConfirm={() => void run()}
      confirmLoading={saving}
    >
      <div className="mt-3 flex flex-col gap-3">
        <p className="text-sm text-mn-text">
          {removing
            ? "소속을 제거합니다. 부서장은 먼저 다른 사람을 부서장으로 지정해야 제거할 수 있습니다."
            : "기존 부서장은 부서원이 됩니다. 부서장은 사용권한이 관리자 이상인 계정만 될 수 있습니다."}
        </p>
        {error ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={error} />
          </p>
        ) : null}
      </div>
    </Modal>
  );
}
