"use client";

/*
 * v2 프로젝트 목록(작업 69-1)
 * - GET /api/projects: 내가 참여자·등록자·승인자인 프로젝트(관리자 이상은 같은 고객사 전체, 서버 판정). 탭(전체·진행 중·승인 대기·반려)은 응답을 화면에서 나눈다(개수 표시).
 *   승인 대기 탭이 승인 큐다. 승인·반려는 상세에서만 한다.
 * - "승인 필요" 표시: 서버가 준 승인자(approver)가 나일 때만(승인 대기 프로젝트). 알 수 없으면 붙이지 않는다.
 * - 768 미만에서는 회의록 목록과 같은 방식으로 같은 table 을 카드처럼 보이게 한다.
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge, Button, StatusDot } from "@/components/mono";
import { useAuth } from "@/components/v2/AuthProvider";
import { ProjectStatusChip } from "@/components/v2/ProjectStatusChip";
import { isAbortError } from "@/lib/v2/errors";
import { listProjects, ROLE_LABEL, type ProjectListItem, type ProjectStatus } from "@/lib/v2/projects";

type Tab = "all" | ProjectStatus;
const TABS: { key: Tab; label: string }[] = [
  { key: "all", label: "전체" },
  { key: "active", label: "진행 중" },
  { key: "pending_approval", label: "승인 대기" },
  { key: "rejected", label: "반려" },
];
const EMPTY_TEXT: Record<Tab, string> = {
  all: "프로젝트가 없습니다.",
  active: "진행 중인 프로젝트가 없습니다.",
  pending_approval: "승인 대기 중인 프로젝트가 없습니다.",
  rejected: "반려된 프로젝트가 없습니다.",
};

type LoadState = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; items: ProjectListItem[] };

/** 카드 모드(768 미만)에서 셀 앞에 보이는 라벨: data-label 속성을 CSS 로 보여 준다(글자 내용·셀 순서는 그대로) */
const CELL_LABEL =
  "max-md:block max-md:px-0 max-md:before:mr-2 max-md:before:inline-block max-md:before:min-w-16 max-md:before:text-xs max-md:before:text-mn-muted max-md:before:content-[attr(data-label)]";

const detailHref = (id: number) => `/v2/projects/${id}`;

