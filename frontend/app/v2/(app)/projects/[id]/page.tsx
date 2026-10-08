"use client";

/*
 * v2 프로젝트 상세(작업 69-1)
 * - GET /api/projects/{id}: 헤더(이름·상태 칩·등록 부서·총괄·등록자·설명), 참여자 표, 연결된 회의록 목록. 볼 수 없으면(404) 안내와 목록 링크.
 * - 버튼은 서버가 준 allowedActions 로만 보인다: approve_project/reject_project(승인 대기), manage_members(참여자 추가·제거), change_lead(총괄 변경).
 *   직급·역할로 화면에서 추정하지 않는다. 서버 거부(403·409·422)는 서버 문구를 그대로 보인다. 모든 변경 응답이 상세이므로 그 값으로 화면을 바꾼다.
 * - 총괄(lead) 행에는 제거 버튼이 없다(총괄은 총괄 변경으로만 바꾼다).
 * - 연결된 회의록: GET /api/projects/{id}/meetings (기본 진행중 단계, 쪽 나눔은 회의록 목록과 같은 방식).
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { Button, StatusDot } from "@/components/mono";
import { ProjectStatusChip } from "@/components/v2/ProjectStatusChip";
import { AddMemberDialog, ChangeLeadDialog } from "@/components/v2/ProjectDialogs";
import { ReasonDialog, type ReasonAction } from "@/components/v2/ReasonDialog";
import { detailPath, formatDateTime, meetingStatus, phaseText } from "@/components/v2/meeting-display";
import { can, PROJECT_ACTION } from "@/lib/v2/actions";
import { ApiError, isAbortError } from "@/lib/v2/errors";
import type { MeetingListPage } from "@/lib/v2/meetings";
import {
  approveProject, getProject, listProjectMeetings, PROJECT_PAGE_SIZE, rejectProject, removeProjectMember, ROLE_LABEL,
  type ProjectDetail,
} from "@/lib/v2/projects";
import { RANK_LABEL } from "@/lib/v2/types";

type LoadState =
  | { kind: "loading" }
  | { kind: "not_found" }
  | { kind: "error"; message: string }
  | { kind: "ready"; project: ProjectDetail };

const errorMessage = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium text-mn-muted">{label}</dt>
      <dd className="mt-1 text-base font-semibold leading-7 text-mn-text [overflow-wrap:anywhere]">{children}</dd>
    </div>
  );
}

/** 프로젝트에 연결된 회의록 목록(쪽 나눔 포함) */
function ProjectMeetings({ projectId }: { projectId: number }) {
  const [page, setPage] = useState(1);
  const [state, setState] = useState<{ kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; data: MeetingListPage }>({ kind: "loading" });
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ kind: "loading" });
    try {
      setState({ kind: "ready", data: await listProjectMeetings(projectId, page, controller.signal) });
    } catch (e) {
      if (isAbortError(e)) return;
      setState({ kind: "error", message: errorMessage(e, "회의록을 불러오지 못했습니다.") });
    }
  }, [projectId, page]);

  useEffect(() => {
    void load();
    return () => controllerRef.current?.abort();
  }, [load]);

  const data = state.kind === "ready" ? state.data : null;
  const lastPage = data ? Math.max(1, Math.ceil(data.total / (data.size || PROJECT_PAGE_SIZE))) : 1;

  return (
    <section aria-label="프로젝트 회의록" className="flex flex-col gap-3">
      <h2 className="text-base font-semibold tracking-tight">프로젝트 회의록</h2>
      {state.kind === "loading" ? (
        <p role="status" className="text-sm text-mn-muted">
          회의록을 불러오는 중…
        </p>
      ) : state.kind === "error" ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-mn-card border border-mn-border bg-mn-surface p-4">
          <span className="text-sm">
            <StatusDot tone="error" label={`회의록을 불러오지 못했습니다 · ${state.message}`} />
          </span>
          <Button size="sm" onClick={() => void load()}>
            다시 시도
          </Button>
        </div>
      ) : state.data.total === 0 ? (
        <p role="status" className="rounded-mn-card border border-mn-border bg-mn-surface p-4 text-sm text-mn-muted">
          연결된 회의록이 없습니다
        </p>
      ) : (
        <>
          <div className="min-w-0 overflow-x-auto rounded-mn-card border border-mn-border bg-mn-surface">
            <table aria-label="프로젝트 회의록 목록" className="w-full min-w-[36rem] table-fixed border-collapse text-sm">
              <colgroup>
                <col />
                <col className="w-[10rem]" />
                <col className="w-[7rem]" />
                <col className="w-[8rem]" />
              </colgroup>
              <thead>
                <tr className="h-10 border-b border-mn-border text-left text-xs text-mn-muted">
                  <th scope="col" className="px-3 font-medium">회의명</th>
                  <th scope="col" className="px-3 font-medium">회의 일시</th>
                  <th scope="col" className="px-3 font-medium">단계</th>
                  <th scope="col" className="px-3 font-medium">상태</th>
                </tr>
              </thead>
              <tbody>
                {state.data.items.map((meeting) => {
                  const status = meetingStatus(meeting);
                  return (
                    <tr key={meeting.id} className="min-h-12 border-b border-mn-border last:border-b-0">
                      <td className="px-3 py-3 [overflow-wrap:anywhere]">
                        <Link href={detailPath(meeting.id)} className="mn-focus rounded-mn-control font-medium text-mn-text hover:underline">
                          {meeting.title || "(제목 없음)"}
                        </Link>
                      </td>
                      <td className="px-3 py-3">
                        <span className="whitespace-nowrap font-mn-mono text-[13px]">{formatDateTime(meeting.heldAt)}</span>
                      </td>
                      <td className="px-3 py-3">{phaseText(meeting)}</td>
                      <td className="px-3 py-3">
                        <StatusDot tone={status.tone} label={status.label} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-3 text-sm text-mn-muted">
            <span aria-live="polite" className="font-mn-mono text-[13px]">
              {state.data.page} / {lastPage} 쪽
            </span>
            <Button size="sm" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))} className="max-md:min-h-11 max-md:min-w-11">
              이전
            </Button>
            <Button size="sm" disabled={page >= lastPage} onClick={() => setPage((p) => p + 1)} className="max-md:min-h-11 max-md:min-w-11">
              다음
            </Button>
          </div>
        </>
      )}
    </section>
  );
}

