"use client";

/*
 * 회의록 제목 줄 동작(관리자 이상에게만 화면이 그린다. 실제 권한은 서버 판정, 거부되면 팝업이 서버 문구를 보여 준다).
 * - 진행중(확정 대기·확정): 더보기(⋯) 메뉴에 보류·직권 종료·삭제
 *   (처리 실패·내용 없음은 삭제만, 처리 중은 동작 없음 — 서버가 처리 중 삭제를 409 로 거부)
 * - 보류: "재개" 버튼 + 메뉴에 삭제 / 종료: 메뉴에 삭제 / 삭제됨: 동작 없음
 * - 메뉴: Enter·Space 로 열기, ↑↓ 로 이동, Esc·Tab·바깥 클릭으로 닫기(Esc 는 더보기 버튼으로 포커스 복귀).
 *   항목을 고르면 더보기 버튼으로 포커스를 옮긴 뒤 팝업을 연다(팝업이 닫히면 그 버튼으로 돌아온다)
 */
import { useEffect, useId, useRef, useState } from "react";

import { Button } from "@/components/mono";
import type { MeetingPhase, MeetingStatus } from "@/lib/v2/meetings";

export type MeetingActionKind = "hold" | "end" | "delete" | "resume";

const MENU_LABEL: Record<Exclude<MeetingActionKind, "resume">, string> = {
  hold: "보류",
  end: "직권 종료",
  delete: "삭제",
};

/** 단계·상태별로 보이는 메뉴 항목 */
function menuItems(phase: MeetingPhase, status: MeetingStatus): Exclude<MeetingActionKind, "resume">[] {
  if (phase === "deleted" || status === "processing") return [];
  if (phase === "active") return status === "awaiting_confirmation" || status === "confirmed" ? ["hold", "end", "delete"] : ["delete"];
  return ["delete"]; // 보류·종료
}

interface MeetingActionsProps {
  phase: MeetingPhase;
  status: MeetingStatus;
  onSelect: (kind: MeetingActionKind) => void;
}

export function MeetingActions({ phase, status, onSelect }: MeetingActionsProps) {
  const menuId = useId();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const items = menuItems(phase, status);

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

  function choose(kind: MeetingActionKind) {
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
      {phase === "on_hold" ? <Button onClick={() => onSelect("resume")}>재개</Button> : null}
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
