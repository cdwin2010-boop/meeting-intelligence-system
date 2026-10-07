/*
 * E2E 가짜 서버용 프로젝트 허용 동작(allowedActions) 계산기. 실제 서버(backend app/auth/actions.py 의 project_allowed_actions)와 같은 표를 따른다.
 * - change_lead: 진행 중이고 총괄 본인(lead 계정) 또는 지시자
 * - manage_members: 진행 중이고 lead 역할 참여자
 * - approve_project / reject_project: 승인 대기이고 등록 부서의 부서장(승인자)
 */
interface Member { accountId: number; role: string }
interface ProjectLike { status: string; lead?: { id: number } | null; members: Member[] }

export function projectActions(project: ProjectLike, account: { id: number; rank: string }, approverId: number | null = null): string[] {
  const out: string[] = [];
  if (project.status === "active" && (account.rank === "executive" || project.lead?.id === account.id)) out.push("change_lead");
  if (project.status === "active" && project.members.some((m) => m.accountId === account.id && m.role === "lead")) out.push("manage_members");
  if (project.status === "pending_approval" && approverId === account.id) out.push("approve_project", "reject_project");
  return out;
}

/** 상세 응답에 허용 동작을 붙인 복사본 */
export function withProjectActions<T extends ProjectLike>(project: T, account: { id: number; rank: string }, approverId: number | null = null): T & { allowedActions: string[] } {
  return { ...project, allowedActions: projectActions(project, account, approverId) };
}
