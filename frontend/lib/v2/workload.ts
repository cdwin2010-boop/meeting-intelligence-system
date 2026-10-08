/*
 * v2 업무 처리 현황 API(작업 69-3): GET /api/workload?departmentId=
 * 응답 모양은 backend/app/api/workload.py 의 WorkloadOut(camelCase)과 맞춘다. 서버는 하루 한 번 집계한 저장값만 돌려준다.
 * 화면의 범위(전사·부서·개인) 판단은 응답의 scope 로만 한다.
 */
import { request } from "./http";

export interface DepartmentRef {
  id: number;
  name: string;
}

/** company=전사, department=부서, self=본인 개인 현황 */
export type WorkloadScopeKind = "company" | "department" | "self";

export interface WorkloadScope {
  kind: WorkloadScopeKind;
  /** 선택 가능한 부서(개인 범위는 빈 목록) */
  departments: DepartmentRef[];
  department: DepartmentRef | null;
}

export interface WorkloadKpis {
  assigned: number;
  /** 0~100. 분모가 0이면 null */
  completionRate: number | null;
  overdue: number;
  avgOpenPerPerson: number;
  memberCount: number;
}

export interface WorkloadMember {
  accountId: number;
  name: string;
  completed: number;
  inProgress: number;
  overdue: number;
  /** 진행 중 + 지연 */
  open: number;
  overloaded: boolean;
}

export interface UrgentItem {
  itemId: number;
  title: string;
  /** YYYY-MM-DD */
  dueDate: string;
  kind: "overdue" | "due_soon";
  /** overdue: 기한 후 지난 일수, due_soon: 기한까지 남은 일수(집계 기준일 기준) */
  days: number;
  meetingId: number;
  meetingTitle: string;
  assignee: DepartmentRef;
}

export interface Workload {
  /** 집계 기준일(YYYY-MM-DD). 아직 집계가 없으면 null */
  snapshotDate: string | null;
  generatedAt: string | null;
  scope: WorkloadScope;
  kpis: WorkloadKpis;
  members: WorkloadMember[];
  urgentItems: UrgentItem[];
}

export function getWorkload(departmentId: number | null, signal?: AbortSignal): Promise<Workload> {
  const query = departmentId === null ? "" : `?departmentId=${encodeURIComponent(String(departmentId))}`;
  return request<Workload>(`/workload${query}`, { signal });
}

/** "2026-10-06" → "26년 10월 06일" (문자열 그대로 자르므로 시간대에 흔들리지 않는다) */
export function formatSnapshotDate(isoDate: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
  return match ? `${match[1].slice(2)}년 ${match[2]}월 ${match[3]}일` : isoDate;
}