export default function V2ProjectDetailPage() {
  const params = useParams<{ id: string }>();
  const rawId = params?.id ?? "";
  const projectId = /^\d+$/.test(rawId) ? Number(rawId) : null;

  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [reasonAction, setReasonAction] = useState<ReasonAction | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [leadOpen, setLeadOpen] = useState(false);
  const [notice, setNotice] = useState<{ tone: "ready" | "error"; text: string } | null>(null);
  const [approving, setApproving] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    if (projectId === null) {
      setState({ kind: "not_found" });
      return;
    }
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ kind: "loading" });
    try {
      setState({ kind: "ready", project: await getProject(projectId, controller.signal) });
    } catch (e) {
      if (isAbortError(e)) return;
      if (e instanceof ApiError && e.status === 404) setState({ kind: "not_found" });
      else setState({ kind: "error", message: errorMessage(e, "프로젝트를 불러오지 못했습니다.") });
    }
  }, [projectId]);

  useEffect(() => {
    void load();
    return () => controllerRef.current?.abort();
  }, [load]);

  const apply = (project: ProjectDetail, text: string) => {
    setState({ kind: "ready", project });
    setNotice({ tone: "ready", text });
  };

  if (state.kind === "loading") {
    return (
      <p role="status" className="text-sm text-mn-muted">
        프로젝트를 불러오는 중…
      </p>
    );
  }
  if (state.kind === "not_found") {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="mn-page-title text-mn-text">프로젝트를 찾을 수 없습니다</h1>
        <p role="status" className="text-sm text-mn-muted">
          없는 프로젝트이거나 볼 수 있는 권한이 없습니다.
        </p>
        <Link href="/v2/projects" className="mn-focus self-start rounded-mn-control text-sm text-mn-muted hover:text-mn-text">
          ← 프로젝트 목록으로
        </Link>
      </div>
    );
  }
  if (state.kind === "error") {
    return (
      <div role="alert" className="flex flex-wrap items-center justify-between gap-4 rounded-mn-card border border-mn-border bg-mn-surface p-6">
        <span className="text-sm">
          <StatusDot tone="error" label={`프로젝트를 불러오지 못했습니다 · ${state.message}`} />
        </span>
        <Button size="sm" onClick={() => void load()}>
          다시 시도
        </Button>
      </div>
    );
  }

  const project = state.project;
  const actions = project.allowedActions;
  const canApprove = can(actions, PROJECT_ACTION.approveProject);
  const canReject = can(actions, PROJECT_ACTION.rejectProject);
  const canManage = can(actions, PROJECT_ACTION.manageMembers);
  const canChangeLead = can(actions, PROJECT_ACTION.changeLead);

  async function onApprove() {
    if (approving) return;
    setApproving(true);
    setNotice(null);
    try {
      apply(await approveProject(project.id), "프로젝트를 승인했습니다");
    } catch (e) {
      setNotice({ tone: "error", text: `승인하지 못했습니다 · ${errorMessage(e, "서버 오류")}` });
    } finally {
      setApproving(false);
    }
  }

  function openReject() {
    setNotice(null);
    setReasonAction({
      title: "프로젝트 반려",
      target: project.name,
      confirmLabel: "반려하기",
      requireReason: true,
      notice: "반려한 프로젝트는 다시 승인할 수 없습니다. 사유는 이력에 남습니다.",
      run: async (reason, signal) => {
        apply(await rejectProject(project.id, reason, signal), "프로젝트를 반려했습니다");
      },
    });
  }

  function openRemove(accountId: number, name: string) {
    setNotice(null);
    setReasonAction({
      title: "참여자 제거",
      target: name,
      confirmLabel: "참여자 제거",
      requireReason: false,
      notice: "이 사람은 더 이상 이 프로젝트의 회의록을 볼 수 없습니다(다른 이유로 볼 수 있는 경우 제외).",
      run: async (_reason, signal) => {
        apply(await removeProjectMember(project.id, accountId, signal), `${name} 님을 참여자에서 제거했습니다`);
      },
    });
  }

  return (
    <div className="flex flex-col gap-6 leading-[1.6]">
      <Link href="/v2/projects" className="mn-focus self-start rounded-mn-control text-sm text-mn-muted hover:text-mn-text">
        ← 프로젝트 목록으로
      </Link>

      <header className="flex flex-col gap-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 flex-col gap-2">
            <h1 title={project.name} className="mn-page-title text-mn-text">{project.name}</h1>
            <span>
              <ProjectStatusChip status={project.status} />
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {canApprove ? (
              <Button size="sm" variant="primary" disabled={approving} onClick={() => void onApprove()}>
                승인
              </Button>
            ) : null}
            {canReject ? (
              <Button size="sm" onClick={openReject}>
                반려
              </Button>
            ) : null}
            {canChangeLead ? (
              <Button
                size="sm"
                onClick={() => {
                  setNotice(null);
                  setLeadOpen(true);
                }}
              >
                총괄 변경
              </Button>
            ) : null}
          </div>
        </div>

        {notice ? (
          <p role={notice.tone === "error" ? "alert" : "status"} className="text-sm">
            <StatusDot tone={notice.tone} label={notice.text} />
          </p>
        ) : null}

        {project.status === "pending_approval" && !canApprove && !canReject ? (
          <p role="status" className="text-sm text-mn-muted">
            승인 대기 중, 승인자: {project.approver?.name ?? "부서장"}
          </p>
        ) : null}

        <section aria-label="프로젝트 개요" className="rounded-mn-card border border-mn-border bg-mn-surface p-5">
          <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="등록 부서">{project.departmentName}</Field>
            <Field label="총괄">{project.lead?.name ?? <span className="font-normal text-mn-muted">아직 없음</span>}</Field>
            <Field label="등록자">{project.registeredBy.name}</Field>
          </dl>
          <div className="mt-4 border-t border-mn-border pt-4">
            <h2 className="text-xs font-medium text-mn-muted">설명</h2>
            <p className="mt-1 whitespace-pre-line text-sm leading-6 [overflow-wrap:anywhere]">
              {project.description.trim() ? project.description : <span className="text-mn-muted">설명이 없습니다.</span>}
            </p>
          </div>
        </section>
      </header>

      <section aria-label="참여자" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-base font-semibold tracking-tight">
            참여자 <span className="font-mn-mono text-[13px] font-normal text-mn-muted">({project.members.length})</span>
          </h2>
          {canManage ? (
            <Button
              size="sm"
              onClick={() => {
                setNotice(null);
                setAddOpen(true);
              }}
            >
              참여자 추가
            </Button>
          ) : null}
        </div>
        <div className="min-w-0 overflow-x-auto rounded-mn-card border border-mn-border bg-mn-surface">
          <table aria-label="참여자 목록" className="w-full min-w-[26rem] table-fixed border-collapse text-sm">
            <colgroup>
              <col />
              <col className="w-[7rem]" />
              <col className="w-[6rem]" />
              {canManage ? <col className="w-[6.5rem]" /> : null}
            </colgroup>
            <thead>
              <tr className="h-10 border-b border-mn-border text-left text-xs text-mn-muted">
                <th scope="col" className="px-3 font-medium">이름</th>
                <th scope="col" className="px-3 font-medium">사용권한</th>
                <th scope="col" className="px-3 font-medium">역할</th>
                {canManage ? <th scope="col" className="px-3 font-medium">동작</th> : null}
              </tr>
            </thead>
            <tbody>
              {project.members.length === 0 ? (
                <tr className="h-12">
                  <td colSpan={canManage ? 4 : 3} className="px-3 text-center text-mn-muted">
                    참여자가 없습니다.
                  </td>
                </tr>
              ) : (
                project.members.map((member) => (
                  <tr key={member.accountId} className="min-h-12 border-b border-mn-border last:border-b-0">
                    <td className="px-3 py-3 font-medium [overflow-wrap:anywhere]">{member.name}</td>
                    <td className="px-3 py-3">{RANK_LABEL[member.rank] ?? member.rank}</td>
                    <td className="px-3 py-3">{ROLE_LABEL[member.role] ?? member.role}</td>
                    {canManage ? (
                      <td className="px-3 py-2">
                        {member.role !== "lead" ? (
                          <Button size="sm" aria-label={`${member.name} 참여자 제거`} onClick={() => openRemove(member.accountId, member.name)}>
                            제거
                          </Button>
                        ) : null}
                      </td>
                    ) : null}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <ProjectMeetings projectId={project.id} />

      <ReasonDialog
        action={reasonAction}
        onClose={() => setReasonAction(null)}
        onDone={async () => {
          /* 변경 응답으로 이미 화면을 바꿨다 */
        }}
      />
      <AddMemberDialog
        project={project}
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onSaved={(saved) => {
          setAddOpen(false);
          apply(saved, "참여자를 추가했습니다");
        }}
      />
      <ChangeLeadDialog
        project={project}
        open={leadOpen}
        onClose={() => setLeadOpen(false)}
        onSaved={(saved) => {
          setLeadOpen(false);
          apply(saved, `총괄을 ${saved.lead?.name ?? ""}(으)로 바꿨습니다. 이전 총괄은 관리자 역할로 남습니다`);
        }}
      />
    </div>
  );
}
