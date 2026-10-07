/*
 * v2 프로젝트 API(작업 69-1). 응답 모양은 backend/app/api/projects.py·departments.py(camelCase)와 맞춘다.
 * 버튼 노출은 서버가 상세 응답에 주는 allowedActions 로만 정한다. 서버 거부(403·409·422 등)는 ApiError 메시지를 그대로 화면에 보인다.
 */
import { request } from "./http";
import type { AccountRef, MeetingListPage } from "./meetings";
import type { Rank } from "./types";

export type ProjectStatus = "pending_approval" | "active" | "rejected";
/** 프로젝트 참여 역할: lead=총괄, manager=관리자, member=참여자 */
export type ProjectRole = "lead" | "manager" | "member";

export interface ProjectListItem {
  id: number;
  name: string;
  description: string;
  status: ProjectStatus;
  departmentId: number;
  departmentName: string;
  registeredBy: AccountRef;
  lead: AccountRef | null;
  /** 승인 대기 중이면 등록 부서의 부서장, 승인·반려된 뒤에는 처리한 사람, 부서장이 직접 등록했으면 null */
  approver: AccountRef | null;
  myRole: ProjectRole | null;
  memberCount: number;
  createdAt: string;
  decidedAt: string | null;
}

export interface ProjectMember {
  accountId: number;
  name: string;
  rank: Rank;
  role: ProjectRole;
}

export interface ProjectDetail extends ProjectListItem {
  members: ProjectMember[];
  /** 서버가 주는 이 사용자의 허용 동작(change_lead·manage_members·approve_project·reject_project) */
  allowedActions?: string[] | null;
}

/** GET /api/me/org 의 한 항목: 내 소속 부서와 부서 안 내 역할(head=부서장, member=부서원) */
export interface OrgMembership {
  departmentId: number;
  name: string;
  kind: "normal" | "executive";
  role: "head" | "member";
}

export interface CandidateMember {
  accountId: number;
  name: string;
  rank: Rank;
}

export interface CandidateGroup {
  departmentId: number;
  departmentName: string;
  members: CandidateMember[];
}

export interface Candidates {
  ownDepartment: CandidateGroup;
  otherDepartments: CandidateGroup[];
  executiveGroup: CandidateGroup[];
}

export const PROJECT_NAME_MAX = 120;
export const PROJECT_DESCRIPTION_MAX = 2000;
export const PROJECT_PAGE_SIZE = 20;

const path = (id: number, tail = "") => `/projects/${encodeURIComponent(String(id))}${tail}`;

export const listProjects = (signal?: AbortSignal) => request<ProjectListItem[]>("/projects", { signal });
export const getProject = (id: number, signal?: AbortSignal) => request<ProjectDetail>(path(id), { signal });
export const getMyOrg = (signal?: AbortSignal) => request<OrgMembership[]>("/me/org", { signal });
export const getCandidates = (departmentId: number, signal?: AbortSignal) =>
  request<Candidates>(`/projects/candidates?departmentId=${encodeURIComponent(String(departmentId))}`, { signal });

export interface ProjectInput {
  name: string;
  description: string;
  departmentId: number;
  memberIds: number[];
}
export const createProject = (input: ProjectInput, signal?: AbortSignal) => request<ProjectDetail>("/projects", { method: "POST", body: input, signal });
export const approveProject = (id: number, signal?: AbortSignal) => request<ProjectDetail>(path(id, "/approve"), { method: "POST", signal });
export const rejectProject = (id: number, reason: string, signal?: AbortSignal) =>
  request<ProjectDetail>(path(id, "/reject"), { method: "POST", body: { reason }, signal });
export const addProjectMember = (id: number, accountId: number, role: ProjectRole, signal?: AbortSignal) =>
  request<ProjectDetail>(path(id, "/members"), { method: "POST", body: { accountId, role }, signal });
/** 참여자 제거는 DELETE 대신 POST(서버 CORS 가 DELETE 를 허용하지 않는다) */
export const removeProjectMember = (id: number, accountId: number, signal?: AbortSignal) =>
  request<ProjectDetail>(path(id, `/members/${encodeURIComponent(String(accountId))}/remove`), { method: "POST", signal });
export const changeProjectLead = (id: number, newLeadId: number, reason: string, signal?: AbortSignal) =>
  request<ProjectDetail>(path(id, "/change-lead"), { method: "POST", body: { newLeadId, reason }, signal });
export const listProjectMeetings = (id: number, page: number, signal?: AbortSignal) =>
  request<MeetingListPage>(`${path(id, "/meetings")}?${new URLSearchParams({ page: String(page), size: String(PROJECT_PAGE_SIZE) }).toString()}`, { signal });

export const STATUS_LABEL: Record<ProjectStatus, string> = { pending_approval: "승인 대기", active: "진행 중", rejected: "반려" };
export const ROLE_LABEL: Record<ProjectRole, string> = { lead: "총괄", manager: "관리자", member: "참여자" };
