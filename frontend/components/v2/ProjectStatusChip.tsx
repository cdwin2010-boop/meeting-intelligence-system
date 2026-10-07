/* 프로젝트 상태 칩: 승인 대기=중립, 진행 중=정보, 반려=취소 토큰. 색만으로 구분하지 않고 한글 라벨을 함께 쓴다 */
import { STATUS_LABEL, type ProjectStatus } from "@/lib/v2/projects";

const CHIP_CLASS: Record<ProjectStatus, string> = {
  pending_approval: "mn-chip-neutral",
  active: "mn-chip-info",
  rejected: "mn-chip-cancelled",
};

export function ProjectStatusChip({ status }: { status: ProjectStatus }) {
  return <span className={`mn-chip ${CHIP_CLASS[status] ?? "mn-chip-neutral"} whitespace-nowrap`}>{STATUS_LABEL[status] ?? status}</span>;
}
