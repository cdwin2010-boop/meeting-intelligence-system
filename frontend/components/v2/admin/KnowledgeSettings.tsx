"use client";

/*
 * 시스템 설정 · 지식(KMS) 연동 항목(작업 84-1). GET /api/admin/settings 의 knowledge 칸, PUT /api/admin/settings/{key} 만 쓴다.
 * - 스위치(knowledge_integration_enabled): 켤 때 경고 확인(사내 연동이며 외부 AI 허용과 별개) → 사유 필수. 끌 때는 사유만.
 * - 주소(knowledge_api_endpoint): 사유 필수. 사설망·루프백 검사 실패 등 서버 422 문구를 그대로 보인다. 주소 전체는 변경 이력에 남지 않는다(설정 여부만).
 * - 호출 기능이 아직 구현되지 않아(작업 83-1 선반영) 켜도 아직 동작하지 않는다는 안내를 항상 보인다.
 */
import { useEffect, useState } from "react";

import { Button, Modal, StatusDot } from "@/components/mono";
import { ReasonDialog, type ReasonAction } from "@/components/v2/ReasonDialog";
import { Card, Chip, formatWhen, messageOf, TextField } from "@/components/v2/admin/AdminUi";
import { putKnowledgeSetting, REASON_LIMIT, type KnowledgeSetting, type SettingSource } from "@/lib/v2/admin";
import { isAbortError } from "@/lib/v2/errors";

const SOURCE_LABEL: Record<SettingSource, string> = {
  admin: "시스템 관리자 설정",
  env: ".env 설정값",
  default: "기본값(설정 없음)",
};

export const NOT_READY_NOTICE = "지식 연동 호출 기능은 아직 구현되지 않았습니다. 켜도 지금은 동작하지 않습니다.";

export function KnowledgeSettings({
  items,
  onChanged,
  onNotice,
}: {
  items: KnowledgeSetting[];
  onChanged: () => Promise<void>;
  onNotice: (message: string) => void;
}) {
  const enabled = items.find((i) => i.key === "knowledge_integration_enabled");
  const endpoint = items.find((i) => i.key === "knowledge_api_endpoint");
  const [warnOpen, setWarnOpen] = useState(false);
  const [reasonAction, setReasonAction] = useState<ReasonAction | null>(null);
  const [addressOpen, setAddressOpen] = useState(false);

  function openSwitchReason() {
    if (!enabled) return;
    const next = enabled.value !== true;
    setReasonAction({
      title: next ? "지식 연동 켜기" : "지식 연동 끄기",
      target: "지식(KMS) 연동 허용",
      confirmLabel: next ? "연동 켜기" : "연동 끄기",
      requireReason: true,
      notice: `사유는 변경 이력에 남습니다(최대 ${REASON_LIMIT}자).`,
      run: async (reason, signal) => {
        await putKnowledgeSetting("knowledge_integration_enabled", next, reason, signal);
        onNotice(`지식 연동을 ${next ? "켰습니다" : "껐습니다"}.`);
      },
    });
  }

  const on = enabled?.value === true;
  const address = typeof endpoint?.value === "string" ? endpoint.value : "";

  return (
    <section aria-label="지식 연동" className="flex flex-col gap-3">
      <h2 className="text-base font-semibold">지식(KMS) 연동</h2>
      <p role="status" className="rounded-mn-card border border-mn-border bg-mn-surface px-4 py-3 text-sm">
        <StatusDot tone="queued" label={NOT_READY_NOTICE} />
      </p>

      <Card label="지식 연동 허용">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="break-words text-base font-semibold">지식(KMS) 연동 허용</h3>
            <p className="mt-1 break-words text-sm text-mn-muted">
              사내 지식 시스템에서 관련 규정을 조회하는 연동을 허용합니다. 외부 AI(LLM) 허용과는 별개의 설정입니다.
            </p>
          </div>
          <Button
            size="sm"
            variant={on ? "danger" : "secondary"}
            disabled={!enabled}
            onClick={() => (on ? openSwitchReason() : setWarnOpen(true))}
            aria-label={`지식 연동 ${on ? "끄기" : "켜기"}`}
          >
            {on ? "연동 끄기" : "연동 켜기"}
          </Button>
        </div>
        <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
          <dt className="text-mn-muted">현재 값</dt>
          <dd>
            <Chip tone={on ? "warning" : "success"}>{on ? "허용" : "허용 안 함"}</Chip>
          </dd>
          <dt className="text-mn-muted">값의 출처</dt>
          <dd>{enabled ? (SOURCE_LABEL[enabled.source] ?? enabled.source) : "—"}</dd>
          {enabled?.updatedAt ? (
            <>
              <dt className="text-mn-muted">마지막 변경</dt>
              <dd className="break-words">
                {formatWhen(enabled.updatedAt)}
                {enabled.updatedBy ? ` · ${enabled.updatedBy.name}` : ""}
              </dd>
            </>
          ) : null}
        </dl>
      </Card>

      <Card label="지식 연동 주소">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="break-words text-base font-semibold">지식 연동 서버 주소</h3>
            <p className="mt-1 break-words text-sm text-mn-muted">
              사설망·루프백 주소만 입력할 수 있습니다. 주소 전체는 변경 이력에 남지 않고 설정 여부만 기록됩니다.
            </p>
          </div>
          <Button size="sm" disabled={!endpoint} onClick={() => setAddressOpen(true)} aria-label="지식 연동 주소 변경">
            주소 변경
          </Button>
        </div>
        <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
          <dt className="text-mn-muted">현재 주소</dt>
          <dd className="break-all font-mn-mono text-[13px]">{address || "설정 안 됨"}</dd>
          <dt className="text-mn-muted">값의 출처</dt>
          <dd>{endpoint ? (SOURCE_LABEL[endpoint.source] ?? endpoint.source) : "—"}</dd>
          {endpoint?.updatedAt ? (
            <>
              <dt className="text-mn-muted">마지막 변경</dt>
              <dd className="break-words">
                {formatWhen(endpoint.updatedAt)}
                {endpoint.updatedBy ? ` · ${endpoint.updatedBy.name}` : ""}
              </dd>
            </>
          ) : null}
        </dl>
      </Card>

      {/* 켤 때 경고 확인 창 */}
      <Modal
        open={warnOpen}
        onClose={() => setWarnOpen(false)}
        tone="danger"
        title="지식 연동 켜기"
        description="대상 지식(KMS) 연동 허용"
        confirmLabel="사내 연동 허용으로 계속"
        cancelLabel="취소"
        onConfirm={() => {
          setWarnOpen(false);
          openSwitchReason();
        }}
      >
        <div className="mt-3 flex flex-col gap-2 text-sm text-mn-text">
          <p>사내(사설망)의 지식 시스템과 연동하는 설정입니다. 켜면 사용자가 버튼을 눌렀을 때 입력한 검색어가 연동 서버로 전달될 수 있습니다.</p>
          <p>외부 AI(LLM) 허용과는 별개의 설정이며, 이 설정을 켜도 외부 AI 사용이 허용되지는 않습니다.</p>
        </div>
      </Modal>
      <ReasonDialog action={reasonAction} onClose={() => setReasonAction(null)} onDone={onChanged} />
      <EndpointDialog
        open={addressOpen}
        current={address}
        onClose={() => setAddressOpen(false)}
        onDone={async () => {
          onNotice("지식 연동 주소를 변경했습니다.");
          await onChanged();
        }}
      />
    </section>
  );
}

