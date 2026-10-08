"use client";

/*
 * 올리기 화면의 회의 유형(필수 라디오 그룹)과 프로젝트 선택(프로젝트 회의일 때만).
 * - 프로젝트 후보: GET /api/projects 중 "진행 중(active)이면서 내 역할(myRole)이 있는 것"만. myRole 이 null 이면
 *   참여자가 아니므로(관리자 이상이 보는 전체 목록·등록자·승인자) 서버가 거부해 올리기 대상이 아니다
 * - 화면을 떠나거나 다시 불러오면 이전 요청은 AbortController 로 취소한다
 */
import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type Ref } from "react";

import { Button, StatusDot } from "@/components/mono";
import { isAbortError } from "@/lib/v2/errors";
import { MEETING_TYPE_LABEL, MEETING_TYPES, type MeetingType } from "@/lib/v2/meeting-types";
import { listProjects, type ProjectListItem } from "@/lib/v2/projects";

const selectClass =
  "mn-focus h-10 w-full rounded-mn-control border border-mn-control bg-mn-bg px-3 text-sm text-mn-text outline-none disabled:opacity-50";

interface MeetingTypeFieldProps {
  value: MeetingType | "";
  onChange: (value: MeetingType) => void;
  disabled?: boolean;
  /** 첫 라디오(검증 실패 시 포커스를 옮길 곳) */
  firstRef?: Ref<HTMLInputElement>;
}

/** 회의 유형 라디오 그룹. 기본 선택 없음, 같은 name 이라 방향키로 이동한다. 선택은 색 외에 ●/○ 표시와 굵은 글씨로도 보인다 */
export function MeetingTypeField({ value, onChange, disabled = false, firstRef }: MeetingTypeFieldProps) {
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-2 text-sm font-medium">
        회의 유형 <span aria-hidden="true" className="font-normal text-mn-muted">(필수)</span>
      </legend>
      <div className="flex flex-wrap gap-2">
        {MEETING_TYPES.map((code, index) => {
          const checked = value === code;
          return (
            <label
              key={code}
              className={[
                "inline-flex min-h-8 cursor-pointer items-center gap-2 rounded-mn-control border px-3 py-1 text-[13px]",
                "has-[:focus-visible]:[box-shadow:var(--mn-focus-ring)] has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-50",
                checked ? "border-mn-accent bg-mn-elevated font-semibold text-mn-text" : "border-mn-control text-mn-muted hover:text-mn-text",
              ].join(" ")}
            >
              <input
                ref={index === 0 ? firstRef : undefined}
                type="radio"
                name="meetingType"
                value={code}
                checked={checked}
                required
                disabled={disabled}
                onChange={() => onChange(code)}
                className="sr-only"
              />
              <span aria-hidden="true" className="font-mn-mono">
                {checked ? "●" : "○"}
              </span>
              {MEETING_TYPE_LABEL[code]}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

type LoadState = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; projects: ProjectListItem[] };

interface ProjectFieldProps {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  selectRef?: Ref<HTMLSelectElement>;
}

/** 내가 참여자인 진행 중 프로젝트만 이름순으로 고르는 선택 컨트롤(필수) */
export function ProjectField({ value, onChange, disabled = false, selectRef }: ProjectFieldProps) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ kind: "loading" });
    try {
      const all = await listProjects(controller.signal);
      const projects = all
        .filter((p) => p.status === "active" && p.myRole !== null && p.myRole !== undefined)
        .sort((a, b) => a.name.localeCompare(b.name, "ko"));
      setState({ kind: "ready", projects });
    } catch (error) {
      if (isAbortError(error)) return;
      setState({ kind: "error", message: error instanceof Error && error.message ? error.message : "목록을 불러오지 못했습니다." });
    }
  }, []);

  useEffect(() => {
    void load();
    return () => controllerRef.current?.abort();
  }, [load]);

  return (
    <div className="flex flex-col gap-2">
      <label htmlFor="meeting-project" className="text-sm font-medium">
        프로젝트 <span aria-hidden="true" className="font-normal text-mn-muted">(필수)</span>
      </label>
      {state.kind === "loading" ? (
        <p role="status" className="text-sm text-mn-muted">
          프로젝트 목록을 불러오는 중…
        </p>
      ) : state.kind === "error" ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-mn-card border border-mn-border p-3">
          <span className="text-sm">
            <StatusDot tone="error" label={`프로젝트 목록을 불러오지 못했습니다 · ${state.message}`} />
          </span>
          <Button size="sm" onClick={() => void load()}>
            목록 다시 불러오기
          </Button>
        </div>
      ) : state.projects.length === 0 ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-mn-card border border-mn-border p-3">
          <span className="text-sm text-mn-muted">참여 중인 진행 중 프로젝트가 없습니다</span>
          <Link
            href="/v2/projects/new"
            className="mn-focus inline-flex h-8 items-center rounded-mn-control border border-mn-control px-3 text-[13px] font-medium hover:bg-mn-elevated"
          >
            새 프로젝트 등록
          </Link>
        </div>
      ) : (
        <>
          <select
            id="meeting-project"
            ref={selectRef}
            value={value}
            required
            disabled={disabled}
            onChange={(event) => onChange(event.target.value)}
            className={selectClass}
          >
            <option value="">프로젝트를 선택하세요</option>
            {state.projects.map((p) => (
              <option key={p.id} value={String(p.id)}>
                {p.name}
              </option>
            ))}
          </select>
          <Link href="/v2/projects/new" className="mn-focus w-fit text-xs text-mn-muted underline underline-offset-2 hover:text-mn-text">
            새 프로젝트 등록
          </Link>
        </>
      )}
    </div>
  );
}
