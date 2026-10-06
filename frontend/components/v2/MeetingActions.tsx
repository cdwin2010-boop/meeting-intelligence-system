"use client";

/*
 * 회의록 제목 줄 동작. 어떤 버튼·메뉴가 보이는지는 서버가 내려준 허용 동작(allowedActions)으로만 정한다(직급·상태를 직접 비교하지 않음).
 * - 더보기(⋯) 메뉴: 엑셀 다운로드(download_excel)·변경 이력(view_history)·수정 회의록 업로드(upload_update)·보류(hold_meeting)·직권 종료(end_meeting)·삭제(delete_meeting)
 * - "재개" 버튼: resume_meeting
 * - 허용 목록이 없으면 아무것도 그리지 않는다. 서버의 403·409 는 팝업이 서버 문구로 보여 준다
 * - 메뉴: Enter·Space 로 열기, ↑↓ 로 이동, Esc·Tab·바깥 클릭으로 닫기(Esc 는 더보기 버튼으로 포커스 복귀).
 *   항목을 고르면 더보기 버튼으로 포커스를 옮긴 뒤 팝업을 연다(팝업이 닫히면 그 버튼으로 돌아온다)
 */
import { useEffect, useId, useRef, useState } from "react";

import { Button } from "@/components/mono";
import { can, MEETING_ACTION, type ActionName } from "@/lib/v2/actions";

export type MeetingActionKind = "hold" | "end" | "delete" | "resume" | "download" | "history" | "upload";

type MenuKind = Exclude<MeetingActionKind, "resume">;

const MENU_LABEL: Record<MenuKind, string> = {
  download: "엑셀 다운로드",
  history: "변경 이력",
  upload: "수정 회의록 업로드",
  hold: "보류",
  end: "직권 종료",
  delete: "삭제",
};

/** 메뉴 항목(표시 순서)과 필요한 허용 동작 */
const MENU_ACTION: [MenuKind, ActionName][] = [
  ["download", MEETING_ACTION.downloadExcel],
  ["history", MEETING_ACTION.viewHistory],
  ["upload", MEETING_ACTION.uploadUpdate],
  ["hold", MEETING_ACTION.holdMeeting],
  ["end", MEETING_ACTION.endMeeting],
  ["delete", MEETING_ACTION.deleteMeeting],
];

interface MeetingActionsProps {
  /** 서버가 내려준 회의록 단위 허용 동작 */
  allowed: readonly string[] | undefined;
  onSelect: (kind: MeetingActionKind) => void;
}

export function MeetingActions({ allowed, onSelect }: MeetingActionsProps) {
  const menuId = useId();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const items = MENU_ACTION.filter(([, name]) => can(allowed, name)).map(([kind]) => kind);

  // 열리면 첫 항목으로 포커스, 바깥을 누르면 닫기
  useEffect(() => {
    if (!open) return;
    menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus();
    const onDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!menuRef.current?.contains(target) && !triggerRef.current?.contains(target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  function choose(kind: MenuKind) {
    setOpen(false);
    triggerRef.current?.focus();
    onSelect(kind);
  }

  function onMenuKeyDown(event: React.KeyboardEvent) {
    const buttons = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? []);
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      triggerRef.current?.focus();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      buttons[(index + 1) % buttons.length]?.focus();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      buttons[(index - 1 + buttons.length) % buttons.length]?.focus();
    } else if (event.key === "Tab") {
      setOpen(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      {can(allowed, MEETING_ACTION.resumeMeeting) ? <Button onClick={() => onSelect("resume")}>재개</Button> : null}
      {items.length > 0 ? (
        <div className="relative">
          <Button
            ref={triggerRef}
            aria-label="회의록 더보기"
            aria-haspopup="menu"
            aria-expanded={open}
            aria-controls={open ? menuId : undefined}
            onClick={() => setOpen((v) => !v)}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown" && !open) {
                event.preventDefault();
                setOpen(true);
              }
            }}
          >
            ⋯
          </Button>
          {open ? (
            <div
              ref={menuRef}
              id={menuId}
              role="menu"
              aria-label="회의록 동작"
              onKeyDown={onMenuKeyDown}
              className="absolute right-0 z-20 mt-1 flex min-w-[160px] flex-col rounded-mn-card border border-mn-border bg-mn-elevated py-1"
            >
              {items.map((kind) => (
                <button
                  key={kind}
                  type="button"
                  role="menuitem"
                  onClick={() => choose(kind)}
                  className="mn-focus h-9 px-3 text-left text-sm text-mn-text hover:bg-mn-surface"
                >
                  {MENU_LABEL[kind]}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
