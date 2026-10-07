"use client";

/*
 * v2 로그인 화면 (시안 docs/ui-v2-mockups/01-login.html)
 * - 아이디·비밀번호 → POST /api/auth/login → 토큰 저장 → next(/v2 아래만) 또는 /v2 로 이동
 * - 이미 로그인 상태면 바로 이동
 * - 실패 문구: 아이디·비밀번호 틀림(401) / 서버 연결 실패. 비밀번호는 어디에도 남기지 않는다.
 */
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useId, useRef, useState, type FormEvent } from "react";

import { Button, StatusDot } from "@/components/mono";
import { useAuth } from "@/components/v2/AuthProvider";
import { ApiError, NetworkError } from "@/lib/v2/errors";
import { safeNextPath } from "@/lib/v2/next-path";

const inputClass =
  "mn-focus h-10 w-full rounded-mn-control border border-mn-control bg-mn-bg px-3 text-sm text-mn-text outline-none disabled:opacity-50";

const MSG_BAD_CREDENTIALS = "아이디 또는 비밀번호가 올바르지 않습니다";
const MSG_NETWORK = "서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요";

function LoginForm() {
  const { status, login } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = safeNextPath(searchParams.get("next"));

  const idInputId = useId();
  const passwordInputId = useId();
  const errorId = useId();
  const idRef = useRef<HTMLInputElement>(null);
  const submittingRef = useRef(false); // 빠른 연타로 두 번 보내는 것을 막는다(상태 갱신 전에도 즉시 반영)

  const [loginId, setLoginId] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 이미 로그인 상태면 원래 가려던 곳으로
  useEffect(() => {
    if (status === "authenticated") router.replace(next);
  }, [status, next, router]);

  useEffect(() => {
    idRef.current?.focus();
  }, []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setError(null);
    try {
      await login(loginId.trim(), password);
      router.replace(next);
    } catch (err) {
      if (err instanceof NetworkError) setError(MSG_NETWORK);
      else if (err instanceof ApiError && err.status !== 401) setError(err.message);
      else setError(MSG_BAD_CREDENTIALS);
      setPassword("");
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-12">
      <form onSubmit={onSubmit} noValidate className="flex w-full max-w-[400px] flex-col gap-6" aria-describedby={error ? errorId : undefined}>
        <div>
          <h1 className="text-[28px] font-semibold leading-9 tracking-tight text-mn-text">회의록 추적</h1>
          <p className="mt-2 text-sm text-mn-muted">회의에서 정한 일을 놓치지 않게 추적합니다.</p>
        </div>

        <div className="flex flex-col gap-4 rounded-mn-card border border-mn-border bg-mn-surface p-6">
          <div className="flex flex-col gap-2">
            <label htmlFor={idInputId} className="text-sm font-medium text-mn-text">
              아이디
            </label>
            <input
              ref={idRef}
              id={idInputId}
              name="username"
              type="text"
              autoComplete="username"
              value={loginId}
              disabled={submitting}
              aria-invalid={error ? true : undefined}
              onChange={(event) => setLoginId(event.target.value)}
              className={inputClass}
            />
          </div>
          <div className="flex flex-col gap-2">
            <label htmlFor={passwordInputId} className="text-sm font-medium text-mn-text">
              비밀번호
            </label>
            <input
              id={passwordInputId}
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              disabled={submitting}
              aria-invalid={error ? true : undefined}
              onChange={(event) => setPassword(event.target.value)}
              className={inputClass}
            />
          </div>

          {error ? (
            <p id={errorId} role="alert" className="text-sm">
              <StatusDot tone="error" label={error} />
            </p>
          ) : null}

          <Button type="submit" variant="primary" disabled={submitting} className="w-full">
            {submitting ? "로그인 중…" : "로그인"}
          </Button>
          <p className="text-xs text-mn-muted">
            계정은 소속 회사 관리자가 발급합니다. 비밀번호는 관리자에게 재설정을 요청하세요.
          </p>
        </div>
      </form>
    </main>
  );
}

export default function V2LoginPage() {
  // useSearchParams 를 쓰는 화면은 Suspense 경계가 필요하다(Next 15)
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
