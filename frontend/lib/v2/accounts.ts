/*
 * v2 같은 고객사 계정 목록(참석자 선택용). backend/app/api/accounts.py 의 AccountOption 과 맞춘다.
 * 서버가 로그인 계정의 고객사·활성 계정만 주며, 응답에는 id·이름·직급만 있다.
 */
import { request } from "./http";
import type { Rank } from "./types";

export interface AccountOption {
  id: number;
  name: string;
  rank: Rank;
}

export function listAccounts(signal?: AbortSignal): Promise<AccountOption[]> {
  return request<AccountOption[]>("/accounts", { signal });
}
