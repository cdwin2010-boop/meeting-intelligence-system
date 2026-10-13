/* 프로젝트 상태 칩: 공용 StatusChip 으로 통합(작업 디자인 2단계). 승인 대기=승인 대기 색, 진행 중=진행 중 색, 반려=보완 필요 색. 글자 라벨은 항상 함께 쓴다 */
import { StatusChip, type StatusChipTone } from "@/components/v2/StatusChip";
import { STATUS_LABEL, type ProjectStatus } from "@/lib/v2/projects";

const TONE: Record<ProjectStatus, StatusChipTone> = {
  pending_approval: "pending",
  active: "progress",
  rejected: "needs",
};

export function ProjectStatusChip({ status }: { status: ProjectStatus }) {
  return <StatusChip tone={TONE[status] ?? "neutral"} label={STATUS_LABEL[status] ?? status} />;
}
