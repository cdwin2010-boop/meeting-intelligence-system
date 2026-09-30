"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Button } from "./Button";

/* ------------------------------------------------------------------ *
 * 모달 스택 (모듈 전역)
 * 여러 모달이 겹쳐 열릴 수 있으므로, ESC / 백드롭 클릭 / 포커스 트랩은
 * "가장 위에 있는(마지막에 push된)" 모달에게만 적용합니다.
 * 비유: 접시를 쌓아둔 더미에서는 맨 위 접시만 집을 수 있습니다.
 * ------------------------------------------------------------------ */
interface StackEntry {
  id: string;
  root: HTMLElement;
}
const modalStack: StackEntry[] = [];

const isTop = (id: string) => modalStack[modalStack.length - 1]?.id === id;

// inert 속성: 해당 영역 전체를 클릭/포커스/스크린리더에서 제외시킵니다.
const setInert = (el: HTMLElement | undefined, inert: boolean) => {
  if (!el) return;
  if (inert) el.setAttribute("inert", "");
  else el.removeAttribute("inert");
};

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

const getFocusable = (container: HTMLElement) =>
  Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));

/* ------------------------------------------------------------------ */

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  /** "danger"면 alertdialog 역할 + 빨간 확인 버튼 + Cancel 버튼에 먼저 포커스 */
  tone?: "default" | "danger";
  /** 확인 버튼 라벨. danger일 때는 "Delete action item"처럼 구체적인 행동명을 쓰세요. */
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm?: () => void;
  confirmDisabled?: boolean;
  /** 확인 동작이 진행 중일 때 (버튼 비활성화) */
  confirmLoading?: boolean;
  closeOnEsc?: boolean;
  closeOnOverlayClick?: boolean;
  /**
   * 대화상자 상자의 크기·위치 클래스. 없으면 기존 그대로 "w-full max-w-md".
   * 크기를 직접 조절하는 창(전사 원문)처럼 기본 폭 제한을 풀어야 할 때만 넘긴다.
   */
  panelClassName?: string;
  children?: ReactNode;
}

export function Modal({
  open,
  onClose,
  title,
  description,
  tone = "default",
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  onConfirm,
  confirmDisabled = false,
  confirmLoading = false,
  closeOnEsc = true,
  closeOnOverlayClick = true,
  panelClassName = "w-full max-w-md",
  children,
}: ModalProps) {
  const id = useId();
  const titleId = `${id}-title`;
  const descId = `${id}-desc`;

  const rootRef = useRef<HTMLDivElement>(null); // 백드롭 포함 최상위 노드
  const dialogRef = useRef<HTMLDivElement>(null); // 실제 대화상자 박스
  const cancelRef = useRef<HTMLButtonElement>(null);

  // onClose를 ref에 담아두면 부모가 리렌더되어 새 함수를 넘겨도
  // 아래 useEffect가 다시 실행되지 않습니다(= 포커스가 튀지 않음).
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const root = rootRef.current;
    const dialog = dialogRef.current;
    if (!root || !dialog) return;

    // 1) 닫힐 때 돌아갈 곳(모달을 연 버튼)을 기억
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;

    // 2) 스택에 등록하고, 바로 아래 모달은 inert 처리
    setInert(modalStack[modalStack.length - 1]?.root, true);
    modalStack.push({ id, root });

    // 3) 초기 포커스: danger는 Cancel(안전한 쪽), 그 외는 첫 번째 포커스 가능 요소
    const initial = tone === "danger" ? cancelRef.current : getFocusable(dialog)[0];
    (initial ?? dialog).focus();

    // 4) 키보드: ESC 닫기 + Tab 포커스 트랩 (맨 위 모달일 때만)
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isTop(id)) return;

      if (event.key === "Escape" && closeOnEsc) {
        event.stopPropagation();
        onCloseRef.current();
        return;
      }

      if (event.key === "Tab") {
        const focusable = getFocusable(dialog);
        if (focusable.length === 0) {
          event.preventDefault();
          dialog.focus();
          return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const active = document.activeElement;

        if (!dialog.contains(active)) {
          // 포커스가 모달 밖으로 나갔다면 안으로 되돌림
          event.preventDefault();
          first.focus();
        } else if (event.shiftKey && active === first) {
          event.preventDefault();
          last.focus(); // 처음에서 Shift+Tab → 마지막으로 순환
        } else if (!event.shiftKey && active === last) {
          event.preventDefault();
          first.focus(); // 마지막에서 Tab → 처음으로 순환
        }
      }
    };
    document.addEventListener("keydown", onKeyDown);

    // 5) 정리(cleanup): 이벤트 해제, 스택에서 제거, 아래 모달 복구, 포커스 반환
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      const index = modalStack.findIndex((entry) => entry.id === id);
      if (index !== -1) modalStack.splice(index, 1);
      setInert(modalStack[modalStack.length - 1]?.root, false);
      opener?.focus();
    };
  }, [open, id, tone, closeOnEsc]);

  // 서버 렌더링 중에는 document가 없으므로 아무것도 그리지 않습니다.
  if (!open || typeof document === "undefined") return null;

  const isDanger = tone === "danger";

  return createPortal(
    <div
      ref={rootRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-mn-overlay p-4"
      onMouseDown={(event) => {
        // 백드롭 자체를 눌렀을 때만 닫음 (대화상자 내부 클릭은 제외)
        if (closeOnOverlayClick && event.target === event.currentTarget && isTop(id)) {
          onClose();
        }
      }}
    >
      <div
        ref={dialogRef}
        role={isDanger ? "alertdialog" : "dialog"}
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descId : undefined}
        tabIndex={-1}
        className={`mn-dialog ${panelClassName} rounded-mn-card border border-mn-border bg-mn-elevated`}
      >
        <div className="flex flex-col gap-2 p-6">
          <h2 id={titleId} className="text-xl font-semibold leading-7 text-mn-text">
            {title}
          </h2>
          {description ? (
            <div id={descId} className="text-sm text-mn-muted">
              {description}
            </div>
          ) : null}
          {children}
        </div>

        {onConfirm ? (
          <div className="flex justify-end gap-2 border-t border-mn-border p-4">
            <Button ref={cancelRef} variant="secondary" onClick={onClose}>
              {cancelLabel}
            </Button>
            <Button
              variant={isDanger ? "danger" : "primary"}
              onClick={onConfirm}
              disabled={confirmDisabled || confirmLoading}
            >
              {confirmLoading ? "Working…" : confirmLabel}
            </Button>
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
