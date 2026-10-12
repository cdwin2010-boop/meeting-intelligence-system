"use client";

/*
 * 본인 비밀번호 변경(작업 82-2): POST /api/auth/change-password.
 * - 현재 비밀번호·새 비밀번호·확인 입력. 새 비밀번호와 확인이 다르면 서버를 부르지 않는다.
 * - 400 = 현재 비밀번호 불일치(서버는 일반 오류만 준다), 422 = 새 비밀번호가 정책에 맞지 않음(서버 문구에 최소 길이가 있다)을 구분해 보인다.
 * - 비밀번호는 입력 상태에만 두고 요청이 끝나면(성공·실패 모두) 비운다. 로그·저장소에 남기지 않는다.
 */
import { useEffect, useState } from "react";

import { Modal, StatusDot } from "@/components/mono";
import { TextField } from "@/components/v2/admin/AdminUi";
import { changeOwnPassword, PASSWORD_MIN_HINT } from "@/lib/v2/admin";
import { ApiError, isAbortError, NetworkError } from "@/lib/v2/errors";

export const MSG_CURRENT_WRONG = "현재 비밀번호가 올바르지 않습니다. 다시 확인해 주세요.";
export const MSG_MISMATCH = "새 비밀번호와 확인이 서로 다릅니다.";

function failureMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 400) return MSG_CURRENT_WRONG;
    if (error.status === 422) {
      // 서버 문구에 최소 글자 수가 들어 있다. 문구가 없으면 기본 안내
      const detail = error.message && error.message !== "입력값을 확인해 주세요." ? error.message : `비밀번호는 ${PASSWORD_MIN_HINT}자 이상이어야 합니다.`;
      return `새 비밀번호가 정책에 맞지 않습니다. ${detail}`;
    }
    return error.message;
  }
  if (error instanceof NetworkError) return error.message;
  return "비밀번호를 변경하지 못했습니다. 잠시 뒤 다시 시도해 주세요.";
}

export function ChangePasswordDialog({
  open,
  onClose,
  onChanged,
}: {
  open: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setCurrent("");
    setNext("");
    setConfirm("");
    setError(null);
    setSaving(false);
  }, [open]);

  async function submit() {
    if (saving || !current || !next) return;
    if (next !== confirm) {
      setError(MSG_MISMATCH);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await changeOwnPassword(current, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      onChanged();
      onClose();
    } catch (err) {
      if (isAbortError(err)) return;
      setError(failureMessage(err));
      setCurrent("");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="비밀번호 변경"
      description="현재 비밀번호를 확인한 뒤 새 비밀번호로 바꿉니다."
      confirmLabel="변경"
      cancelLabel="취소"
      onConfirm={() => void submit()}
      confirmDisabled={!current || !next || !confirm}
      confirmLoading={saving}
    >
      <div className="mt-3 flex flex-col gap-3">
        <TextField label="현재 비밀번호" type="password" value={current} onChange={setCurrent} autoComplete="current-password" required disabled={saving} />
        <TextField
          label="새 비밀번호"
          type="password"
          value={next}
          onChange={setNext}
          autoComplete="new-password"
          required
          disabled={saving}
          hint={`최소 ${PASSWORD_MIN_HINT}자 이상(서버 정책에 따라 더 길 수 있습니다).`}
        />
        <TextField label="새 비밀번호 확인" type="password" value={confirm} onChange={setConfirm} autoComplete="new-password" required disabled={saving} />
        {error ? (
          <p role="alert" className="text-sm">
            <StatusDot tone="error" label={error} />
          </p>
        ) : null}
      </div>
    </Modal>
  );
}
