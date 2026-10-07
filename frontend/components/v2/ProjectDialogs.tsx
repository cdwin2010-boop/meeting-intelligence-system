"use client";

/*
 * 프로젝트 상세의 팝업(작업 69-1): 참여자 추가, 총괄 변경. 공용 Modal 을 그대로 쓴다(포커스 가두기·Esc·닫으면 연 버튼으로 복귀).
 * - 참여자 추가: 후보(우리 부서·다른 부서·임원 그룹, 이미 참여한 사람 제외)에서 한 명을 고르고 역할(참여자·관리자)을 정한다.
 *   후보 API 를 쓸 수 없으면(예: 총괄이 등록 부서 소속이 아님) 같은 고객사 계정 목록으로 대신한다. 직급 때문에 서버가 거부하면(409) 서버 문구를 그대로 보인다.
 * - 총괄 변경: 새 총괄(관리자 이상 직급) 선택 + 사유(필수, 공백 금지, 2000자). 이전 총괄은 관리자 역할로 남는다(서버 처리).
 */
import { useEffect, useId, useState } from "react";

import { Modal, StatusDot } from "@/components/mono";
import { listAccounts } from "@/lib/v2/accounts";
import { isAbortError } from "@/lib/v2/errors";
import { REASON_MAX } from "@/lib/v2/meetings";
import { addProjectMember, changeProjectLead, getCandidates, type ProjectDetail, type ProjectRole } from "@/lib/v2/projects";
import { RANK_LABEL, type Rank } from "@/lib/v2/types";

const fieldClass =
  "mn-focus h-10 w-full rounded-mn-control border border-mn-control bg-mn-bg px-3 text-sm text-mn-text outline-none disabled:opacity-50";
const errorMessage = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

interface Option {
  accountId: number;
  name: string;
  rank: Rank;
}
interface OptionGroup {
  label: string;
  members: Option[];
}

interface AddMemberDialogProps {
  project: ProjectDetail;
  open: boolean;
  onClose: () => void;
  onSaved: (project: ProjectDetail) => void;
}

