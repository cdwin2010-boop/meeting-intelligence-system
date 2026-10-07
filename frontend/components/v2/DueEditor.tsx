"use client";

/*
 * 업무 행의 기한 칸: 값 표시 + "기한 설정/변경" 인라인 편집(날짜 선택 또는 "미확정").
 * 재개 뒤 비워진 기한을 다시 정할 때 쓴다. 입력 여부만 확인하고 값의 옳고 그름은 서버가 판정하며,
 * 거부(403 권한 없음·409 종결 등)는 서버 문구를 그대로 보여 준다(권한 값이 응답에 없어 버튼은 숨기지 않는다).
 */
import { useEffect, useId, useRef, useState } from "react";

import { Badge, Button, StatusDot } from "@/components/mono";
import { updateDue } from "@/lib/v2/action-items";
import { isAbortError } from "@/lib/v2/errors";
import type { ActionItem } from "@/lib/v2/meetings";

interface DueEditorProps {
  item: ActionItem;
  /** 기한 입력 허용(set_due). 아니면 값만 보여 준다 */
  editable: boolean;
  onSaved: (saved: ActionItem) => void;
}

export function DueEditor({ item, editable, onSaved }: DueEditorProps) {
  const [editing, setEditing] = useState(false);
  const [date, setDate] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const inputId = useId();
  const label = item.title || "업무명 없음";
  useEffect(() => () => controllerRef.current?.abort(), []);

  function open() {
    setDate(item.dueDate ?? "");
    setError(null);
    setEditing(true);
  }

  async function save(due: { dueDate: string } | { dueUndetermined: true }) {
    if ("dueDate" in due && !due.dueDate) {
      setError("날짜를 선택하거나 '미확정'을 누르세요");
      return;
    }
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setSaving(true);
    setError(null);
    try {
      const saved = await updateDue(item.id, due, controller.signal);
      setEditing(false);
      onSaved(saved);
    } catch (e) {
      if (isAbortError(e)) return;
      setError(e instanceof Error && e.message ? e.message : "서버 오류");
    } finally {
      setSaving(false);
    }
  }

  const value = item.dueUndetermined ? (
    <Badge>미확정</Badge>
  ) : item.dueDate ? (
    <span className="whitespace-nowrap font-mn-mono text-[13px]">{item.dueDate}</span>
  ) : (
    <span className="text-mn-muted">—</span>
  );

  if (!editable) return value;

  if (!editing) {
    return (
      <span className="flex flex-wrap items-center justify-between gap-2 py-2">
        {value}
        <Button size="sm" aria-label={`${label} 기한 ${item.dueDate || item.dueUndetermined ? "변경" : "설정"}`} onClick={open}>
          {item.dueDate || item.dueUndetermined ? "변경" : "설정"}
        </Button>
      </span>
    );
  }

  return (
    <span className="flex flex-col gap-2 py-2">
      <label htmlFor={inputId} className="sr-only">
        {label} 완료 기한
      </label>
      <input
        id={inputId}
        type="date"
        value={date}
        disabled={saving}
        onChange={(event) => setDate(event.target.value)}
        className="mn-focus h-9 w-full rounded-mn-control border border-mn-border bg-mn-bg px-2 font-mn-mono text-sm text-mn-text outline-none disabled:opacity-50"
      />
      <span className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={saving} aria-label={`${label} 기한 저장`} onClick={() => void save({ dueDate: date })}>
          {saving ? "저장 중…" : "저장"}
        </Button>
        <Button size="sm" disabled={saving} aria-label={`${label} 기한 미확정`} onClick={() => void save({ dueUndetermined: true })}>
          미확정
        </Button>
        <Button size="sm" disabled={saving} aria-label={`${label} 기한 입력 취소`} onClick={() => setEditing(false)}>
          취소
        </Button>
      </span>
      {error ? (
        <span role="alert" className="text-xs">
          <StatusDot tone="error" label={error} />
        </span>
      ) : null}
    </span>
  );
}
