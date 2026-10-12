/*
 * v2 시스템 관리자 API(작업 82-2). 응답 모양은 backend/app/api/admin.py(camelCase)와 맞춘다. 이미 있는 /api/admin/* 만 쓴다.
 * 삭제 API 는 없다(계정은 비활성화, 부서는 삭제하지 않음). 서버 거부(403·404·409·422)는 ApiError 메시지를 그대로 화면에 보인다.
 */
import { request } from "./http";
import type { Rank } from "./types";

export interface AdminDepartmentBrief {
  id: number;
  name: string;
  role: "head" | "member";
}

export interface AdminAccount {
  id: number;
  loginId: string;
  name: string;
  email: string;
  rank: Rank;
  isActive: boolean;
  isSystemAdmin: boolean;
  departments: AdminDepartmentBrief[];
  createdAt: string;
}

export interface AdminAccountPage {
  items: AdminAccount[];
  total: number;
  page: number;
  size: number;
}

export interface AdminMember {
  accountId: number;
  name: string;
  rank: Rank;
  role: "head" | "member";
  isActive: boolean;
}

export interface AdminDepartment {
  id: number;
  name: string;
  parentId: number | null;
  kind: "normal" | "executive";
  head: AdminMember | null;
  members: AdminMember[];
}

export type SettingKey = "external_llm_enabled" | "local_llm_external_host_allowed";
/** default=기본값, env=.env 에 명시된 값, admin=시스템 관리자가 정한 값 */
export type SettingSource = "default" | "env" | "admin";

export interface AdminSetting {
  key: SettingKey;
  value: boolean;
  source: SettingSource;
  updatedAt: string | null;
  updatedBy: { id: number; name: string } | null;
}

export type KnowledgeSettingKey = "knowledge_integration_enabled" | "knowledge_api_endpoint";

/** 지식(KMS) 연동 설정(작업 83-1·84-1): 스위치는 bool, 주소는 문자열("" = 설정 안 됨) */
export interface KnowledgeSetting {
  key: KnowledgeSettingKey;
  value: boolean | string;
  source: SettingSource;
  updatedAt: string | null;
  updatedBy: { id: number; name: string } | null;
}

export interface AdminSettings {
  /** EXTERNAL_POLICY_ENFORCED. false 면 이 설정은 아직 처리에 적용되지 않는다 */
  enforced: boolean;
  items: AdminSetting[];
  /** 지식 연동 설정(추가 칸). 필드가 없는 서버 응답이면 없다 */
  knowledge?: KnowledgeSetting[];
}

export interface LicenseInfo {
  status: string;
  licensee: string | null;
  maxAccounts: number | null;
  activeAccounts: number;
  overLimit: boolean;
  expiresAt: string | null;
  daysLeft: number | null;
  hardwareMatch: boolean | null;
  message: string;
  warnings: string[];
}

export const PASSWORD_MIN_HINT = 10;
export const REASON_LIMIT = 500;
export const ACCOUNT_PAGE_SIZE = 20;

const seg = (value: number | string) => encodeURIComponent(String(value));

// ---- 계정 ----
export const listAccounts = (query: { q?: string; page?: number; size?: number }, signal?: AbortSignal) => {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  params.set("page", String(query.page ?? 1));
  params.set("size", String(query.size ?? ACCOUNT_PAGE_SIZE));
  return request<AdminAccountPage>(`/admin/accounts?${params.toString()}`, { signal });
};

export interface AccountCreateInput {
  loginId: string;
  name: string;
  email: string;
  rank: Rank;
  password: string;
}

export const createAccount = (input: AccountCreateInput, signal?: AbortSignal) =>
  request<AdminAccount>("/admin/accounts", { method: "POST", body: input, signal });

export interface AccountPatchInput {
  name?: string;
  email?: string;
  rank?: Rank;
  isActive?: boolean;
}

export const patchAccount = (id: number, patch: AccountPatchInput, signal?: AbortSignal) =>
  request<AdminAccount>(`/admin/accounts/${seg(id)}`, { method: "PATCH", body: patch, signal });

export const resetPassword = (id: number, newPassword: string, signal?: AbortSignal) =>
  request<void>(`/admin/accounts/${seg(id)}/password-reset`, { method: "POST", body: { newPassword }, signal });

export const setSystemAdmin = (id: number, enabled: boolean, signal?: AbortSignal) =>
  request<AdminAccount>(`/admin/accounts/${seg(id)}/system-admin`, { method: "PUT", body: { enabled }, signal });

// ---- 부서 ----
export const listDepartments = (signal?: AbortSignal) => request<AdminDepartment[]>("/admin/departments", { signal });

export const createDepartment = (input: { name: string; parentId: number | null; isExecutiveGroup: boolean }, signal?: AbortSignal) =>
  request<AdminDepartment>("/admin/departments", { method: "POST", body: input, signal });

export const renameDepartment = (id: number, name: string, signal?: AbortSignal) =>
  request<AdminDepartment>(`/admin/departments/${seg(id)}`, { method: "PATCH", body: { name }, signal });

export const setDepartmentHead = (id: number, accountId: number, signal?: AbortSignal) =>
  request<AdminDepartment>(`/admin/departments/${seg(id)}/head`, { method: "PUT", body: { accountId }, signal });

export const addDepartmentMember = (id: number, accountId: number, signal?: AbortSignal) =>
  request<AdminDepartment>(`/admin/departments/${seg(id)}/members`, { method: "POST", body: { accountId }, signal });

export const removeDepartmentMember = (id: number, accountId: number, signal?: AbortSignal) =>
  request<AdminDepartment>(`/admin/departments/${seg(id)}/members/${seg(accountId)}/remove`, { method: "POST", signal });

// ---- 설정·라이선스 ----
export const getSettings = (signal?: AbortSignal) => request<AdminSettings>("/admin/settings", { signal });

export const putSetting = (key: SettingKey, value: boolean, reason: string, signal?: AbortSignal) =>
  request<AdminSetting>(`/admin/settings/${seg(key)}`, { method: "PUT", body: { value, reason }, signal });

export const getLicense = (signal?: AbortSignal) => request<LicenseInfo>("/system/license", { signal });

// ---- 본인 비밀번호 변경(작업 82-2 파트 B) ----
export const changeOwnPassword = (currentPassword: string, newPassword: string, signal?: AbortSignal) =>
  request<void>("/auth/change-password", { method: "POST", body: { currentPassword, newPassword }, signal });

/** 지식 연동 설정 변경(스위치는 bool, 주소는 문자열). 서버가 사유 필수·사설망 검사를 한다(위반 422) */
export const putKnowledgeSetting = (key: KnowledgeSettingKey, value: boolean | string, reason: string, signal?: AbortSignal) =>
  request<KnowledgeSetting>(`/admin/settings/${encodeURIComponent(key)}`, { method: "PUT", body: { value, reason }, signal });
