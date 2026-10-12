"use client";

/*
 * 시스템 관리 · 시스템 설정(작업 82-2): 외부 연동 허용 두 스위치. GET/PUT /api/admin/settings 만 쓴다.
 * - 현재 값과 출처(시스템 관리자 설정 / .env 설정값 / 기본값)를 글자로 보여 준다.
 * - 변경은 사유(1~500자) 필수. 켤 때는 먼저 "외부 전송 허용" 경고 확인 창을 거친다. 끌 때는 사유만.
 * - 서버가 정책 적용 스위치(EXTERNAL_POLICY_ENFORCED)를 끄고 있으면(enforced=false) 값이 아직 처리에 적용되지 않는다는 안내를 보인다.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { Button, Modal, StatusDot } from "@/components/mono";
import { ReasonDialog, type ReasonAction } from "@/components/v2/ReasonDialog";
import { Card, Chip, formatWhen, messageOf } from "@/components/v2/admin/AdminUi";
import { getSettings, putSetting, REASON_LIMIT, type AdminSetting, type AdminSettings, type SettingKey, type SettingSource } from "@/lib/v2/admin";
import { isAbortError } from "@/lib/v2/errors";

const META: Record<SettingKey, { title: string; description: string; onWarning: string }> = {
  external_llm_enabled: {
    title: "외부 AI(LLM) 사용 허용",
    description: "Gemini 같은 외부 AI 서비스로 음성·전사 내용을 보내 처리하는 것을 허용합니다.",
    onWarning: "허용하면 회의 음성과 전사 내용이 외부 AI 서비스로 전송됩니다. 협의된 프로젝트만 허용하세요.",
  },
  local_llm_external_host_allowed: {
    title: "로컬 AI 서버의 외부 주소 허용",
    description: "로컬 AI 서버 주소가 사내망(사설망) 밖이어도 연결하는 것을 허용합니다.",
    onWarning: "허용하면 전사 내용이 사내망 밖의 주소로 전송될 수 있습니다. 협의된 프로젝트만 허용하세요.",
  },
};

const SOURCE_LABEL: Record<SettingSource, string> = {
  admin: "시스템 관리자 설정",
  env: ".env 설정값",
  default: "기본값(설정 없음)",
};

type LoadState = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; data: AdminSettings };

export default function AdminSettingsPage() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [warnFor, setWarnFor] = useState<SettingKey | null>(null);
  const [reasonAction, setReasonAction] = useState<ReasonAction | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      const data = await getSettings(controller.signal);
      if (!controller.signal.aborted) setState({ kind: "ready", data });
    } catch (error) {
      if (isAbortError(error)) return;
      setState({ kind: "error", message: messageOf(error, "설정을 불러오지 못했습니다.") });
    }
  }, []);

  useEffect(() => {
    void load();
    return () => controllerRef.current?.abort();
  }, [load]);

  function openReason(setting: AdminSetting) {
    const next = !setting.value;
    const meta = META[setting.key];
    setReasonAction({
      title: next ? "외부 연동 허용" : "외부 연동 허용 해제",
      target: meta.title,
      confirmLabel: next ? "허용하기" : "허용 해제하기",
      requireReason: true,
      notice: `사유는 변경 이력에 남습니다(최대 ${REASON_LIMIT}자).`,
      run: async (reason, signal) => {
        await putSetting(setting.key, next, reason, signal);
        setNotice(`${meta.title}: ${next ? "허용" : "허용 안 함"}으로 변경했습니다.`);
      },
    });
  }

  function onToggle(setting: AdminSetting) {
    if (!setting.value) setWarnFor(setting.key); // 켜는 경우: 경고 확인 창 먼저
    else openReason(setting);
  }

  const settings = state.kind === "ready" ? state.data.items : [];
  const warnSetting = settings.find((s) => s.key === warnFor) ?? null;

  return (
    <>
      <p className="text-sm text-mn-muted">외부 연동은 기본적으로 꺼져 있습니다. 협의된 프로젝트만 시스템 관리자가 켭니다.</p>

      {state.kind === "loading" ? (
        <p role="status" className="text-sm text-mn-muted">
          설정을 불러오는 중…
        </p>
      ) : null}
      {state.kind === "error" ? (
        <Card>
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={state.message} />
          </p>
          <Button size="sm" className="mt-3" onClick={() => void load()}>
            다시 시도
          </Button>
        </Card>
      ) : null}
      {state.kind === "ready" && !state.data.enforced ? (
        <p role="status" className="rounded-mn-card border border-mn-border bg-mn-surface px-4 py-3 text-sm">
          <StatusDot tone="queued" label="정책 적용 스위치(EXTERNAL_POLICY_ENFORCED)가 꺼져 있어 아래 설정은 아직 처리에 적용되지 않습니다. 값은 미리 저장됩니다." />
        </p>
      ) : null}
      {notice ? (
        <p role="status" className="text-sm">
          <StatusDot tone="ready" label={notice} />
        </p>
      ) : null}

      <ul aria-label="시스템 설정 목록" className="flex flex-col gap-3">
        {settings.map((setting) => {
          const meta = META[setting.key];
          return (
            <li key={setting.key} className="min-w-0">
              <Card label={meta.title}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="break-words text-base font-semibold">{meta.title}</h2>
                    <p className="mt-1 break-words text-sm text-mn-muted">{meta.description}</p>
                  </div>
                  <Button
                    size="sm"
                    variant={setting.value ? "danger" : "secondary"}
                    onClick={() => onToggle(setting)}
                    aria-label={`${meta.title} ${setting.value ? "허용 해제" : "허용"}`}
                  >
                    {setting.value ? "허용 해제" : "허용하기"}
                  </Button>
                </div>
                <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
                  <dt className="text-mn-muted">현재 값</dt>
                  <dd>
                    <Chip tone={setting.value ? "warning" : "success"}>{setting.value ? "허용" : "허용 안 함"}</Chip>
                  </dd>
                  <dt className="text-mn-muted">값의 출처</dt>
                  <dd>{SOURCE_LABEL[setting.source] ?? setting.source}</dd>
                  {setting.updatedAt ? (
                    <>
                      <dt className="text-mn-muted">마지막 변경</dt>
                      <dd className="break-words">
                        {formatWhen(setting.updatedAt)}
                        {setting.updatedBy ? ` · ${setting.updatedBy.name}` : ""}
                      </dd>
                    </>
                  ) : null}
                </dl>
              </Card>
            </li>
          );
        })}
      </ul>

      {/* 켤 때 경고 확인 창 */}
      <Modal
        open={warnSetting !== null}
        onClose={() => setWarnFor(null)}
        tone="danger"
        title="외부 전송 허용"
        description={warnSetting ? `대상 ${META[warnSetting.key].title}` : undefined}
        confirmLabel="외부 전송 허용으로 계속"
        cancelLabel="취소"
        onConfirm={() => {
          if (warnSetting) {
            const target = warnSetting;
            setWarnFor(null);
            openReason(target);
          }
        }}
      >
        <p className="mt-3 text-sm text-mn-text">{warnSetting ? META[warnSetting.key].onWarning : ""}</p>
      </Modal>
      <ReasonDialog action={reasonAction} onClose={() => setReasonAction(null)} onDone={load} />
    </>
  );
}
