"use client";

/*
 * 시스템 관리 · 라이선스 상태(작업 82-2): GET /api/system/license 를 표시만 한다(변경 기능 없음).
 * 서명·키·장비 값은 서버가 내려주지 않는다. 만료나 계정 수 초과도 표시만 하며 기능을 막지 않는다(서버 정책).
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { Button, StatusDot } from "@/components/mono";
import { Card, Chip, formatWhen, messageOf } from "@/components/v2/admin/AdminUi";
import { getLicense, type LicenseInfo } from "@/lib/v2/admin";
import { isAbortError } from "@/lib/v2/errors";

const STATUS: Record<string, { label: string; tone: "success" | "warning" | "danger" | "muted" }> = {
  valid: { label: "정상", tone: "success" },
  expiring_soon: { label: "만료 임박", tone: "warning" },
  expired: { label: "만료됨", tone: "danger" },
  invalid_signature: { label: "서명 확인 실패", tone: "danger" },
  hardware_mismatch: { label: "장비 불일치", tone: "danger" },
  missing: { label: "라이선스 파일 없음", tone: "muted" },
  malformed: { label: "파일 형식 오류", tone: "danger" },
  public_key_missing: { label: "검증용 공개키 없음", tone: "muted" },
};

type LoadState = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; data: LicenseInfo };

export default function AdminLicensePage() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ kind: "loading" });
    try {
      const data = await getLicense(controller.signal);
      if (!controller.signal.aborted) setState({ kind: "ready", data });
    } catch (error) {
      if (isAbortError(error)) return;
      setState({ kind: "error", message: messageOf(error, "라이선스 상태를 불러오지 못했습니다.") });
    }
  }, []);

  useEffect(() => {
    void load();
    return () => controllerRef.current?.abort();
  }, [load]);

  if (state.kind === "loading") {
    return (
      <p role="status" className="text-sm text-mn-muted">
        라이선스 상태를 불러오는 중…
      </p>
    );
  }
  if (state.kind === "error") {
    return (
      <Card>
        <p role="alert" className="text-sm">
          <StatusDot tone="error" label={state.message} />
        </p>
        <Button size="sm" className="mt-3" onClick={() => void load()}>
          다시 시도
        </Button>
      </Card>
    );
  }

  const info = state.data;
  const status = STATUS[info.status] ?? { label: info.status, tone: "muted" as const };
  const known = info.licensee !== null;
  return (
    <>
      <p className="text-sm text-mn-muted">라이선스(사용 허가) 상태입니다. 이 화면은 확인만 할 수 있습니다.</p>
      <Card label="라이선스 상태">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-base font-semibold">상태</h2>
          <Chip tone={status.tone}>{status.label}</Chip>
        </div>
        {info.message ? <p className="mt-2 break-words text-sm text-mn-muted">{info.message}</p> : null}
        <dl className="mt-4 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
          <dt className="text-mn-muted">발급 대상</dt>
          <dd className="break-words">{known ? info.licensee : "—"}</dd>
          <dt className="text-mn-muted">허용 계정 수</dt>
          <dd className="font-mn-mono">{info.maxAccounts ?? "—"}</dd>
          <dt className="text-mn-muted">현재 활성 계정 수</dt>
          <dd className="font-mn-mono">
            {info.activeAccounts}
            {info.overLimit ? <span className="ml-2"><Chip tone="warning">허용 수 초과</Chip></span> : null}
          </dd>
          <dt className="text-mn-muted">만료일</dt>
          <dd className="break-words">{formatWhen(info.expiresAt)}</dd>
          <dt className="text-mn-muted">남은 일수</dt>
          <dd className="font-mn-mono">{info.daysLeft === null ? "—" : info.daysLeft < 0 ? `${Math.abs(info.daysLeft)}일 지남` : `${info.daysLeft}일`}</dd>
          <dt className="text-mn-muted">장비 일치</dt>
          <dd>{info.hardwareMatch === null ? "확인 안 함" : info.hardwareMatch ? "일치" : "불일치"}</dd>
        </dl>
      </Card>
      {info.warnings.length > 0 ? (
        <Card label="라이선스 경고">
          <ul className="flex flex-col gap-1 pl-5 text-sm">
            {info.warnings.map((warning) => (
              <li key={warning} className="list-disc break-words">
                {warning}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </>
  );
}