export function AddMemberDialog({ project, open, onClose, onSaved }: AddMemberDialogProps) {
  const personId = useId();
  const roleId = useId();
  const [groups, setGroups] = useState<OptionGroup[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [accountId, setAccountId] = useState("");
  const [role, setRole] = useState<ProjectRole>("member");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setAccountId("");
    setRole("member");
    setError(null);
    setSaving(false);
    setGroups(null);
    setLoadError(null);
    const controller = new AbortController();
    const taken = new Set(project.members.map((m) => m.accountId));
    const strip = (label: string, members: Option[]): OptionGroup => ({ label, members: members.filter((m) => !taken.has(m.accountId)) });
    (async () => {
      try {
        const data = await getCandidates(project.departmentId, controller.signal);
        setGroups([
          strip("우리 부서", data.ownDepartment.members),
          ...data.otherDepartments.map((g) => strip(`다른 부서 · ${g.departmentName}`, g.members)),
          ...data.executiveGroup.map((g) => strip(`임원 그룹 · ${g.departmentName}`, g.members)),
        ].filter((g) => g.members.length > 0));
      } catch (e) {
        if (isAbortError(e)) return;
        try {
          const accounts = await listAccounts(controller.signal); // 후보 API 를 쓸 수 없으면 같은 고객사 계정 목록으로 대신한다
          setGroups([strip("전체", accounts.map((a) => ({ accountId: a.id, name: a.name, rank: a.rank })))].filter((g) => g.members.length > 0));
        } catch (e2) {
          if (!isAbortError(e2)) setLoadError(errorMessage(e2, "후보를 불러오지 못했습니다."));
        }
      }
    })();
    return () => controller.abort();
    // 열릴 때만 불러온다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function onConfirm() {
    if (saving || accountId === "") return;
    setSaving(true);
    setError(null);
    try {
      const saved = await addProjectMember(project.id, Number(accountId), role);
      setSaving(false);
      onSaved(saved);
    } catch (e) {
      setError(errorMessage(e, "서버 오류"));
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="참여자 추가"
      description="프로젝트에 참여할 사람과 역할을 고르세요. 관리자 역할은 관리자 이상 직급만 맡을 수 있습니다."
      confirmLabel="참여자 추가"
      cancelLabel="취소"
      initialFocus="cancel"
      onConfirm={() => void onConfirm()}
      confirmDisabled={accountId === ""}
      confirmLoading={saving}
      panelClassName="w-full max-w-lg"
    >
      <div className="mt-3 flex flex-col gap-4">
        {groups === null && loadError === null ? (
          <p role="status" className="text-sm text-mn-muted">
            후보를 불러오는 중…
          </p>
        ) : null}
        {loadError ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={`후보를 불러오지 못했습니다 · ${loadError}`} />
          </p>
        ) : null}
        {groups !== null && groups.length === 0 ? <p className="text-sm text-mn-muted">추가할 수 있는 사람이 없습니다.</p> : null}
        {groups !== null && groups.length > 0 ? (
          <>
            <div className="flex flex-col gap-1">
              <label htmlFor={personId} className="text-sm font-medium">
                추가할 사람
              </label>
              <select id={personId} value={accountId} disabled={saving} onChange={(e) => setAccountId(e.target.value)} className={fieldClass}>
                <option value="">선택하세요</option>
                {groups.map((g) => (
                  <optgroup key={g.label} label={g.label}>
                    {g.members.map((m) => (
                      <option key={m.accountId} value={m.accountId}>
                        {m.name} ({RANK_LABEL[m.rank] ?? m.rank})
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor={roleId} className="text-sm font-medium">
                역할
              </label>
              <select id={roleId} value={role} disabled={saving} onChange={(e) => setRole(e.target.value as ProjectRole)} className={fieldClass}>
                <option value="member">참여자</option>
                <option value="manager">관리자</option>
              </select>
            </div>
          </>
        ) : null}
        {error ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={`추가하지 못했습니다 · ${error}`} />
          </p>
        ) : null}
      </div>
    </Modal>
  );
}

interface ChangeLeadDialogProps {
  project: ProjectDetail;
  open: boolean;
  onClose: () => void;
  onSaved: (project: ProjectDetail) => void;
}

export function ChangeLeadDialog({ project, open, onClose, onSaved }: ChangeLeadDialogProps) {
  const personId = useId();
  const reasonId = useId();
  const [accounts, setAccounts] = useState<Option[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [newLeadId, setNewLeadId] = useState("");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setNewLeadId("");
    setReason("");
    setError(null);
    setSaving(false);
    setAccounts(null);
    setLoadError(null);
    const controller = new AbortController();
    listAccounts(controller.signal)
      .then((list) =>
        // 새 총괄은 관리자 이상 직급만(현재 총괄 제외)
        setAccounts(list.filter((a) => a.rank !== "staff" && a.id !== project.lead?.id).map((a) => ({ accountId: a.id, name: a.name, rank: a.rank }))),
      )
      .catch((e) => {
        if (!isAbortError(e)) setLoadError(errorMessage(e, "계정 목록을 불러오지 못했습니다."));
      });
    return () => controller.abort();
    // 열릴 때만 불러온다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const trimmed = reason.trim();
  const blocked = newLeadId === "" || trimmed === "" || trimmed.length > REASON_MAX;

  async function onConfirm() {
    if (saving || blocked) return;
    setSaving(true);
    setError(null);
    try {
      const saved = await changeProjectLead(project.id, Number(newLeadId), trimmed);
      setSaving(false);
      onSaved(saved);
    } catch (e) {
      setError(errorMessage(e, "서버 오류"));
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="총괄 변경"
      description="새 총괄은 관리자 이상 직급이어야 합니다. 이전 총괄은 관리자 역할로 프로젝트에 남고, 이미 확정된 건은 그대로입니다."
      confirmLabel="총괄 변경"
      cancelLabel="취소"
      initialFocus="cancel"
      onConfirm={() => void onConfirm()}
      confirmDisabled={blocked}
      confirmLoading={saving}
      panelClassName="w-full max-w-lg"
    >
      <div className="mt-3 flex flex-col gap-4">
        {accounts === null && loadError === null ? (
          <p role="status" className="text-sm text-mn-muted">
            계정 목록을 불러오는 중…
          </p>
        ) : null}
        {loadError ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={`계정 목록을 불러오지 못했습니다 · ${loadError}`} />
          </p>
        ) : null}
        {accounts !== null && accounts.length === 0 ? <p className="text-sm text-mn-muted">총괄로 지정할 수 있는 관리자 이상 계정이 없습니다.</p> : null}
        {accounts !== null && accounts.length > 0 ? (
          <div className="flex flex-col gap-1">
            <label htmlFor={personId} className="text-sm font-medium">
              새 총괄
            </label>
            <select id={personId} value={newLeadId} disabled={saving} onChange={(e) => setNewLeadId(e.target.value)} className={fieldClass}>
              <option value="">선택하세요</option>
              {accounts.map((a) => (
                <option key={a.accountId} value={a.accountId}>
                  {a.name} ({RANK_LABEL[a.rank] ?? a.rank})
                </option>
              ))}
            </select>
          </div>
        ) : null}
        <div className="flex flex-col gap-1">
          <div className="flex items-center justify-between">
            <label htmlFor={reasonId} className="text-sm font-medium">
              변경 사유
            </label>
            <span className="font-mn-mono text-[13px] text-mn-muted">
              {trimmed.length}/{REASON_MAX}자
            </span>
          </div>
          <textarea
            id={reasonId}
            value={reason}
            rows={3}
            disabled={saving}
            onChange={(e) => {
              setError(null);
              setReason(e.target.value);
            }}
            className="mn-focus w-full rounded-mn-control border border-mn-control bg-mn-bg px-3 py-2 text-sm text-mn-text outline-none disabled:opacity-50"
          />
        </div>
        {error ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={`총괄을 바꾸지 못했습니다 · ${error}`} />
          </p>
        ) : null}
      </div>
    </Modal>
  );
}
