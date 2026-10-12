"use client";

/*
 * 승인 대기 패널(작업 82-2): 오른쪽 패널에서 "내가 처리해야 할 승인·결정"을 한눈에 보여 준다.
 * - 프로젝트 승인: GET /api/projects 중 승인 대기(pending_approval)이고 승인자가 나인 것 → 프로젝트 상세(승인·반려는 상세에서)
 * - 수정·대체 요청: GET /api/me/todos 의 pendingChangeRequests(서버가 내가 해결할 수 있는 미해결 요청만 준다) → 회의록 상세
 * 새 API 없이 기존 응답만 쓴다. 건수가 0이면 아무것도 그리지 않고, 조회에 실패해도 조용히 숨긴다(다른 화면에 영향 없음).
 * 응답 모양이 예상과 달라도(배열이 아님 등) 오류 없이 빈 상태로 본다.
 */
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { isAbortError } from "@/lib/v2/errors";
import { listProjects, type ProjectListItem } from "@/lib/v2/projects";
import { getTodos, type PendingChangeRequest } from "@/lib/v2/todos";

interface Queue {
  projects: ProjectListItem[];
  requests: PendingChangeRequest[];
}

const EMPTY: Queue = { projects: [], requests: [] };
const MAX_SHOWN = 5;

export function ApprovalQueue({ accountId }: { accountId: number }) {
  const [queue, setQueue] = useState<Queue>(EMPTY);
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const [projects, todos] = await Promise.all([
      listProjects(controller.signal).catch((error) => (isAbortError(error) ? null : [])),
      getTodos(controller.signal).catch((error) => (isAbortError(error) ? null : null)),
    ]);
    if (controller.signal.aborted) return;
    const mine = Array.isArray(projects)
      ? projects.filter((p) => p?.status === "pending_approval" && p.approver?.id === accountId)
      : [];
    const pending = todos?.pendingChangeRequests?.items;
    setQueue({ projects: mine, requests: Array.isArray(pending) ? (pending as PendingChangeRequest[]) : [] });
  }, [accountId]);

  useEffect(() => {
    void load();
    const onFocus = () => void load();
    window.addEventListener("focus", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      controllerRef.current?.abort();
    };
  }, [load]);

  const total = queue.projects.length + queue.requests.length;
  if (total === 0) return null;

  return (
    <section aria-label="승인 대기" className="flex flex-col gap-2">
      <h2 className="text-xs font-semibold tracking-tight text-mn-muted">
        승인 대기 <span className="font-mn-mono">{total}</span>건
      </h2>
      <ul className="flex flex-col gap-1">
        {queue.projects.slice(0, MAX_SHOWN).map((project) => (
          <li key={`p-${project.id}`}>
            <Link
              href={`/v2/projects/${project.id}`}
              aria-label={`프로젝트 승인 대기: ${project.name}`}
              className="mn-focus flex min-w-0 flex-col rounded-mn-control border border-mn-border px-3 py-2 text-sm hover:bg-mn-elevated"
            >
              <span className="text-xs text-mn-muted">프로젝트 승인</span>
              <span className="break-words">{project.name}</span>
            </Link>
          </li>
        ))}
        {queue.requests.slice(0, MAX_SHOWN).map((request) => (
          <li key={`r-${request.requestId}`}>
            <Link
              href={`/v2/meetings/${request.meetingId}`}
              aria-label={`${request.kind === "supersede" ? "대체 요청" : "수정 요청"} 처리 대기: ${request.meetingTitle}`}
              className="mn-focus flex min-w-0 flex-col rounded-mn-control border border-mn-border px-3 py-2 text-sm hover:bg-mn-elevated"
            >
              <span className="text-xs text-mn-muted">{request.kind === "supersede" ? "대체 요청" : "수정 요청"}</span>
              <span className="break-words">{request.itemTitle || request.meetingTitle}</span>
            </Link>
          </li>
        ))}
      </ul>
      {total > MAX_SHOWN * 2 || queue.projects.length > MAX_SHOWN || queue.requests.length > MAX_SHOWN ? (
        <p className="text-xs text-mn-muted">나머지는 프로젝트 목록과 할 일 화면에서 확인하세요.</p>
      ) : null}
    </section>
  );
}
