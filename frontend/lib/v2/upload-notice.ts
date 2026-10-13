/*
 * 업로드 성공 직후 안내(작업 88-2). 올리기 화면이 202 를 받으면 회의록 상세로 이동하므로, 이동 전에 회의록 번호로 표시를 남기고
 * 상세 화면이 한 번만 꺼내 보여 준다(새로고침하면 다시 보이지 않는다). 세션 저장소가 막혀도 오류 없이 안내만 생략된다.
 */
export const UPLOAD_ACCEPTED_NOTICE =
  "녹취 파일이 정상적으로 등록되었습니다. 변환 작업 대기열에 등록되었으며, 순차적으로 변환이 진행됩니다.";

const KEY = "mi.v2.uploadNotice";

export function setUploadNotice(meetingId: number): void {
  try {
    window.sessionStorage.setItem(KEY, String(meetingId));
  } catch {
    // 안내만 생략된다
  }
}

/** 이 회의록에 대한 표시가 있으면 지우고 true */
export function consumeUploadNotice(meetingId: number): boolean {
  try {
    if (window.sessionStorage.getItem(KEY) !== String(meetingId)) return false;
    window.sessionStorage.removeItem(KEY);
    return true;
  } catch {
    return false;
  }
}
