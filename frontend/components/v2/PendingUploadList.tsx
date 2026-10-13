"use client";

/*
 * 등록 보류 목록(작업 88-2, 모바일 올리기 화면 아래). 이 기기 브라우저에 임시 저장된 파일만 보여 준다.
 * - 항목: 파일명·크기·보류 시각, [지금 올리기]·[삭제]. [지금 올리기]는 올리기 화면의 같은 업로드 흐름(Wi-Fi 안내 판정 포함)을 쓴다.
 *   실패하면 목록에 남기고 사유를 항목 아래에 보인다. 자동 재업로드는 없다.
 * - [삭제]는 "기기에 임시 저장된 파일만 지워집니다" 확인 팝업(확인 버튼 이름 "보류 파일 삭제", 취소에 먼저 포커스) 뒤에 지운다.
 */
import { useState } from "react";

import { Button, Modal, StatusDot } from "@/components/mono";
import { formatFileSize } from "@/components/v2/FilePicker";
import { formatDateTime } from "@/components/v2/meeting-display";
import type { PendingMeta } from "@/lib/v2/pending-uploads";

export function PendingUploadList({
  items,
  errors,
  busy,
  onUploadNow,
  onDelete,
}: {
  items: PendingMeta[];
  /** 항목 id → 마지막 올리기 실패 사유 */
  errors: Record<string, string>;
  /** 업로드 진행 중이면 [지금 올리기]를 막는다 */
  busy: boolean;
  onUploadNow: (item: PendingMeta) => void;
  onDelete: (item: PendingMeta) => Promise<void>;
}) {
  const [target, setTarget] = useState<PendingMeta | null>(null);
  const [deleting, setDeleting] = useState(false);

  if (items.length === 0) return null;

  async function confirmDelete() {
    if (!target || deleting) return;
    setDeleting(true);
    try {
      await onDelete(target);
    } finally {
      setDeleting(false);
      setTarget(null);
    }
  }

  return (
    <section aria-label="등록 보류 목록" className="flex flex-col gap-3 rounded-mn-card border border-mn-border bg-mn-surface p-4">
      <h2 className="text-[17px] font-bold leading-6">
        등록 보류 목록 (<span className="font-mn-mono">{items.length}</span>건)
      </h2>
      <p className="text-[13px] leading-5 text-mn-muted">Wi-Fi 연결 후 직접 올려 주세요. 이 기기에만 임시 저장되어 있습니다.</p>
      <ul className="flex flex-col gap-3">
        {items.map((item) => (
          <li key={item.id} className="flex flex-col gap-2 rounded-mn-control border border-mn-border p-3">
            <span title={item.fileName} className="break-all text-sm font-medium">
              {item.fileName}
            </span>
            <span className="text-[13px] leading-5 text-mn-muted">
              <span className="font-mn-mono">{formatFileSize(item.size)}</span> · 보류 <span className="font-mn-mono">{formatDateTime(item.pendingAt)}</span>
            </span>
            {errors[item.id] ? (
              <span role="alert" className="text-[13px] leading-5">
                <StatusDot tone="error" label={`올리지 못했습니다 · ${errors[item.id]}`} />
              </span>
            ) : null}
            <span className="flex flex-wrap gap-2">
              <Button size="sm" variant="primary" disabled={busy} aria-label={`${item.fileName} 지금 올리기`} onClick={() => onUploadNow(item)} className="max-md:min-h-11">
                지금 올리기
              </Button>
              <Button size="sm" disabled={busy} aria-label={`${item.fileName} 삭제`} onClick={() => setTarget(item)} className="max-md:min-h-11">
                삭제
              </Button>
            </span>
          </li>
        ))}
      </ul>
      <Modal
        open={target !== null}
        onClose={() => (deleting ? undefined : setTarget(null))}
        tone="danger"
        title="보류 파일 삭제"
        description={`기기에 임시 저장된 파일만 지워집니다. ${target ? `“${target.fileName}”` : ""} 이 파일은 다시 올리려면 새로 선택해야 합니다.`}
        confirmLabel="보류 파일 삭제"
        cancelLabel="취소"
        confirmLoading={deleting}
        onConfirm={() => void confirmDelete()}
      />
    </section>
  );
}
