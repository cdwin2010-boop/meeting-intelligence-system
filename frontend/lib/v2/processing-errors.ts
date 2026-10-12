/*
 * 처리 작업 오류 코드 → 한국어 이유(상수 한 곳). 코드는 backend app/pipeline/errors.py(error_code)·app/services/reprocess.py 가 내려주는 분류 코드다.
 * 코드 목록: gemini_key_missing · upload_missing · timeout · stt_file_failed · extraction_invalid · internal_error · server_restarted · gemini_api_{HTTP 상태}
 *   · llm_unavailable · llm_error · llm_host_blocked · external_disabled · worker_stopped(작업 82-2)
 * 매핑에 없는 코드는 "처리 중 오류가 발생했습니다 (코드)". 화면은 이유와 함께 코드 글자도 보여 준다.
 */
const TRANSIENT = "Gemini 서버가 일시적으로 응답하지 않았습니다. 잠시 뒤 다시 처리해 보세요.";
const KEY = "Gemini 키 설정을 확인해야 합니다.";

export const PROCESSING_ERROR_TEXT: Record<string, string> = {
  gemini_key_missing: KEY,
  gemini_api_401: KEY,
  gemini_api_403: KEY,
  gemini_api_500: TRANSIENT,
  gemini_api_502: TRANSIENT,
  gemini_api_503: TRANSIENT,
  gemini_api_504: TRANSIENT,
  gemini_api_429: "Gemini 사용 한도(할당량)를 초과했습니다. 잠시 뒤 다시 처리하거나 키 설정을 확인해 주세요.",
  gemini_api_400: "Gemini 가 요청을 받아들이지 않았습니다. 음성 파일 형식이나 모델 설정을 확인해 주세요.",
  gemini_api_404: "Gemini 가 요청을 받아들이지 않았습니다. 음성 파일 형식이나 모델 설정을 확인해 주세요.",
  timeout: "Gemini 응답 시간이 초과되었습니다. 다시 처리해 보세요.",
  stt_file_failed: "Gemini 가 음성 파일을 처리하지 못했습니다. 파일을 확인해 주세요.",
  extraction_invalid: "업무 추출 결과의 형식이 올바르지 않았습니다. 다시 처리해 보세요.",
  upload_missing: "보관된 음성 파일을 찾을 수 없습니다. 음성을 다시 올려 주세요.",
  internal_error: "서버 내부 오류로 처리하지 못했습니다. 다시 처리해 보세요.",
  server_restarted: "서버가 재시작되어 처리가 중단되었습니다. 다시 처리해 주세요.",
  // 로컬 AI·외부 연동 정책·워커(작업 82-2)
  llm_unavailable: "로컬 AI 서버에 연결하지 못했거나 응답이 늦었습니다. 서버가 켜져 있는지 확인한 뒤 다시 처리해 보세요.",
  llm_error: "로컬 AI 서버가 요청을 받아들이지 않았습니다. 시스템 관리자에게 모델·주소 설정 확인을 요청해 주세요.",
  llm_host_blocked: "로컬 AI 서버 주소가 허용되지 않습니다. 시스템 관리자에게 외부 주소 허용 설정 확인을 요청해 주세요.",
  external_disabled: "외부 AI 사용이 허용되지 않아 처리하지 않았습니다. 시스템 관리자에게 외부 연동 허용을 요청한 뒤 다시 처리해 주세요.",
  worker_stopped: "처리 프로그램이 멈춰 작업을 끝내지 못했습니다. 잠시 뒤 다시 처리해 보세요.",
};

/** 오류 코드 → 한국어 이유. 코드가 없으면 일반 문구 */
export function describeProcessingError(code: string | null | undefined): string {
  if (!code) return "처리 중 오류가 발생했습니다.";
  return PROCESSING_ERROR_TEXT[code] ?? `처리 중 오류가 발생했습니다 (${code})`;
}