function ProjectsTable({ items, myId }: { items: ProjectListItem[]; myId: number | null }) {
  const router = useRouter();
  return (
    <div className="min-w-0 overflow-x-auto rounded-mn-card border border-mn-border bg-mn-surface">
      <table role="table" aria-label="프로젝트 목록" className="w-full min-w-[44rem] table-fixed border-collapse text-sm max-md:block max-md:min-w-0">
        <colgroup className="max-md:hidden">
          <col />
          <col className="w-[7rem]" />
          <col className="w-[8rem]" />
          <col className="w-[8rem]" />
          <col className="w-[6rem]" />
          <col className="w-[6rem]" />
        </colgroup>
        <thead role="rowgroup" className="max-md:sr-only">
          <tr role="row" className="h-10 border-b border-mn-border text-left text-xs text-mn-muted">
            <th role="columnheader" scope="col" className="px-3 font-medium">프로젝트</th>
            <th role="columnheader" scope="col" className="px-3 font-medium">상태</th>
            <th role="columnheader" scope="col" className="px-3 font-medium">등록 부서</th>
            <th role="columnheader" scope="col" className="px-3 font-medium">총괄</th>
            <th role="columnheader" scope="col" className="px-3 font-medium">내 역할</th>
            <th role="columnheader" scope="col" className="px-3 text-right font-medium max-md:text-left">참여자</th>
          </tr>
        </thead>
        <tbody role="rowgroup" className="max-md:block">
          {items.map((project) => {
            const needsMyApproval = project.status === "pending_approval" && myId !== null && project.approver?.id === myId;
            return (
              <tr
                key={project.id}
                role="row"
                onClick={() => router.push(detailHref(project.id))}
                className="min-h-12 cursor-pointer border-b border-mn-border last:border-b-0 hover:bg-mn-elevated max-md:flex max-md:flex-col max-md:gap-1 max-md:px-4 max-md:py-3"
              >
                <td role="cell" data-label="프로젝트" className={`px-3 py-3 [overflow-wrap:anywhere] ${CELL_LABEL}`}>
                  <span className="flex flex-wrap items-center gap-2">
                    <Link
                      href={detailHref(project.id)}
                      onClick={(event) => event.stopPropagation()}
                      className="mn-focus rounded-mn-control font-medium text-mn-text hover:underline"
                    >
                      {project.name}
                    </Link>
                    {needsMyApproval ? <Badge variant="solid">승인 필요</Badge> : null}
                  </span>
                </td>
                <td role="cell" data-label="상태" className={`px-3 ${CELL_LABEL}`}>
                  <ProjectStatusChip status={project.status} />
                </td>
                <td role="cell" data-label="등록 부서" className={`px-3 py-3 [overflow-wrap:anywhere] ${CELL_LABEL}`}>
                  {project.departmentName}
                </td>
                <td role="cell" data-label="총괄" className={`px-3 py-3 [overflow-wrap:anywhere] ${CELL_LABEL}`}>
                  {project.lead?.name ?? <span className="text-mn-muted">—</span>}
                </td>
                <td role="cell" data-label="내 역할" className={`px-3 ${CELL_LABEL}`}>
                  {project.myRole ? ROLE_LABEL[project.myRole] : <span className="text-mn-muted">—</span>}
                </td>
                <td role="cell" data-label="참여자" className={`px-3 text-right font-mn-mono text-[13px] max-md:text-left ${CELL_LABEL}`}>
                  {project.memberCount}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function V2ProjectsPage() {
  const { account } = useAuth();
  const [tab, setTab] = useState<Tab>("all");
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ kind: "loading" });
    try {
      setState({ kind: "ready", items: await listProjects(controller.signal) });
    } catch (error) {
      if (isAbortError(error)) return;
      setState({ kind: "error", message: error instanceof Error && error.message ? error.message : "프로젝트를 불러오지 못했습니다." });
    }
  }, []);

  useEffect(() => {
    void load();
    return () => controllerRef.current?.abort();
  }, [load]);

  const items = state.kind === "ready" ? state.items : [];
  const countOf = (key: Tab) => (key === "all" ? items.length : items.filter((p) => p.status === key).length);
  const shown = tab === "all" ? items : items.filter((p) => p.status === tab);

  return (
    <div className="flex flex-col gap-6 leading-[1.6]">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 title="프로젝트" className="mn-page-title text-mn-text">프로젝트</h1>
          <p className="mt-1 text-sm text-mn-muted">참여 중인 프로젝트와 승인 대기 현황입니다.</p>
        </div>
        <Link
          href="/v2/projects/new"
          className="mn-focus inline-flex h-10 shrink-0 items-center justify-center whitespace-nowrap rounded-mn-control border border-mn-accent bg-mn-accent px-4 text-sm font-medium text-mn-on-accent hover:bg-mn-accent-hover max-md:h-11"
        >
          프로젝트 등록
        </Link>
      </header>

      <div role="tablist" aria-label="프로젝트 상태" className="flex gap-1 overflow-x-auto overflow-y-hidden border-b border-mn-border max-md:flex-nowrap">
        {TABS.map((t) => {
          const selected = t.key === tab;
          return (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={selected}
              onClick={() => setTab(t.key)}
              className={`mn-focus h-10 shrink-0 whitespace-nowrap rounded-t-mn-control border-b-2 px-4 text-sm max-md:h-11 max-md:aria-selected:before:content-['●_'] ${
                selected ? "border-mn-text font-semibold text-mn-text" : "border-transparent text-mn-muted hover:text-mn-text"
              }`}
            >
              {t.label} <span className="font-mn-mono text-[13px]">({state.kind === "ready" ? countOf(t.key) : 0})</span>
            </button>
          );
        })}
      </div>

      {state.kind === "loading" ? (
        <p role="status" className="text-sm text-mn-muted">
          프로젝트를 불러오는 중…
        </p>
      ) : state.kind === "error" ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-4 rounded-mn-card border border-mn-border bg-mn-surface p-6">
          <span className="text-sm">
            <StatusDot tone="error" label={`프로젝트를 불러오지 못했습니다 · ${state.message}`} />
          </span>
          <Button size="sm" onClick={() => void load()}>
            다시 시도
          </Button>
        </div>
      ) : shown.length === 0 ? (
        <div role="status" className="rounded-mn-card border border-mn-border bg-mn-surface p-6 text-sm text-mn-muted">
          {EMPTY_TEXT[tab]}
        </div>
      ) : (
        <ProjectsTable items={shown} myId={account?.id ?? null} />
      )}
    </div>
  );
}
