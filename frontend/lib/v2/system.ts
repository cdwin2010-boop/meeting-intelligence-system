/*
 * v2 처리 엔진 정보: GET /api/system/engine (로그인 필요). 응답은 {engine: "fake"|"gemini", isFake} 뿐이다(키·모드·모델은 내려오지 않는다).
 * 조회에 실패하면(서버 없음·권한 등) 모른다고 보고 안내를 보이지 않는다.
 */
import { request } from "./http";

export interface EngineInfo {
  engine: string;
  isFake: boolean;
}

export function getEngine(signal?: AbortSignal): Promise<EngineInfo> {
  return request<EngineInfo>("/system/engine", { signal });
}

/** 가짜 처리 모드 안내 문구(앱 틀·업로드 화면 공통) */
export const FAKE_ENGINE_NOTICE = "시험용 가짜 처리 모드입니다. 업로드한 음성은 실제로 처리되지 않습니다.";
/** 회의록 상세에서 그 회의록에 기록된 처리 엔진이 fake 일 때 */
export const FAKE_MEETING_NOTICE = "이 회의록은 시험용 가짜 처리 모드로 만들어졌습니다. 실제 음성을 처리한 결과가 아닙니다.";

/** 처리 중 자동 새로고침 주기(초). NEXT_PUBLIC_PROCESSING_REFRESH_SEC, 기본 5초 */
export const PROCESSING_REFRESH_MS = (() => {
  const seconds = Number(process.env.NEXT_PUBLIC_PROCESSING_REFRESH_SEC);
  return (Number.isFinite(seconds) && seconds > 0 ? seconds : 5) * 1000;
})();
