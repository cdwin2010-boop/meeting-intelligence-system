"use client";

/*
 * v2 프로젝트 등록(작업 69-1)
 * - 등록 부서: GET /api/me/org 의 내 소속 부서만. 소속이 없으면 등록을 막고 안내한다.
 * - 참여자 후보: GET /api/projects/candidates?departmentId= 를 "우리 부서 / 다른 부서(부서별) / 임원 그룹" 그룹의 체크 목록으로 보여 준다(선택은 선택 사항).
 *   부서를 바꾸면 후보를 다시 불러온다. 등록자 본인은 서버가 자동으로 참여자로 넣으므로 목록에서 뺀다.
 * - 안내: 선택한 부서에서 내 역할이 부서장이면 바로 진행 중·본인이 총괄, 부서원이면 부서장 승인 뒤 진행.
 * - 제출 POST /api/projects(name, description, departmentId, memberIds). 성공하면 상세로 이동, 서버 거부(403·409·422)는 폼 위 알림(role="alert").
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";

import { Button, StatusDot } from "@/components/mono";
import { useAuth } from "@/components/v2/AuthProvider";
import { isAbortError } from "@/lib/v2/errors";
import {
  createProject, getCandidates, getMyOrg, PROJECT_DESCRIPTION_MAX, PROJECT_NAME_MAX,
  type CandidateGroup, type Candidates, type OrgMembership,
} from "@/lib/v2/projects";
import { RANK_LABEL } from "@/lib/v2/types";

const inputClass =
  "mn-focus w-full rounded-mn-control border border-mn-control bg-mn-bg px-3 text-sm text-mn-text outline-none disabled:opacity-50";
const errorMessage = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

type OrgState = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; departments: OrgMembership[] };
type CandidateState = { kind: "idle" } | { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; data: Candidates };

function MemberGroup({
  title, group, selected, onToggle, disabled, myId,
}: {
  title: string;
  group: CandidateGroup;
  selected: number[];
  onToggle: (id: number, checked: boolean) => void;
  disabled: boolean;
  myId: number | null;
}) {
  const members = group.members.filter((m) => m.accountId !== myId);
  return (
    <fieldset className="flex min-w-0 flex-col gap-2">
      <legend className="text-sm font-medium">{title}</legend>
      {members.length === 0 ? (
        <p className="text-sm text-mn-muted">선택할 수 있는 사람이 없습니다.</p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {members.map((member) => {
            const checked = selected.includes(member.accountId);
            return (
              <li key={member.accountId}>
                <label
                  className={[
                    "inline-flex min-h-8 cursor-pointer items-center gap-2 rounded-mn-control border px-3 text-[13px]",
                    "has-[:focus-visible]:[box-shadow:var(--mn-focus-ring)] has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-50",
                    checked ? "border-mn-accent bg-mn-elevated text-mn-text" : "border-mn-control text-mn-muted hover:text-mn-text",
                  ].join(" ")}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={disabled}
                    onChange={(event) => onToggle(member.accountId, event.target.checked)}
                    className="size-4"
                  />
                  {member.name}
                  <span className="text-xs">{RANK_LABEL[member.rank] ?? member.rank}</span>
                </label>
              </li>
            );
          })}
        </ul>
      )}
    </fieldset>
  );
}

export default function V2ProjectNewPage() {
  const router = useRouter();
  const { account } = useAuth();
  const myId = account?.id ?? null;
  const nameId = useId();
  const descId = useId();
  const deptId = useId();
  const [org, setOrg] = useState<OrgState>({ kind: "loading" });
  const [departmentId, setDepartmentId] = useState<number | null>(null);
  const [candidates, setCandidates] = useState<CandidateState>({ kind: "idle" });
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [memberIds, setMemberIds] = useState<number[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submitRef = useRef(false);

  // 내 소속 부서
  useEffect(() => {
    const controller = new AbortController();
    getMyOrg(controller.signal)
      .then((departments) => {
        setOrg({ kind: "ready", departments });
        setDepartmentId(departments[0]?.departmentId ?? null);
      })
      .catch((e) => {
        if (!isAbortError(e)) setOrg({ kind: "error", message: errorMessage(e, "소속 부서를 불러오지 못했습니다.") });
      });
    return () => controller.abort();
  }, []);

  // 부서를 바꾸면 후보를 다시 불러오고 선택을 비운다
  useEffect(() => {
    setMemberIds([]);
    if (departmentId === null) {
      setCandidates({ kind: "idle" });
      return;
    }
    const controller = new AbortController();
    setCandidates({ kind: "loading" });
    getCandidates(departmentId, controller.signal)
      .then((data) => setCandidates({ kind: "ready", data }))
      .catch((e) => {
        if (!isAbortError(e)) setCandidates({ kind: "error", message: errorMessage(e, "참여자 후보를 불러오지 못했습니다.") });
      });
    return () => controller.abort();
  }, [departmentId]);

  const departments = org.kind === "ready" ? org.departments : [];
  const myDepartment = departments.find((d) => d.departmentId === departmentId) ?? null;
  const trimmed = name.trim();
  const nameProblem = name.length > 0 && trimmed === "" ? "프로젝트 이름은 공백만으로 쓸 수 없습니다." : trimmed.length > PROJECT_NAME_MAX ? `프로젝트 이름은 ${PROJECT_NAME_MAX}자 이하여야 합니다.` : null;
  const descriptionProblem = description.length > PROJECT_DESCRIPTION_MAX ? `설명은 ${PROJECT_DESCRIPTION_MAX}자 이하여야 합니다.` : null;
  const canSubmit = !submitting && myDepartment !== null && trimmed !== "" && !nameProblem && !descriptionProblem;

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit || submitRef.current || departmentId === null) return;
    submitRef.current = true; // 제출 중 중복 클릭 방지
    setSubmitting(true);
    setError(null);
    try {
      const created = await createProject({ name: trimmed, description: description.trim(), departmentId, memberIds });
      router.push(`/v2/projects/${created.id}`);
    } catch (e) {
      setError(errorMessage(e, "서버 오류"));
      setSubmitting(false);
      submitRef.current = false;
    }
  }

  const toggle = (id: number, checked: boolean) => setMemberIds((prev) => (checked ? [...prev, id] : prev.filter((v) => v !== id)));

  return (
    <div className="flex flex-col gap-6 leading-[1.6]">
      <header>
        <Link href="/v2/projects" className="mn-focus rounded-mn-control text-sm text-mn-muted hover:text-mn-text">
          ← 프로젝트 목록으로
        </Link>
        <h1 title="프로젝트 등록" className="mn-page-title mt-2 text-mn-text">프로젝트 등록</h1>
      </header>

      {org.kind === "loading" ? (
        <p role="status" className="text-sm text-mn-muted">
          소속 부서를 불러오는 중…
        </p>
      ) : org.kind === "error" ? (
        <div role="alert" className="rounded-mn-card border border-mn-border bg-mn-surface p-6 text-sm">
          <StatusDot tone="error" label={`소속 부서를 불러오지 못했습니다 · ${org.message}`} />
        </div>
      ) : departments.length === 0 ? (
        <p role="status" className="rounded-mn-card border border-mn-border bg-mn-surface p-6 text-sm">
          소속 부서가 없어 프로젝트를 등록할 수 없습니다. 관리자에게 문의하세요
        </p>
      ) : (
        <form onSubmit={onSubmit} className="flex flex-col gap-5 rounded-mn-card border border-mn-border bg-mn-surface p-6" noValidate>
          {error ? (
            <div role="alert" className="text-sm">
              <StatusDot tone="error" label={`등록하지 못했습니다 · ${error}`} />
            </div>
          ) : null}

          <div className="flex flex-col gap-2">
            <label htmlFor={nameId} className="text-sm font-medium">
              프로젝트 이름
            </label>
            <input id={nameId} value={name} disabled={submitting} onChange={(e) => setName(e.target.value)} aria-invalid={nameProblem !== null} className={`${inputClass} h-10`} />
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-mn-muted">
              <span>{nameProblem ? <StatusDot tone="error" label={nameProblem} /> : "필수, 최대 120자"}</span>
              <span className="font-mn-mono">{trimmed.length}/{PROJECT_NAME_MAX}</span>
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <label htmlFor={descId} className="text-sm font-medium">
              설명 <span className="font-normal text-mn-muted">(선택)</span>
            </label>
            <textarea id={descId} value={description} rows={4} disabled={submitting} onChange={(e) => setDescription(e.target.value)} className={`${inputClass} py-2`} />
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-mn-muted">
              <span>{descriptionProblem ? <StatusDot tone="error" label={descriptionProblem} /> : "최대 2000자"}</span>
              <span className="font-mn-mono">{description.length}/{PROJECT_DESCRIPTION_MAX}</span>
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <label htmlFor={deptId} className="text-sm font-medium">
              등록 부서
            </label>
            <select
              id={deptId}
              value={departmentId ?? ""}
              disabled={submitting}
              onChange={(e) => setDepartmentId(Number(e.target.value))}
              className={`${inputClass} h-10`}
            >
              {departments.map((d) => (
                <option key={d.departmentId} value={d.departmentId}>
                  {d.name}
                </option>
              ))}
            </select>
            <p role="status" className="text-sm text-mn-muted">
              {myDepartment?.role === "head" ? "등록하면 바로 진행 중이 되고 본인이 총괄이 됩니다" : "부서장의 승인 후 진행됩니다"}
            </p>
          </div>

          <fieldset className="flex flex-col gap-4" disabled={submitting}>
            <legend className="mb-1 text-sm font-medium">
              참여자 선택 <span className="font-normal text-mn-muted">(선택 사항, 등록자는 자동으로 참여합니다)</span>
            </legend>
            {candidates.kind === "loading" ? (
              <p role="status" className="text-sm text-mn-muted">
                참여자 후보를 불러오는 중…
              </p>
            ) : candidates.kind === "error" ? (
              <p role="alert" className="text-sm">
                <StatusDot tone="error" label={`참여자 후보를 불러오지 못했습니다 · ${candidates.message}`} />
              </p>
            ) : candidates.kind === "ready" ? (
              <>
                <MemberGroup title="우리 부서" group={candidates.data.ownDepartment} selected={memberIds} onToggle={toggle} disabled={submitting} myId={myId} />
                {candidates.data.otherDepartments.map((group) => (
                  <MemberGroup key={group.departmentId} title={`다른 부서 · ${group.departmentName}`} group={group} selected={memberIds} onToggle={toggle} disabled={submitting} myId={myId} />
                ))}
                {candidates.data.executiveGroup.map((group) => (
                  <MemberGroup key={group.departmentId} title={`임원 그룹 · ${group.departmentName}`} group={group} selected={memberIds} onToggle={toggle} disabled={submitting} myId={myId} />
                ))}
              </>
            ) : null}
          </fieldset>

          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" variant="primary" disabled={!canSubmit}>
              {submitting ? "등록 중…" : "등록하기"}
            </Button>
            <Link href="/v2/projects" className="mn-focus inline-flex h-10 items-center rounded-mn-control border border-mn-control px-4 text-sm font-medium hover:bg-mn-elevated">
              취소
            </Link>
          </div>
        </form>
      )}
    </div>
  );
}
