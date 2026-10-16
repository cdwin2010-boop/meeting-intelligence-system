"use client";

/*
 * 엔진 시험 설정 화면 본문(작업 91-1, 시험 전용): 원격(OpenAI 호환 HTTP) 엔진의 주소·모델·키·타임아웃·청크 한도 저장과 로딩 테스트.
 * 시험 모드가 꺼져 있으면(서버 403 포함) 아무것도 그리지 않는다. 키는 입력만 받고 화면에는 저장 여부·마스킹만 보인다.
 * 라우트 두 곳(/v2/engine-test: 관리자 이상, /v2/admin/engine-test: 시스템 관리자)이 이 컴포넌트를 쓴다.
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { Button, StatusDot } from "@/components/mono";
import { inputClass, messageOf } from "@/components/v2/admin/AdminUi";
import { useEngineTestMode } from "@/components/v2/EngineChoice";
import { EngineBenchPanel } from "@/components/v2/EngineBenchPanel";
import { isAbortError } from "@/lib/v2/errors";
import {
  CONNECTION_TYPE_LABEL,
  DIAGNOSE_LABEL,
  ENGINE_TEST_NOTICE,
  getRemoteSettings,
  runDiagnose,
  runLoadTest,
  saveRemoteSettings,
  type ConnectionType,
  type DiagnoseResult,
  type LoadTestResult,
  type RemoteSettings,
} from "@/lib/v2/engine-test";

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-sm">
      <span className="font-medium">{label}</span>
      {children}
      {hint ? <span className="text-xs text-mn-muted">{hint}</span> : null}
    </label>
  );
}

export function EngineTestPanel() {
  const { testMode } = useEngineTestMode();
  const headingId = useId();
  const [loaded, setLoaded] = useState<RemoteSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [connectionType, setConnectionType] = useState<ConnectionType>("openai");
  const [diagnosing, setDiagnosing] = useState(false);
  const [diagnosis, setDiagnosis] = useState<DiagnoseResult | null>(null);
  const [diagnoseError, setDiagnoseError] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState("300");
  const [chunkMaxChars, setChunkMaxChars] = useState("0");
  const [saving, setSaving] = useState(false);
  const [saveNotice, setSaveNotice] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<LoadTestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const apply = useCallback((settings: RemoteSettings) => {
    setLoaded(settings);
    setBaseUrl(settings.baseUrl);
    setModel(settings.model);
    setConnectionType(settings.connectionType === "ollama" ? "ollama" : "openai");
    setTimeoutSeconds(String(settings.timeoutSeconds));
    setChunkMaxChars(String(settings.chunkMaxChars));
    setApiKey("");
  }, []);

  useEffect(() => {
    if (!testMode) return;
    const controller = new AbortController();
    getRemoteSettings(controller.signal)
      .then(apply)
      .catch((error) => {
        if (!isAbortError(error)) setLoadError(messageOf(error, "설정을 불러오지 못했습니다."));
      });
    return () => controller.abort();
  }, [testMode, apply]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  if (!testMode) return null;

  async function save() {
    if (saving) return;
    setSaving(true);
    setSaveNotice(null);
    setSaveError(null);
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      const saved = await saveRemoteSettings(
        {
          baseUrl: baseUrl.trim(),
          model: model.trim(),
          apiKey: apiKey.trim() || undefined,
          timeoutSeconds: Number(timeoutSeconds),
          chunkMaxChars: Number(chunkMaxChars) || 0,
          // 서버가 연결 방식을 내려주지 않은 구버전이고 값이 기본(openai)이면 보내지 않는다
          connectionType: loaded?.connectionType === undefined && connectionType === "openai" ? undefined : connectionType,
        },
        controller.signal,
      );
      apply(saved);
      setSaveNotice("저장했습니다.");
    } catch (error) {
      if (!isAbortError(error)) setSaveError(messageOf(error));
    } finally {
      setSaving(false);
    }
  }

  async function diagnose() {
    if (diagnosing) return;
    setDiagnosing(true);
    setDiagnosis(null);
    setDiagnoseError(null);
    try {
      setDiagnosis(await runDiagnose());
    } catch (error) {
      if (!isAbortError(error)) setDiagnoseError(messageOf(error, "연결 진단을 하지 못했습니다."));
    } finally {
      setDiagnosing(false);
    }
  }

  async function loadTest() {
    if (testing) return;
    setTesting(true);
    setResult(null);
    setTestError(null);
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      setResult(await runLoadTest(controller.signal));
    } catch (error) {
      if (!isAbortError(error)) setTestError(messageOf(error, "로딩 테스트를 하지 못했습니다."));
    } finally {
      setTesting(false);
    }
  }

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 id={headingId} className="text-[17px] font-bold leading-6">
          원격(시험용) 엔진
        </h2>
        <p role="note" className="text-sm text-mn-muted">
          OpenAI 호환 HTTP 서버를 업무 추출 엔진으로 시험합니다. 시험 전용이며 납품 설정에서는 꺼집니다.
        </p>
      </div>
      <p role="status" aria-label="시험 전용 안내" className="rounded-mn-card border border-mn-border bg-mn-surface px-4 py-3 text-sm">
        <StatusDot tone="queued" label={ENGINE_TEST_NOTICE} />
      </p>

      {loadError ? (
        <p role="alert" className="text-sm">
          <StatusDot tone="error" label={loadError} />
        </p>
      ) : null}

      <form
        aria-label="원격 엔진 설정"
        className="flex flex-col gap-4 rounded-mn-card border border-mn-border bg-mn-surface p-5"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <Field
          label="연결 방식"
          hint={
            connectionType === "ollama"
              ? "Ollama 네이티브 API(/api/chat)로 맥락 길이(num_ctx)를 서버에 직접 보냅니다. 주소는 서버 주소만(끝의 /v1 은 자동으로 뗍니다)."
              : "OpenAI 호환 /v1 방식은 맥락 길이를 서버에 보낼 수 없습니다."
          }
        >
          <select
            value={connectionType}
            onChange={(event) => setConnectionType(event.target.value === "ollama" ? "ollama" : "openai")}
            className={inputClass}
            aria-label="연결 방식"
          >
            {(Object.keys(CONNECTION_TYPE_LABEL) as ConnectionType[]).map((key) => (
              <option key={key} value={key}>
                {CONNECTION_TYPE_LABEL[key]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="서버 주소" hint="예: https://…/v1 (터널을 열 때마다 바뀌면 여기서 바꿉니다)">
          <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} className={`${inputClass} font-mn-mono`} autoComplete="off" spellCheck={false} />
        </Field>
        <Field label="모델명">
          <input value={model} onChange={(event) => setModel(event.target.value)} className={`${inputClass} font-mn-mono`} autoComplete="off" spellCheck={false} />
        </Field>
        <Field
          label="API 키"
          hint={loaded?.apiKeySet ? `저장된 키가 있습니다(${loaded.apiKeyMasked}). 비워 두면 그대로 유지합니다.` : "필요한 서버만 입력합니다."}
        >
          <input
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            className={`${inputClass} font-mn-mono`}
            autoComplete="new-password"
            placeholder={loaded?.apiKeySet ? loaded.apiKeyMasked : ""}
          />
        </Field>
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="요청 타임아웃(초)">
            <input
              inputMode="numeric"
              value={timeoutSeconds}
              onChange={(event) => setTimeoutSeconds(event.target.value)}
              className={`${inputClass} font-mn-mono`}
            />
          </Field>
          <Field label="청크 한도(글자)" hint="0 이면 기본 청크 한도를 씁니다.">
            <input
              inputMode="numeric"
              value={chunkMaxChars}
              onChange={(event) => setChunkMaxChars(event.target.value)}
              className={`${inputClass} font-mn-mono`}
            />
          </Field>
        </div>
        {saveError ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={saveError} />
          </p>
        ) : null}
        {saveNotice ? (
          <p role="status" className="text-sm">
            <StatusDot tone="ready" label={saveNotice} />
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" disabled={saving}>
            {saving ? "저장 중…" : "저장"}
          </Button>
          <Button onClick={() => void loadTest()} disabled={testing || saving} aria-label="로딩 테스트">
            {testing ? "테스트 중…" : "로딩 테스트"}
          </Button>
          <Button onClick={() => void diagnose()} disabled={diagnosing || saving} aria-label="연결 진단">
            {diagnosing ? "진단 중…" : "연결 진단"}
          </Button>
        </div>
      </form>

      {diagnoseError ? (
        <p role="alert" className="text-sm">
          <StatusDot tone="error" label={diagnoseError} />
        </p>
      ) : null}
      {diagnosis ? (
        <section aria-label="연결 진단 결과" className="flex flex-col gap-2 rounded-mn-card border border-mn-border bg-mn-surface p-4">
          <p role="status" className="text-sm">
            <StatusDot
              tone={diagnosis.category === "OK" ? "ready" : "error"}
              label={`${DIAGNOSE_LABEL[diagnosis.category]} · ${diagnosis.hint}`}
            />
          </p>
          <p className="font-mn-mono text-[13px] text-mn-muted">
            {diagnosis.httpStatus === null ? "HTTP —" : `HTTP ${diagnosis.httpStatus}`} · {diagnosis.elapsedMs} ms
          </p>
          {diagnosis.category === "MODEL_MISSING" && diagnosis.candidates.length > 0 ? (
            <div role="group" aria-label="모델명 후보" className="flex flex-col gap-2">
              <p className="text-sm text-mn-muted">가장 가까운 모델명입니다. 선택하면 위 모델명 칸에 채워지며, 저장해야 적용됩니다.</p>
              <div className="flex flex-wrap gap-2">
                {diagnosis.candidates.map((name) => (
                  <Button key={name} type="button" size="sm" variant="secondary" onClick={() => setModel(name)} aria-label={`후보 ${name}`}>
                    {name}
                  </Button>
                ))}
              </div>
            </div>
          ) : null}
        </section>
      ) : null}

      {testError ? (
        <p role="alert" className="text-sm">
          <StatusDot tone="error" label={testError} />
        </p>
      ) : null}
      {result ? (
        <section aria-label="로딩 테스트 결과" className="flex flex-col gap-3 rounded-mn-card border border-mn-border bg-mn-surface p-5">
          <p role="status" className="text-sm font-medium">
            <StatusDot tone={result.ok ? "ready" : "error"} label={result.ok ? "로딩 테스트 성공" : "로딩 테스트 실패"} />
          </p>
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
            <dt className="text-mn-muted">모델 목록 조회</dt>
            <dd>{result.modelListed ? "성공" : "실패"}</dd>
            <dt className="text-mn-muted">현재 모델명</dt>
            <dd className="break-all font-mn-mono text-[13px]">
              {result.currentModel || "—"}
              {result.modelListed ? (result.modelFound ? " (서버 목록에 있음)" : " (서버 목록에 없음)") : ""}
            </dd>
            <dt className="text-mn-muted">응답 시간</dt>
            <dd className="font-mn-mono text-[13px]">{result.responseMs === null ? "—" : `${result.responseMs} ms`}</dd>
            <dt className="text-mn-muted">JSON 파싱</dt>
            <dd>{result.jsonParsed ? "성공" : "실패"}</dd>
            {result.failureReason ? (
              <>
                <dt className="text-mn-muted">실패 사유</dt>
                <dd className="break-words">{result.failureReason}</dd>
              </>
            ) : null}
          </dl>
        </section>
      ) : null}
      <EngineBenchPanel />
    </section>
  );
}
