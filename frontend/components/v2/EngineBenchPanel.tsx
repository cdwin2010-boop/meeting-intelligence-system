"use client";

/*
 * 원격(시험용) 엔진 연결 점검·벤치마크 측정 영역(작업 L-3a·L-5, 시험 전용). 엔진 시험 화면 안에서 쓴다.
 * 주소·키 입력칸은 위쪽 원격 엔진 설정을 그대로 쓴다(새 입력칸 없음). 측정은 서버가 백그라운드로 실행하고 이 화면은 상태만 조회한다.
 * L-5: 변수 입력(허용 범위·기본값·가이드 상시 표시), 프리셋 A~D, 맥락 스윕, 이전 측정 이력 비교.
 * 변수 정의(기본값·범위·프리셋)는 서버가 내려준 값만 쓴다. 서버가 정의를 주지 못하면 예전 방식(빠른 점검·전체 측정)만 보인다.
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { Button, StatusDot } from "@/components/mono";
import { inputClass, messageOf } from "@/components/v2/admin/AdminUi";
import {
  BENCH_RESULT_FILE_NAME,
  BENCH_STATE_LABEL,
  downloadBenchResult,
  formatElapsed,
  getBenchHistory,
  getBenchModels,
  getBenchParams,
  getBenchStatus,
  resumeBench,
  startBench,
  startBenchWith,
  stopBench,
  type BenchHistoryItem,
  type BenchMode,
  type BenchParams,
  type BenchStatus,
} from "@/lib/v2/engine-bench";
import { fieldError, fmt, modifiedItems, presetSummary, textsOf, toValues, type ParamTexts } from "@/lib/v2/engine-bench-params";
import { ApiError, isAbortError } from "@/lib/v2/errors";

const POLL_MS = 2000;
const DISCONNECT_NOTICE = "연결이 끊겼습니다. 새 주소를 입력하고 이어하기를 눌러 주세요";

function isParams(value: unknown): value is BenchParams {
  return typeof value === "object" && value !== null && Array.isArray((value as BenchParams).params) && Array.isArray((value as BenchParams).presets);
}

function pct(value: number | null): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(1)}%`;
}

function mb(bytes: number | null): string {
  return bytes === null || bytes === undefined ? "—" : `${Math.round(bytes / 1024 / 1024)} MB`;
}

export function EngineBenchPanel() {
  const headingId = useId();
  const [status, setStatus] = useState<BenchStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [candidates, setCandidates] = useState<string[]>([]);
  const [defs, setDefs] = useState<BenchParams | null>(null);
  const [texts, setTexts] = useState<ParamTexts>({});
  const [presetId, setPresetId] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [history, setHistory] = useState<BenchHistoryItem[]>([]);
  const controllerRef = useRef<AbortController | null>(null);
  const previousStatus = useRef<string>("");

  const refresh = useCallback(async () => {
    const controller = new AbortController();
    controllerRef.current?.abort();
    controllerRef.current = controller;
    try {
      setStatus(await getBenchStatus(controller.signal));
    } catch (e) {
      if (!isAbortError(e)) setError(messageOf(e, "측정 상태를 불러오지 못했습니다."));
    }
  }, []);

  const loadHistory = useCallback(async (signal?: AbortSignal) => {
    try {
      const body = await getBenchHistory(signal);
      setHistory(Array.isArray(body?.items) ? body.items : []);
    } catch (e) {
      if (!isAbortError(e)) setHistory([]);
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => controllerRef.current?.abort();
  }, [refresh]);

  // 변수 정의·모델 목록·이력은 한 번 읽는다(정의가 없으면 예전 방식 화면만 쓴다)
  useEffect(() => {
    const controller = new AbortController();
    getBenchParams(controller.signal)
      .then((body) => {
        if (!isParams(body)) return;
        setDefs(body);
        setTexts(textsOf(body.defaults));
      })
      .catch(() => undefined);
    getBenchModels(controller.signal)
      .then((body) => setModels(Array.isArray(body?.models) ? body.models : []))
      .catch(() => undefined);
    void loadHistory(controller.signal);
    return () => controller.abort();
  }, [loadHistory]);

  // 측정 중에는 2초마다 상태를 다시 조회한다(끝나면 멈춘다)
  const running = status?.status === "running";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [running, refresh]);

  // 측정이 끝나면 이력을 다시 읽는다
  useEffect(() => {
    const now = status?.status ?? "";
    if (previousStatus.current === "running" && now !== "running") void loadHistory();
    previousStatus.current = now;
  }, [status?.status, loadHistory]);

  const preset = useMemo(() => defs?.presets.find((p) => p.id === presetId) ?? null, [defs, presetId]);
  // 비활성(맥락을 보낼 수 없는 방식) 변수는 검사하지 않고 기본값으로 둔다
  const activeDefs = useMemo(() => (defs ? defs.params.filter((d) => !d.disabledReason) : []), [defs]);
  const errors = useMemo(() => {
    const out: Record<string, string> = {};
    for (const def of activeDefs) {
      const message = fieldError(def, texts[def.key] ?? "");
      if (message) out[def.key] = message;
    }
    return out;
  }, [activeDefs, texts]);
  const hasErrors = Object.keys(errors).length > 0 || Object.keys(fieldErrors).length > 0;
  const values = useMemo(() => {
    if (!defs) return null;
    const active = toValues(activeDefs, texts);
    if (!active) return null;
    const fixed: Record<string, number> = {};
    for (const def of defs.params) if (def.disabledReason) fixed[def.key] = defs.defaults[def.key];
    return { ...fixed, ...active };
  }, [defs, activeDefs, texts]);
  const summary = useMemo(() => {
    if (!defs || !values) return "";
    return presetSummary(preset, modifiedItems(defs.params, defs.defaults, preset, values));
  }, [defs, values, preset]);

  function applyPreset(id: string) {
    if (!defs) return;
    const next = defs.presets.find((p) => p.id === id);
    if (!next) return;
    const merged: Record<string, number> = { ...defs.defaults, ...next.values };
    // 맥락을 서버에 보낼 수 없는 방식이면 맥락 값은 바꾸지 않는다(입력란이 비활성)
    if (!defs.contextSendable) merged.contextTokens = defs.defaults.contextTokens;
    setTexts((prev) => ({ ...prev, ...textsOf(merged) }));
    setPresetId(id);
    setFieldErrors({});
  }

  function change(key: string, text: string) {
    setTexts((prev) => ({ ...prev, [key]: text }));
    setFieldErrors((prev) => {
      if (!(key in prev)) return prev;
      const { [key]: _removed, ...rest } = prev;
      return rest;
    });
  }

  function resetDefaults() {
    if (!defs) return;
    setTexts(textsOf(defs.defaults));
    setPresetId("");
    setFieldErrors({});
    setModel("");
  }

  async function act(run: () => Promise<BenchStatus>) {
    if (busy) return;
    setBusy(true);
    setError(null);
    setCandidates([]);
    setFieldErrors({});
    try {
      setStatus(await run());
    } catch (e) {
      if (!isAbortError(e)) {
        setError(messageOf(e));
        const body = e instanceof ApiError && typeof e.body === "object" && e.body !== null ? (e.body as { detail?: unknown }).detail : undefined;
        const detail = typeof body === "object" && body !== null ? (body as { candidates?: unknown; errors?: unknown }) : undefined;
        // 시작 직전 연결 진단이 정상이 아니면 서버가 분류·후보를 함께 준다(모델명 후보는 위 설정에서 직접 고른다)
        if (detail && Array.isArray(detail.candidates)) setCandidates(detail.candidates.filter((c): c is string => typeof c === "string"));
        // 범위·조합 오류: 문제 입력란을 빨갛게 표시하고 입력값은 그대로 둔다
        if (detail && Array.isArray(detail.errors)) {
          const next: Record<string, string> = {};
          for (const entry of detail.errors as { key?: unknown; message?: unknown }[]) {
            if (typeof entry.key === "string" && typeof entry.message === "string") next[entry.key] = entry.message;
          }
          setFieldErrors(next);
        }
      }
    } finally {
      setBusy(false);
    }
  }

  function begin(mode: BenchMode, sweep = false) {
    if (!defs) return act(() => startBench(mode));
    if (hasErrors || !values) return Promise.resolve();
    const chosenModel = model.trim();
    return act(() =>
      startBenchWith({ mode, preset: presetId || undefined, values, model: chosenModel || undefined, sweep: sweep || undefined }),
    );
  }

  async function download(runId?: string) {
    setError(null);
    try {
      const blob = await downloadBenchResult(undefined, runId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = runId ? `l3-bench-${runId}.json` : BENCH_RESULT_FILE_NAME;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      if (!isAbortError(e)) setError(messageOf(e, "결과 파일을 내려받지 못했습니다."));
    }
  }

  const state = status?.status ?? "idle";
  const tone = state === "completed" ? "ready" : state === "running" ? "building" : state === "idle" ? "queued" : "error";
  const interrupted = state === "interrupted";
  const blocked = defs !== null && hasErrors;

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3 rounded-mn-card border border-mn-border bg-mn-surface p-5">
      <div className="flex flex-col gap-1">
        <h3 id={headingId} className="text-[15px] font-bold leading-6">
          연결 점검 및 벤치마크 측정
        </h3>
        <p className="text-sm text-mn-muted">
          도구에 내장한 가상 전사본으로 측정합니다(실제 회의 데이터 사용 안 함). 결과 파일에는 건수·시간·비율만 담기고 주소·키는 담기지 않습니다.
        </p>
      </div>

      {defs ? (
        <div className="flex flex-col gap-3" role="group" aria-label="측정 변수">
          <div role="group" aria-label="프리셋" className="flex flex-wrap gap-2">
            {defs.presets.map((p) => (
              <Button
                key={p.id}
                type="button"
                size="sm"
                variant={p.id === presetId ? "primary" : "secondary"}
                aria-pressed={p.id === presetId}
                disabled={busy || running}
                onClick={() => applyPreset(p.id)}
                aria-label={`프리셋 ${p.id} ${p.name}`}
              >
                {p.id} {p.name}
              </Button>
            ))}
            <Button type="button" size="sm" variant="secondary" onClick={resetDefaults} disabled={busy || running} aria-label="기본값으로 되돌리기">
              기본값으로 되돌리기
            </Button>
          </div>
          <p role="status" aria-label="프리셋 상태" className="text-sm text-mn-muted">
            {summary || "입력값을 확인하세요"}
          </p>

          <div className="grid gap-3 md:grid-cols-2">
            {defs.params.map((def) => {
              const message = fieldErrors[def.key] ?? errors[def.key];
              const disabled = Boolean(def.disabledReason) || busy || running;
              const id = `bench-${def.key}`;
              return (
                <div key={def.key} className="flex min-w-0 flex-col gap-1 text-sm">
                  <label htmlFor={id} className="font-medium">
                    {def.label} <span className="text-xs text-mn-muted">({def.unit})</span>
                  </label>
                  <input
                    id={id}
                    type="number"
                    inputMode="numeric"
                    min={def.min}
                    max={def.max}
                    step={def.type === "int" ? 1 : "any"}
                    value={texts[def.key] ?? ""}
                    disabled={disabled}
                    aria-invalid={message ? true : undefined}
                    aria-describedby={`${id}-hint`}
                    onChange={(event) => change(def.key, event.target.value)}
                    className={`${inputClass} font-mn-mono ${message ? "border-mn-red" : ""}`}
                  />
                  <p id={`${id}-hint`} className="text-xs text-mn-muted">
                    허용 {fmt(def.min)}~{fmt(def.max)}, 기본 {fmt(def.default)}
                    {def.autoValue !== null ? "(0 = 자동)" : ""} · {def.guide}
                  </p>
                  {def.disabledReason ? <p className="text-xs">{def.disabledReason}</p> : null}
                  {message ? (
                    <p role="alert" className="text-xs">
                      <StatusDot tone="error" label={message} />
                    </p>
                  ) : null}
                </div>
              );
            })}
            <div className="flex min-w-0 flex-col gap-1 text-sm">
              <label htmlFor="bench-model" className="font-medium">
                측정 모델
              </label>
              <select
                id="bench-model"
                value={model}
                disabled={busy || running || models.length === 0}
                onChange={(event) => setModel(event.target.value)}
                className={inputClass}
              >
                <option value="">저장된 설정의 모델 사용</option>
                {models.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
              <p className="text-xs text-mn-muted">
                {models.length === 0 ? "서버 모델 목록을 불러오지 못했습니다. 위 설정에서 모델명을 저장하세요." : "서버 목록에서 고릅니다."}
              </p>
            </div>
          </div>
        </div>
      ) : null}

      <div className="flex flex-wrap gap-2">
        <Button onClick={() => void begin("quick")} disabled={busy || running || blocked} aria-label="빠른 점검">
          빠른 점검(약 5분)
        </Button>
        <Button onClick={() => void begin("full")} disabled={busy || running || blocked} aria-label="전체 측정">
          전체 측정
        </Button>
        {defs ? (
          <Button onClick={() => void begin("full", true)} disabled={busy || running || blocked || !defs.sweepEnabled} aria-label="맥락 스윕">
            맥락 스윕({defs.sweepOrder.join("→")})
          </Button>
        ) : null}
        <Button onClick={() => void act(() => resumeBench())} disabled={busy || running || !status?.canResume} aria-label="이어하기">
          이어하기
        </Button>
        <Button onClick={() => void act(() => stopBench())} disabled={busy || !running} aria-label="중단">
          중단
        </Button>
      </div>
      {defs && !defs.sweepEnabled ? <p className="text-xs text-mn-muted">맥락 스윕 비활성: {defs.sweepDisabledReason}</p> : null}

      <div aria-label="측정 진행 상태" role="status" className="flex flex-col gap-1 text-sm">
        <StatusDot tone={tone} label={BENCH_STATE_LABEL[state]} />
        {status && state !== "idle" ? (
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1">
            <dt className="text-mn-muted">현재 항목</dt>
            <dd>{status.currentLabel || "—"}</dd>
            <dt className="text-mn-muted">완료</dt>
            <dd className="font-mn-mono text-[13px]">
              {status.doneUnits}/{status.totalUnits}
            </dd>
            <dt className="text-mn-muted">경과 시간</dt>
            <dd className="font-mn-mono text-[13px]">{formatElapsed(status.elapsedSeconds)}</dd>
            {status.settingsSummary ? (
              <>
                <dt className="text-mn-muted">설정</dt>
                <dd>{status.settingsSummary}</dd>
              </>
            ) : null}
            {status.sweepTotal ? (
              <>
                <dt className="text-mn-muted">맥락 스윕</dt>
                <dd className="font-mn-mono text-[13px]">
                  {status.sweepIndex}/{status.sweepTotal}
                </dd>
              </>
            ) : null}
          </dl>
        ) : null}
        {interrupted ? <p role="alert">{status?.message || DISCONNECT_NOTICE}</p> : null}
        {!interrupted && status?.message ? <p className="text-mn-muted">{status.message}</p> : null}
      </div>

      {error ? (
        <p role="alert" className="text-sm">
          <StatusDot tone="error" label={error} />
        </p>
      ) : null}
      {candidates.length > 0 ? (
        <p className="text-sm text-mn-muted">
          가장 가까운 모델명: <span className="font-mn-mono text-[13px]">{candidates.join(", ")}</span> — 위 원격 설정에서 모델명을 바꿔 저장한 뒤 다시 시작하세요.
        </p>
      ) : null}

      <div>
        <Button onClick={() => void download()} disabled={!status?.canDownload} aria-label="결과 파일 내려받기">
          결과 파일 내려받기
        </Button>
      </div>

      {history.length > 0 ? (
        <div className="flex flex-col gap-2">
          <h4 className="text-sm font-bold">이전 측정 비교</h4>
          <div className="overflow-x-auto">
            <table aria-label="이전 측정 비교" className="w-full min-w-[720px] text-left text-[13px]">
              <thead className="text-mn-muted">
                <tr>
                  <th className="py-1 pr-3 font-normal">시작</th>
                  <th className="py-1 pr-3 font-normal">프리셋</th>
                  <th className="py-1 pr-3 font-normal">맥락</th>
                  <th className="py-1 pr-3 font-normal">모델</th>
                  <th className="py-1 pr-3 font-normal">완료 항목</th>
                  <th className="py-1 pr-3 font-normal">속도(토큰/초)</th>
                  <th className="py-1 pr-3 font-normal">VRAM</th>
                  <th className="py-1 pr-3 font-normal">근거 검증률</th>
                  <th className="py-1 pr-3 font-normal">경고</th>
                  <th className="py-1 font-normal">
                    <span className="sr-only">내려받기</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {history.map((row) => (
                  <tr key={row.runId} className="border-t border-mn-border">
                    <td className="py-1 pr-3 font-mn-mono">{row.startedAt.replace("T", " ").slice(0, 16)}</td>
                    <td className="py-1 pr-3">{row.presetSummary || row.preset || "—"}</td>
                    <td className="py-1 pr-3 font-mn-mono">{row.contextTokens ?? "—"}</td>
                    <td className="py-1 pr-3 font-mn-mono">{row.model || "—"}</td>
                    <td className="py-1 pr-3 font-mn-mono">
                      {row.completedUnits}/{row.totalUnits}
                    </td>
                    <td className="py-1 pr-3 font-mn-mono">{row.tokensPerSecond === null ? "—" : row.tokensPerSecond.toFixed(1)}</td>
                    <td className="py-1 pr-3 font-mn-mono">{mb(row.vramBytes)}</td>
                    <td className="py-1 pr-3 font-mn-mono">{pct(row.verifiedRate)}</td>
                    <td className="py-1 pr-3 font-mn-mono">{row.warningCount > 0 ? `${row.warningCount}건` : "없음"}</td>
                    <td className="py-1">
                      <Button type="button" size="sm" variant="secondary" onClick={() => void download(row.runId)} aria-label={`측정 ${row.runId} 결과 내려받기`}>
                        내려받기
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </section>
  );
}
