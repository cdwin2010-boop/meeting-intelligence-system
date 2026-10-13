/*
 * 엔진 시험 모드 API(작업 91-1, 시험 전용). 계약: docs/contracts/engine-test-v1.json.
 * 시험 모드(ENGINE_TEST_MODE)가 꺼져 있으면 서버가 403 으로 거부한다(화면 숨김만으로 막지 않는다).
 * API 키 원문은 응답에 없다(설정 여부·마스킹 표시만). 서버 주소는 설정 화면에서 바꿀 수 있어야 하므로 내려온다.
 */
import { request } from "./http";

export type EngineChoice = "default" | "remote";

export const ENGINE_CHOICE_LABEL: Record<EngineChoice, string> = {
  default: "기본 엔진",
  remote: "원격(시험용)",
};

/** 화면 고정 안내(서버 notice 와 같은 문구) */
export const ENGINE_TEST_NOTICE = "외부 터널을 거치므로 시험용 데이터만 사용하세요";

export interface RemoteSettings {
  baseUrl: string;
  model: string;
  apiKeySet: boolean;
  apiKeyMasked: string;
  timeoutSeconds: number;
  chunkMaxChars: number;
  configured: boolean;
  defaultEngine: EngineChoice;
  notice: string;
}

export interface RemoteSettingsInput {
  baseUrl: string;
  model: string;
  /** 생략·빈 값이면 기존 키 유지 */
  apiKey?: string;
  timeoutSeconds: number;
  chunkMaxChars: number;
}

export interface LoadTestResult {
  ok: boolean;
  modelListed: boolean;
  modelFound: boolean;
  currentModel: string;
  availableModels: string[];
  responseMs: number | null;
  jsonParsed: boolean;
  failureStage: "" | "models" | "inference";
  failureReason: string;
}

const BASE = "/admin/engine-test/remote";

export function getRemoteSettings(signal?: AbortSignal): Promise<RemoteSettings> {
  return request<RemoteSettings>(BASE, { signal });
}

export function saveRemoteSettings(input: RemoteSettingsInput, signal?: AbortSignal): Promise<RemoteSettings> {
  return request<RemoteSettings>(BASE, { method: "PUT", body: input, signal });
}

/** 로딩 테스트: 진행 중인 처리 작업이 있으면 409(서버 문구), 모델 목록·추론 요청이 있어 시간이 걸린다 */
export function runLoadTest(signal?: AbortSignal): Promise<LoadTestResult> {
  return request<LoadTestResult>(`${BASE}/load-test`, { method: "POST", signal, timeoutMs: 150_000 });
}
