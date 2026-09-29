"use client";

import { Button, StatusDot, type StatusDotTone } from "@/components/mono";
import type { ActionItem, ActionItemStatus, SortKey, SortState } from "@/lib/types";

/* 상태값 → StatusDot 톤/라벨 매핑 (Teal 완료 / Blue 진행 / Red 지연 / 회색 대기) */
const STATUS_VIEW: Record<ActionItemStatus, { tone: StatusDotTone; label: string }> = {
  open: { tone: "queued", label: "Open" },
  in_progress: { tone: "building", label: "In progress" },
  done: { tone: "ready", label: "Done" },
  overdue: { tone: "error", label: "Overdue" },
};

interface ColumnDef {
  key: SortKey;
  header: string;
  className?: string;
}

const COLUMNS: ColumnDef[] = [
  { key: "id", header: "ID", className: "w-24" },
  { key: "task", header: "Task" },
  { key: "assignee", header: "Assignee", className: "w-28" },
  { key: "dueDate", header: "Due date", className: "w-32" },
  { key: "status", header: "Status", className: "w-36" },
];

const COLUMN_COUNT = COLUMNS.length + 1; // + 삭제 버튼 열

/** 현재 정렬 상태 → aria-sort 값 (스크린리더가 정렬 방향을 읽어줍니다) */
function getAriaSort(sort: SortState, key: SortKey): "ascending" | "descending" | "none" {
  if (sort?.key !== key) return "none";
  return sort.direction === "asc" ? "ascending" : "descending";
}

interface ActionItemTableProps {
  items: ActionItem[];
  sort: SortState;
  onSort: (key: SortKey) => void;
  loading: boolean;
  error: string | null;
  expandedIds: ReadonlySet<string>;
  onToggleExpand: (id: string) => void;
  onDelete: (item: ActionItem) => void;
}

