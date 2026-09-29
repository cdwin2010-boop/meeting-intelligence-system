"use client";

import { Modal } from "@/components/mono";
import type { ActionItem } from "@/lib/types";

interface DeleteActionItemModalProps {
  /** 삭제 대상. null이면 모달이 닫혀 있음 */
  item: ActionItem | null;
  deleting: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}

export function DeleteActionItemModal({
  item,
  deleting,
  error,
  onCancel,
  onConfirm,
}: DeleteActionItemModalProps) {
  return (
    <Modal
      open={item !== null}
      onClose={onCancel}
      tone="danger"
      title="Delete Action Item"
      // 무엇을 잃는지 + 되돌릴 수 없음 (Mono Dark 파괴적 액션 규칙)
      description={
        item ? (
          <>
            <span className="font-mn-mono text-mn-text">{item.id}</span> “{item.task}” and its
            evidence quote will be permanently removed. This cannot be undone.
          </>
        ) : null
      }
      // "OK"/"확인" 대신 구체적인 행동명
      confirmLabel="Delete action item"
      cancelLabel="Cancel"
      onConfirm={onConfirm}
      confirmLoading={deleting}
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
