"use client";

/*
 * 회의록 5개 항목(목적·주요 논의사항·결정사항·리스크·다음 안건) 표시와 직권 수정 팝업.
 * - AI 가 자동 생성하고 정보가 없으면 서버가 "내용없음"을 준다. 5개 항목이 아직 없는 기존 회의록(engine·수정 기록 모두 없음)은
 *   "아직 생성되지 않았습니다"로 안내하고, 직권 수정으로 채울 수 있다.
 * - "항목 수정"은 허용 동작(edit_minutes)이 있을 때만 보이고(서버 판정, 거부되면 서버 문구), 확정 이후에도 수정할 수 있다.
 * - 팝업은 바뀐 항목만 보낸다. 비우면 서버가 "내용없음"으로 저장한다. 글자 수는 보여 주기만 하고 제한은 서버 판정에 맡긴다.
 *   저장 뒤 어느 항목이 바뀌었는지는 서버 응답을 저장 전 값과 비교해 알려 준다.
 */
import { useEffect, useId, useRef, useState } from "react";

import { Button, Modal, StatusDot } from "@/components/mono";
import { formatDateTime } from "@/components/v2/meeting-display";
import { isAbortError } from "@/lib/v2/errors";
import { MINUTES_EMPTY, MINUTES_FIELDS, MINUTES_LABEL, updateMinutes, type Minutes, type MinutesField } from "@/lib/v2/meetings";

const errorMessage = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

/** 5개 항목이 아직 만들어지지 않은 회의록인지(처리 엔진 기록도 직권 수정 기록도 없음) */
export const minutesNotGenerated = (minutes: Minutes | undefined): boolean =>
  !minutes || (minutes.engine === null && minutes.updatedAt === null);

interface MinutesPanelProps {
  minutes: Minutes | undefined;
  /** 항목 수정 허용(edit_minutes) */
  canEdit: boolean;
  /** 저장 결과 안내(예: 변경된 항목 이름) */
  notice: string | null;
  onEdit: () => void;
}

export function MinutesPanel({ minutes, canEdit, notice, onEdit }: MinutesPanelProps) {
  const missing = minutesNotGenerated(minutes);
  return (
    <section aria-label="회의록 항목" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-xs text-mn-muted">회의록 항목</h2>
        {canEdit ? (
          <Button size="sm" onClick={onEdit}>
            항목 수정
          </Button>
        ) : null}
      </div>
      {missing ? (
        <p className="text-sm text-mn-muted">아직 생성되지 않았습니다.{canEdit ? " 항목 수정으로 직접 채울 수 있습니다." : ""}</p>
      ) : (
        <dl className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {MINUTES_FIELDS.map((field) => {
            const value = (minutes?.[field] ?? "").trim();
            return (
              <div key={field}>
                <dt className="text-xs text-mn-muted">{MINUTES_LABEL[field]}</dt>
                <dd className="mt-1 whitespace-pre-line text-sm">{value && value !== MINUTES_EMPTY ? value : <span className="text-mn-muted">{MINUTES_EMPTY}</span>}</dd>
              </div>
            );
          })}
        </dl>
      )}
      {minutes && !missing ? (
        <p className="text-xs text-mn-muted">
          {minutes.engine ? `생성 엔진 ${minutes.engine}` : "직접 작성"}
          {minutes.updatedBy && minutes.updatedAt ? ` · 직권 수정 ${minutes.updatedBy.name} ${formatDateTime(minutes.updatedAt)}` : ""}
        </p>
      ) : null}
      <div aria-live="polite" className="text-xs empty:hidden">
        {notice ? <StatusDot tone="ready" label={notice} /> : null}
      </div>
    </section>
  );
}

interface MinutesDialogProps {
  meetingId: number;
  open: boolean;
  /** 현재 5개 항목(없으면 모두 비어 있는 것으로) */
  minutes: Minutes | undefined;
  onClose: () => void;
  /** 저장 성공: 서버가 돌려준 항목과 바뀐 항목 이름 */
  onSaved: (saved: Minutes, changedLabels: string[]) => void;
}

