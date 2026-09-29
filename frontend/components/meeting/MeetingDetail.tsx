"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { deleteActionItem, fetchActionItems, isAbortError } from "@/lib/api";
import type { ActionItem, Meeting, SortKey, SortState } from "@/lib/types";
import { ActionItemTable } from "./ActionItemTable";
import { DeleteActionItemModal } from "./DeleteActionItemModal";
import { MeetingHeader } from "./MeetingHeader";
import { TranscriptViewer } from "./TranscriptViewer";

/** 단일 열 3-State 정렬: 없음 → 오름차순 → 내림차순 → 없음 (다른 열을 누르면 그 열의 오름차순부터) */
function nextSort(current: SortState, key: SortKey): SortState {
  if (current?.key !== key) return { key, direction: "asc" };
  if (current.direction === "asc") return { key, direction: "desc" };
  return null;
}

export function MeetingDetail({ meeting }: { meeting: Meeting }) {
  const [items, setItems] = useState<ActionItem[]>([]);
  const [sort, setSort] = useState<SortState>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [expandedIds, setExpandedIds] = useState<ReadonlySet<string>>(new Set());

  const [deleteTarget, setDeleteTarget] = useState<ActionItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState(""); // 스크린리더용 결과 안내

  const deleteAbortRef = useRef<AbortController | null>(null);
  const regionRef = useRef<HTMLDivElement>(null);

  /* ---------- 조회: 정렬이 바뀔 때마다 새로 가져오고, 이전 요청은 취소 ---------- */
  useEffect(() => {
    const controller = new AbortController(); // 이 요청 전용 "취소 리모컨"
    setLoading(true);
    setLoadError(null);

    fetchActionItems(meeting.id, sort, controller.signal)
      .then((rows) => {
        setItems(rows);
        setLoading(false);
      })
      .catch((error: unknown) => {
        if (isAbortError(error)) return; // 우리가 취소한 것 → 에러 아님, 상태도 건드리지 않음
        setLoadError("Failed to load action items.");
        setLoading(false);
      });

    // 정렬이 또 바뀌거나 화면을 떠나면 진행 중인 요청을 취소 (오래된 응답이 최신 결과를 덮어쓰는 경쟁 상태 방지)
    return () => controller.abort();
  }, [meeting.id, sort]);

  // 화면을 떠날 때 진행 중인 삭제 요청도 정리
  useEffect(() => () => deleteAbortRef.current?.abort(), []);

  /* ---------- 핸들러 ---------- */
  const handleSort = useCallback((key: SortKey) => {
    setSort((current) => nextSort(current, key));
  }, []);

  const handleToggleExpand = useCallback((id: string) => {
    setExpandedIds((current) => {
      const next = new Set(current); // 상태는 직접 수정하지 않고 복사본을 만든다(불변성)
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const closeDeleteModal = useCallback(() => {
    deleteAbortRef.current?.abort(); // 삭제 요청이 진행 중이었다면 취소
    deleteAbortRef.current = null;
    setDeleting(false);
    setDeleteError(null);
    setDeleteTarget(null);
  }, []);

  const handleConfirmDelete = useCallback(async () => {
    if (!deleteTarget) return;
    const target = deleteTarget;
    const controller = new AbortController();
    deleteAbortRef.current = controller;
    setDeleting(true);
    setDeleteError(null);

    try {
      await deleteActionItem(target.id, controller.signal);
      setItems((current) => current.filter((item) => item.id !== target.id));
      setExpandedIds((current) => {
        const next = new Set(current);
        next.delete(target.id);
        return next;
      });
      setAnnouncement(`Action item ${target.id} deleted.`);
      deleteAbortRef.current = null;
      setDeleting(false);
      setDeleteTarget(null);

      // 삭제된 행은 사라져서 모달이 포커스를 돌려줄 곳이 없으므로, 표 영역으로 포커스를 옮깁니다.
      requestAnimationFrame(() => regionRef.current?.focus());
    } catch (error: unknown) {
      if (isAbortError(error)) return; // 사용자가 Cancel/ESC로 취소한 경우
      setDeleting(false);
      setDeleteError("Could not delete the action item. Try again.");
    }
  }, [deleteTarget]);

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-4 py-12">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <TranscriptViewer transcriptText={meeting.transcriptText} />
        <Link
          href="/upload"
          className="mn-focus inline-flex h-8 items-center rounded-mn-control border border-mn-border px-3 text-[13px] font-medium text-mn-text hover:bg-mn-elevated"
        >
          음성 등록
        </Link>
      </div>
      <MeetingHeader meeting={meeting} />

      <section aria-labelledby="action-items-heading" className="flex flex-col gap-3">
        <div className="flex items-baseline justify-between">
          <h2 id="action-items-heading" className="text-xl font-semibold leading-7 text-mn-text">
            Action items
          </h2>
          <span className="font-mn-mono text-xs text-mn-muted">{items.length} total</span>
        </div>

        {/* tabIndex=-1: 코드로만 포커스를 받을 수 있는 영역 (삭제 후 포커스 복귀용) */}
        <div ref={regionRef} tabIndex={-1} className="outline-none">
          <ActionItemTable
            items={items}
            sort={sort}
            onSort={handleSort}
            loading={loading}
            error={loadError}
            expandedIds={expandedIds}
            onToggleExpand={handleToggleExpand}
            onDelete={setDeleteTarget}
          />
        </div>
      </section>

      <DeleteActionItemModal
        item={deleteTarget}
        deleting={deleting}
        error={deleteError}
        onCancel={closeDeleteModal}
        onConfirm={handleConfirmDelete}
      />

      <p role="status" aria-live="polite" className="mn-sr-only">
        {announcement}
      </p>
    </div>
  );
}
