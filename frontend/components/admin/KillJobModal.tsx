"use client";

import { Modal } from "@/components/mono";
import type { AdminJob } from "@/lib/types";

interface KillJobModalProps {
  /** 종료 대상. null이면 모달이 닫혀 있음 */
  job: AdminJob | null;
  killing: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}

export function KillJobModal({ job, killing, error, onCancel, onConfirm }: KillJobModalProps) {
  return (
    <Modal
      open={job !== null}
      onClose={onCancel}
      tone="danger" // → alertdialog 역할, Cancel 우선 포커스, Red 확인 버튼
      title="Kill Job"
      // 무엇이 사라지는지 + 그 후에 할 수 있는 것을 구체적으로 안내
      description={
        job ? (
          <>
            <span className="font-mn-mono text-mn-text">{job.id}</span> “{job.meetingTitle}” will
            stop immediately
            {job.worker ? (
              <>
                {" "}
                on worker <span className="font-mn-mono text-mn-text">{job.worker.id}</span>
              </>
            ) : null}
            . Partial output is discarded and the job is marked as failed. You can retry it
            afterwards.
          </>
        ) : null
      }
      // "OK"/"확인" 대신 구체적인 행동명
      confirmLabel="Kill Processing Job"
      cancelLabel="Cancel"
      onConfirm={onConfirm}
      confirmLoading={killing}
    >
      {error ? (
        <p role="alert" className="mt-2 text-sm text-mn-text">
          <span aria-hidden="true" className="mr-2 inline-block size-2 rounded-full bg-mn-red" />
          {error}
        </p>
      ) : null}
    </Modal>
  );
}