function EndpointDialog({
  open,
  current,
  onClose,
  onDone,
}: {
  open: boolean;
  current: string;
  onClose: () => void;
  onDone: () => Promise<void>;
}) {
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setValue(current);
    setReason("");
    setError(null);
    setSaving(false);
    // 열 때마다 현재 값으로 채운다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const trimmedReason = reason.trim();

  async function submit() {
    if (saving || !trimmedReason) return;
    setSaving(true);
    setError(null);
    try {
      await putKnowledgeSetting("knowledge_api_endpoint", value.trim(), trimmedReason);
      await onDone();
      onClose();
    } catch (err) {
      if (isAbortError(err)) return;
      setError(messageOf(err, "주소를 변경하지 못했습니다.")); // 서버 422 문구(사설망·루프백 검사 등)를 그대로 보인다
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="지식 연동 주소 변경"
      description="사설망·루프백 주소만 입력할 수 있습니다. 비우면 주소를 지웁니다."
      confirmLabel="주소 저장"
      cancelLabel="취소"
      onConfirm={() => void submit()}
      confirmDisabled={!trimmedReason}
      confirmLoading={saving}
    >
      <div className="mt-3 flex flex-col gap-3">
        <TextField label="주소" value={value} onChange={setValue} autoComplete="off" disabled={saving} hint="예: http://192.168.0.10:9000 (외부 주소는 저장되지 않습니다)" />
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <label htmlFor="knowledge-endpoint-reason" className="text-sm font-medium">
              사유 <span className="font-normal text-mn-muted">(필수)</span>
            </label>
            <span className="font-mn-mono text-[13px] text-mn-muted">
              {reason.length} / {REASON_LIMIT}
            </span>
          </div>
          <textarea
            id="knowledge-endpoint-reason"
            value={reason}
            maxLength={REASON_LIMIT}
            rows={3}
            disabled={saving}
            placeholder="변경 사유를 적어 주세요"
            onChange={(event) => {
              setError(null);
              setReason(event.target.value);
            }}
            className="mn-focus w-full rounded-mn-control border border-mn-control bg-mn-bg px-3 py-2 text-sm text-mn-text outline-none placeholder:text-mn-muted disabled:opacity-50"
          />
        </div>
        <p className="text-xs text-mn-muted">주소 전체는 변경 이력에 남지 않습니다. 사유와 설정 여부만 기록됩니다.</p>
        {error ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={error} />
          </p>
        ) : null}
      </div>
    </Modal>
  );
}