const initialOf = (minutes: Minutes | undefined, field: MinutesField): string => {
  const value = minutes?.[field] ?? "";
  return value === MINUTES_EMPTY ? "" : value;
};

export function MinutesDialog({ meetingId, open, minutes, onClose, onSaved }: MinutesDialogProps) {
  const baseId = useId();
  const [values, setValues] = useState<Record<MinutesField, string>>(() => emptyValues());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const runRef = useRef<AbortController | null>(null);

  // 열 때마다 현재 값으로 채운다("내용없음"은 빈 칸으로 보여 준다)
  useEffect(() => {
    if (!open) return;
    setValues(Object.fromEntries(MINUTES_FIELDS.map((f) => [f, initialOf(minutes, f)])) as Record<MinutesField, string>);
    setError(null);
    setSaving(false);
    return () => runRef.current?.abort();
    // 열릴 때만 채운다(저장 중 부모가 minutes 를 바꿔도 입력을 덮어쓰지 않는다)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const changed = MINUTES_FIELDS.filter((f) => values[f].trim() !== initialOf(minutes, f).trim());

  async function onConfirm() {
    if (saving || changed.length === 0) return;
    runRef.current?.abort();
    const controller = new AbortController();
    runRef.current = controller;
    setSaving(true);
    setError(null);
    try {
      // 바뀐 항목만 보낸다
      const saved = await updateMinutes(meetingId, Object.fromEntries(changed.map((f) => [f, values[f]])), controller.signal);
      const labels = MINUTES_FIELDS.filter((f) => (saved[f] ?? "") !== (minutes?.[f] ?? MINUTES_EMPTY)).map((f) => MINUTES_LABEL[f]);
      setSaving(false);
      onSaved(saved, labels);
    } catch (err) {
      if (isAbortError(err)) return;
      // 입력값은 그대로 두고 서버 문구를 보여 준다
      setError(errorMessage(err, "서버 오류"));
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="회의록 항목 수정"
      description={"직권 수정: 바뀐 항목만 저장되고 변경 이력에 남습니다. 항목을 비우면 \"내용없음\"으로 저장됩니다."}
      confirmLabel="항목 저장"
      cancelLabel="취소"
      initialFocus="cancel"
      onConfirm={() => void onConfirm()}
      confirmDisabled={changed.length === 0}
      confirmLoading={saving}
      panelClassName="w-full max-w-2xl"
    >
      <div className="mt-3 flex max-h-[60vh] flex-col gap-4 overflow-y-auto pr-1">
        {MINUTES_FIELDS.map((field) => (
          <div key={field} className="flex flex-col gap-1">
            <div className="flex items-center justify-between">
              <label htmlFor={`${baseId}-${field}`} className="text-sm font-medium">
                {MINUTES_LABEL[field]}
              </label>
              <span className="font-mn-mono text-[13px] text-mn-muted">{values[field].length}자</span>
            </div>
            <textarea
              id={`${baseId}-${field}`}
              value={values[field]}
              rows={3}
              disabled={saving}
              placeholder={MINUTES_EMPTY}
              onChange={(event) => {
                setError(null);
                setValues((prev) => ({ ...prev, [field]: event.target.value }));
              }}
              className="mn-focus w-full rounded-mn-control border border-mn-control bg-mn-bg px-3 py-2 text-sm text-mn-text outline-none placeholder:text-mn-muted disabled:opacity-50"
            />
          </div>
        ))}
        {error ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={`저장하지 못했습니다 · ${error}`} />
          </p>
        ) : null}
      </div>
    </Modal>
  );
}

function emptyValues(): Record<MinutesField, string> {
  return Object.fromEntries(MINUTES_FIELDS.map((f) => [f, ""])) as Record<MinutesField, string>;
}