export function ActionItemTable({
  items,
  sort,
  onSort,
  loading,
  error,
  expandedIds,
  onToggleExpand,
  onDelete,
}: ActionItemTableProps) {
  return (
    <div className="overflow-x-auto rounded-mn-card border border-mn-border bg-mn-surface">
      {/* aria-busy: 로딩 중임을 보조기기에 알림 */}
      <table className="w-full border-collapse text-left" aria-busy={loading}>
        <caption className="mn-sr-only">
          Action items extracted from this meeting. Select a column header to sort. Select a row to
          show its evidence quote.
        </caption>

        <thead>
          <tr className="h-12 border-b border-mn-border">
            {COLUMNS.map((column) => {
              const active = sort?.key === column.key;
              return (
                <th
                  key={column.key}
                  scope="col"
                  aria-sort={getAriaSort(sort, column.key)}
                  className={`px-4 text-xs font-medium ${column.className ?? ""}`}
                >
                  <button
                    type="button"
                    onClick={() => onSort(column.key)}
                    className={[
                      "mn-focus -mx-2 inline-flex h-8 items-center gap-1 rounded-mn-control px-2",
                      "hover:bg-mn-elevated",
                      active ? "text-mn-text" : "text-mn-muted",
                    ].join(" ")}
                  >
                    {column.header}
                    {/* 색이 아니라 기호로도 방향을 표시 (↕ 정렬 없음 / ↑ 오름차순 / ↓ 내림차순) */}
                    <span aria-hidden="true" className="font-mn-mono">
                      {active ? (sort.direction === "asc" ? "↑" : "↓") : "↕"}
                    </span>
                  </button>
                </th>
              );
            })}
            <th scope="col" className="w-24 px-4">
              <span className="mn-sr-only">Actions</span>
            </th>
          </tr>
        </thead>

        <tbody className={loading && items.length > 0 ? "opacity-60" : undefined}>
          {error ? (
            <tr className="h-12">
              <td colSpan={COLUMN_COUNT} className="px-4">
                {/* 에러는 색(Red 점)과 함께 반드시 문구로도 표시 */}
                <span role="alert">
                  <StatusDot tone="error" label={error} />
                </span>
              </td>
            </tr>
          ) : null}

          {!error && loading && items.length === 0 ? (
            <tr className="h-12">
              <td colSpan={COLUMN_COUNT} className="px-4 text-sm text-mn-muted">
                Loading action items…
              </td>
            </tr>
          ) : null}

          {!error && !loading && items.length === 0 ? (
            <tr className="h-12">
              <td colSpan={COLUMN_COUNT} className="px-4 text-sm text-mn-muted">
                No action items in this meeting.
              </td>
            </tr>
          ) : null}

          {items.map((item) => {
            const expanded = expandedIds.has(item.id);
            const quoteId = `quote-${item.id}`;
            const status = STATUS_VIEW[item.status];

            return (
              <ActionItemRows
                key={item.id}
                item={item}
                expanded={expanded}
                quoteId={quoteId}
                status={status}
                onToggleExpand={onToggleExpand}
                onDelete={onDelete}
              />
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * 한 액션아이템 = <tr> 두 개(본 행 + 펼침 행)
 * <tbody> 안에서 형제 <tr>로 이어 붙이기 위해 Fragment(<>)를 사용합니다.
 * ------------------------------------------------------------------ */
interface ActionItemRowsProps {
  item: ActionItem;
  expanded: boolean;
  quoteId: string;
  status: { tone: StatusDotTone; label: string };
  onToggleExpand: (id: string) => void;
  onDelete: (item: ActionItem) => void;
}

function ActionItemRows({
  item,
  expanded,
  quoteId,
  status,
  onToggleExpand,
  onDelete,
}: ActionItemRowsProps) {
  return (
    <>
      {/* 행 어디를 눌러도 토글. 키보드 사용자는 안쪽 button(Enter/Space)으로 같은 동작을 합니다.
          (button의 click이 tr까지 버블링되므로 핸들러는 tr에 한 번만 둡니다) */}
      <tr
        onClick={() => onToggleExpand(item.id)}
        className="h-12 cursor-pointer border-b border-mn-border hover:bg-mn-elevated"
      >
        <td className="px-4 font-mn-mono text-xs text-mn-muted">{item.id}</td>

        <td className="max-w-0 px-4">
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={expanded ? quoteId : undefined}
            className="mn-focus -mx-2 flex w-[calc(100%+1rem)] items-center gap-2 rounded-mn-control px-2 py-1 text-left"
          >
            <span
              aria-hidden="true"
              className={`shrink-0 text-mn-muted transition-transform ${expanded ? "rotate-90" : ""}`}
            >
              ▸
            </span>
            {/* 행 높이 48px을 지키기 위해 한 줄로 자르고, 전체 문장은 title/펼침 영역에서 확인 */}
            <span className="truncate text-sm text-mn-text" title={item.task}>
              {item.task}
            </span>
          </button>
        </td>

        <td className="px-4 text-sm text-mn-text">{item.assignee}</td>
        <td className="px-4 font-mn-mono text-xs text-mn-text">{item.dueDate}</td>
        <td className="px-4">
          <StatusDot tone={status.tone} label={status.label} />
        </td>

        <td className="px-4 text-right">
          <Button
            size="sm"
            variant="secondary"
            aria-label={`Delete action item ${item.id}`}
            onClick={(event) => {
              event.stopPropagation(); // 삭제 클릭이 행 펼침으로 번지지 않도록 차단
              onDelete(item);
            }}
          >
            Delete
          </Button>
        </td>
      </tr>

      {expanded ? (
        <tr id={quoteId} className="border-b border-mn-border bg-mn-bg">
          <td />
          <td colSpan={COLUMN_COUNT - 1} className="px-4 py-4">
            <figure>
              <figcaption className="mb-2 flex items-center gap-2 text-xs text-mn-muted">
                <span>Evidence quote</span>
                <span aria-hidden="true">·</span>
                <span className="font-mn-mono">{item.quote.timestamp}</span>
                <span aria-hidden="true">·</span>
                <span>{item.quote.speaker}</span>
              </figcaption>
              <blockquote className="border-l border-mn-border pl-4 text-sm leading-6 text-mn-text">
                {item.quote.text}
              </blockquote>
            </figure>
          </td>
        </tr>
      ) : null}
    </>
  );
}
