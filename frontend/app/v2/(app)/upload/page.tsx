"use client";

/*
 * v2 회의록 올리기 (시안 docs/ui-v2-mockups/04-upload.html)
 * - 입력: 음성 파일(선택 또는 끌어다 놓기), 회의명(비우면 서버가 파일명으로 채움), 회의 일시(브라우저 현지 시각 → 오프셋 붙은 ISO)
 * - POST /api/meetings/upload(202) 성공 시 /v2/meetings/{meetingId} 로 이동. 처리(전사·추출)는 서버 백그라운드
 * - 허용 형식(415)·용량(413)은 서버 설정이 판정한다. 오류는 서버 문구로 보여 주고 같은 입력으로 다시 시도할 수 있다
 * - 참석자는 같은 고객사 계정 중 여러 명 선택(선택 사항, 목록 조회 실패해도 올리기는 가능)
 * - 회의 유형은 필수(화면에서만 강제, 서버는 선택 입력). 프로젝트 회의면 내가 참여자인 진행 중 프로젝트를 함께 보낸다
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type DragEvent, type FormEvent } from "react";

import { Button, StatusDot } from "@/components/mono";
import { FakeEngineNotice, useEngineIsFake } from "@/components/v2/EngineNotice";
import { FilePicker } from "@/components/v2/FilePicker";
import { MeetingTypeField, ProjectField } from "@/components/v2/MeetingClassFields";
import { detailPath, V2_MEETINGS_PATH } from "@/components/v2/meeting-display";
import { ParticipantPicker } from "@/components/v2/ParticipantPicker";
import { useProcessing } from "@/components/v2/ProcessingProvider";
import { isAbortError } from "@/lib/v2/errors";
import type { MeetingType } from "@/lib/v2/meeting-types";
import { uploadMeeting } from "@/lib/v2/meetings";

const inputClass =
  "mn-focus h-10 w-full rounded-mn-control border border-mn-control bg-mn-bg px-3 text-sm text-mn-text outline-none disabled:opacity-50";

const pad = (n: number) => String(n).padStart(2, "0");

/** 지금 시각(현지)을 date·time 입력 기본값으로 */
function nowParts(): { date: string; time: string } {
  const d = new Date();
  return { date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`, time: `${pad(d.getHours())}:${pad(d.getMinutes())}` };
}

/** 현지 날짜·시각 → 오프셋 붙은 ISO 8601 (예: 2026-10-01T13:20:00+09:00). 잘못된 값이면 null */
function toOffsetIso(date: string, time: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^\d{2}:\d{2}$/.test(time)) return null;
  const local = new Date(`${date}T${time}:00`);
  if (Number.isNaN(local.getTime())) return null;
  const offsetMin = -local.getTimezoneOffset(); // 그 날짜 기준(서머타임 반영)
  const sign = offsetMin >= 0 ? "+" : "-";
  const abs = Math.abs(offsetMin);
  return `${date}T${time}:00${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

export default function V2UploadPage() {
  const router = useRouter();
  const engineIsFake = useEngineIsFake(true);
  const { refresh: refreshProcessing } = useProcessing();
  const titleId = useId();
  const dateId = useId();
  const timeId = useId();
  const fileHintId = useId();
  const controllerRef = useRef<AbortController | null>(null);
  const typeRef = useRef<HTMLInputElement | null>(null);
  const projectRef = useRef<HTMLSelectElement | null>(null);
  const submittingRef = useRef(false); // 빠른 연타로 두 번 보내는 것을 막는다

  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [meetingType, setMeetingType] = useState<MeetingType | "">("");
  const [projectId, setProjectId] = useState("");
  const [participantIds, setParticipantIds] = useState<number[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  // 기본 일시는 화면을 연 시각(서버 렌더링과 어긋나지 않도록 마운트 후 채움)
  useEffect(() => {
    const parts = nowParts();
    setDate(parts.date);
    setTime(parts.time);
  }, []);

  // 화면을 떠나면 진행 중인 전송 취소
  useEffect(() => () => controllerRef.current?.abort(), []);

  function pickFile(next: File | null) {
    setFile(next);
    setError(null);
    setFormError(null);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragOver(false);
    if (submitting) return;
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) pickFile(dropped);
  }

  // 유형을 바꾸면 프로젝트 선택은 초기화한다(프로젝트 회의가 아니면 projectId 를 보내지 않는다)
  function pickType(next: MeetingType) {
    setMeetingType(next);
    setProjectId("");
    setFormError(null);
  }

  async function submit() {
    if (submittingRef.current) return;
    if (!file) {
      setFormError("음성 파일을 선택하세요.");
      return;
    }
    const heldAt = toOffsetIso(date, time);
    if (!heldAt) {
      setFormError("회의 일시를 입력하세요.");
      return;
    }
    if (!meetingType) {
      setFormError("회의 유형을 선택해 주세요");
      typeRef.current?.focus();
      return;
    }
    if (meetingType === "project" && !projectId) {
      setFormError("프로젝트를 선택해 주세요");
      projectRef.current?.focus();
      return;
    }
    setFormError(null);
    setError(null);
    submittingRef.current = true;
    setSubmitting(true);
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      const accepted = await uploadMeeting({
          file,
          title: title.trim(),
          heldAt,
          participantIds,
          meetingType,
          projectId: meetingType === "project" ? Number(projectId) : undefined,
        }, controller.signal);
      refreshProcessing(); // 왼쪽 메뉴 처리 현황에 바로 나타나게 한다(같은 상태 저장소)
      router.push(detailPath(accepted.meetingId));
    } catch (err) {
      if (isAbortError(err)) return;
      setError(err instanceof Error && err.message ? err.message : "올리지 못했습니다.");
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submit();
  }

  return (
    <>
      <header>
        <h1 className="text-[28px] font-semibold leading-9 tracking-tight text-mn-text">회의록 올리기</h1>
        <p className="mt-1 text-sm text-mn-muted">음성 파일을 올리면 회의록과 업무를 자동으로 만듭니다.</p>
      </header>

      <FakeEngineNotice show={engineIsFake} label="업로드 화면 처리 엔진 안내" />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <form
          onSubmit={onSubmit}
          noValidate
          aria-label="회의록 올리기"
          className="flex flex-col gap-5 rounded-mn-card border border-mn-border bg-mn-surface p-6"
        >
          {/* 파일: 끌어다 놓기 영역 + 공용 FilePicker(버튼·상태 글자·숨긴 입력) */}
          <div
            onDragOver={(event) => {
              event.preventDefault();
              if (!submitting) setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            className={[
              "flex flex-col items-center gap-3 rounded-mn-card border border-dashed px-6 py-8 text-center",
              dragOver ? "border-mn-accent bg-mn-elevated" : "border-mn-control",
            ].join(" ")}
          >
            <p className="text-sm font-medium">음성 파일을 끌어다 놓거나 선택하세요</p>
            <p id={fileHintId} className="text-xs text-mn-muted">
              허용 형식과 최대 용량은 서버 설정을 따르며, 벗어나면 올릴 때 알려 드립니다.
            </p>
            <FilePicker
              file={file}
              buttonLabel="음성 파일 선택"
              inputLabel="음성 파일 선택"
              accept="audio/*,video/mp4,video/webm"
              describedBy={fileHintId}
              disabled={submitting}
              className="justify-center"
              onPick={pickFile}
            />
          </div>

          <div className="flex flex-col gap-2">
            <label htmlFor={titleId} className="text-sm font-medium">
              회의명
            </label>
            <input
              id={titleId}
              type="text"
              value={title}
              maxLength={300}
              disabled={submitting}
              placeholder="비워 두면 파일 이름을 씁니다"
              onChange={(event) => setTitle(event.target.value)}
              className={`${inputClass} placeholder:text-mn-muted`}
            />
          </div>

          <fieldset className="flex flex-col gap-2">
            <legend className="mb-2 text-sm font-medium">회의 일시</legend>
            <div className="flex flex-wrap items-center gap-2">
              <label htmlFor={dateId} className="sr-only">
                회의 날짜
              </label>
              <input
                id={dateId}
                type="date"
                value={date}
                required
                disabled={submitting}
                onChange={(event) => setDate(event.target.value)}
                className={`${inputClass} w-[160px] font-mn-mono`}
              />
              <label htmlFor={timeId} className="sr-only">
                회의 시각
              </label>
              <input
                id={timeId}
                type="time"
                value={time}
                required
                disabled={submitting}
                onChange={(event) => setTime(event.target.value)}
                className={`${inputClass} w-[120px] font-mn-mono`}
              />
              <span className="text-xs text-mn-muted">이 컴퓨터의 현지 시각</span>
            </div>
            <span className="text-xs text-mn-muted">상대 날짜(다음 주 화요일 등)를 이 일시 기준으로 계산합니다.</span>
          </fieldset>

          <MeetingTypeField value={meetingType} onChange={pickType} disabled={submitting} firstRef={typeRef} />
          {meetingType === "project" ? <ProjectField value={projectId} onChange={setProjectId} disabled={submitting} selectRef={projectRef} /> : null}

          <ParticipantPicker selected={participantIds} onChange={setParticipantIds} disabled={submitting} />

          {formError ? (
            <p role="alert" className="text-sm">
              <StatusDot tone="error" label={formError} />
            </p>
          ) : null}

          {error ? (
            <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-mn-card border border-mn-border p-4">
              <span className="text-sm">
                <StatusDot tone="error" label={`올리지 못했습니다 · ${error}`} />
              </span>
              <Button size="sm" onClick={() => void submit()}>
                다시 시도
              </Button>
            </div>
          ) : null}

          {submitting ? (
            <p role="status" className="text-sm">
              <StatusDot tone="building" label="올리는 중… 파일 크기에 따라 시간이 걸릴 수 있습니다" />
            </p>
          ) : null}

          <div className="flex justify-end gap-3">
            <Link
              href={V2_MEETINGS_PATH}
              className="mn-focus inline-flex h-10 items-center rounded-mn-control border border-mn-control px-4 text-sm font-medium hover:bg-mn-elevated"
            >
              취소
            </Link>
            <Button type="submit" variant="primary" disabled={submitting}>
              {submitting ? "올리는 중…" : "올리고 분석 시작"}
            </Button>
          </div>
        </form>

        <aside aria-label="올린 뒤 일어나는 일" className="rounded-mn-card border border-mn-border bg-mn-surface p-6">
          <h2 className="text-base font-semibold tracking-tight">올린 뒤 일어나는 일</h2>
          <ol className="mt-4 flex flex-col gap-4 text-sm">
            {[
              ["업로드", "파일을 안전하게 저장합니다."],
              ["전사", "화자와 시간이 붙은 텍스트로 바꿉니다. 보통 몇 분 걸립니다."],
              ["업무 추출", "업무명·담당자·완료 기한과 근거 발언을 뽑습니다."],
              ["확정", "관리자가 확인하면 확정됩니다. 담당자가 올리면 확정 대기가 됩니다."],
            ].map(([name, text], index) => (
              <li key={name} className="flex gap-3">
                <span className="font-mn-mono text-[13px] text-mn-muted">{index + 1}</span>
                <div>
                  <p className="font-medium">{name}</p>
                  <p className="mt-1 text-xs text-mn-muted">{text}</p>
                </div>
              </li>
            ))}
          </ol>
        </aside>
      </div>
    </>
  );
}
