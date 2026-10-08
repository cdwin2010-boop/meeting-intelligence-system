"use client";

/*
 * v2 업무 현황(작업 69-3): GET /api/workload 의 저장값(하루 한 번 집계, 전일 기준)만 보여 준다.
 * - 범위(전사·부서·개인)와 선택 가능한 부서는 서버 응답의 scope 로만 정한다(화면은 권한을 판단하지 않는다)
 * - KPI 4개, 구성원별 가로 스택 바(완료·진행 중·지연: 색과 함께 글자 라벨·건수), 집중 관리(지연·D-3 임박) 목록, 하단 기준일 안내
 * - 조회는 AbortController 로 이전 요청을 취소한다
 */
import Link from "next/link";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { Button, MetricCard, StatusDot } from "@/components/mono";
import { ApiError, isAbortError } from "@/lib/v2/errors";
import { formatSnapshotDate, getWorkload, type UrgentItem, type Workload, type WorkloadMember } from "@/lib/v2/workload";

type LoadState =
  | { kind: "loading" }
  | { kind: "forbidden"; message: string }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: Workload };

const SEGMENTS = [
  { key: "completed", label: "완료", chip: "mn-chip-success", fill: "bg-[var(--mn-chip-success-fg)]" },
  { key: "inProgress", label: "진행 중", chip: "mn-chip-info", fill: "bg-[var(--mn-chip-info-fg)]" },
  { key: "overdue", label: "지연", chip: "mn-chip-danger", fill: "bg-[var(--mn-chip-danger-fg)]" },
] as const;

/** 구성원 한 명의 스택 바 + 글자 라벨·건수. 분모가 0이면 빈 바와 "업무 없음" */
function MemberRow({ member }: { member: WorkloadMember }) {
  const total = member.completed + member.inProgress + member.overdue;
  const summary = `${member.name}: 완료 ${member.completed}건, 진행 중 ${member.inProgress}건, 지연 ${member.overdue}건`;
  return (
    <li className="grid gap-2 border-b border-mn-border px-5 py-4 last:border-b-0 md:grid-cols-[180px_minmax(0,1fr)] md:items-center md:gap-6">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{member.name}</span>
        {member.overloaded ? <span className="mn-chip mn-chip-warning whitespace-nowrap">미완료 과다</span> : null}
      </div>
      <div className="flex min-w-0 flex-col gap-2">
        <div
          role="img"
          aria-label={total === 0 ? `${member.name}: 업무 없음` : summary}
          className="flex h-3 w-full overflow-hidden rounded-mn-badge border border-mn-border bg-mn-bg"
        >
          {total === 0
            ? null
            : SEGMENTS.map((segment) => {
                const count = member[segment.key];
                return count > 0 ? (
                  <span key={segment.key} className={segment.fill} style={{ width: `${(count / total) * 100}%` }} />
                ) : null;
              })}
        </div>
        {total === 0 ? (
          <span className="text-xs text-mn-muted">업무 없음</span>
        ) : (
          <ul className="flex flex-wrap gap-2" aria-hidden="true">
            {SEGMENTS.map((segment) => (
              <li key={segment.key} className={`mn-chip ${segment.chip} whitespace-nowrap`}>
                {segment.label} <span className="font-mn-mono">{member[segment.key]}</span>건
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  );
}

function urgentLabel(item: UrgentItem): string {
  return item.kind === "overdue" ? `지연 ${item.days}일` : `D-${item.days}`;
}

function UrgentRow({ item }: { item: UrgentItem }) {
  const label = urgentLabel(item);
  return (
    <li>
      <Link
        href={`/v2/meetings/${item.meetingId}`}
        aria-label={`${item.title || "업무명 없음"} 업무, 담당 ${item.assignee.name}, 기한 ${item.dueDate}, ${label}, 회의록 ${item.meetingTitle}`}
        className="mn-focus grid gap-1 px-5 py-3 hover:bg-mn-elevated md:grid-cols-[minmax(0,1fr)_120px_110px_minmax(0,220px)] md:items-center md:gap-4"
      >
        <span className="min-w-0 truncate text-sm font-medium">{item.title || "(업무명 없음)"}</span>
        <span className="text-sm text-mn-muted">{item.assignee.name}</span>
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-mn-mono text-[13px]">{item.dueDate}</span>
        </span>
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          <span className={`mn-chip ${item.kind === "overdue" ? "mn-chip-danger" : "mn-chip-warning"} whitespace-nowrap`}>{label}</span>
          <span className="min-w-0 truncate text-xs text-mn-muted">{item.meetingTitle}</span>
        </span>
      </Link>
    </li>
  );
}

export default function V2WorkloadPage() {
  const selectId = useId();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  // "" 이면 서버 기본 범위, 숫자 문자열이면 그 부서
  const [requested, setRequested] = useState("");
  const [companyCapable, setCompanyCapable] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async (departmentId: string) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ kind: "loading" });
    try {
      const data = await getWorkload(departmentId === "" ? null : Number(departmentId), controller.signal);
      if (data.scope.kind === "company") setCompanyCapable(true);
      setState({ kind: "ready", data });
    } catch (error) {
      if (isAbortError(error)) return;
      if (error instanceof ApiError && error.status === 403) {
        setState({ kind: "forbidden", message: error.message || "이 현황을 볼 권한이 없습니다." });
        return;
      }
      setState({ kind: "error", message: error instanceof Error && error.message ? error.message : "현황을 불러오지 못했습니다." });
    }
  }, []);

  useEffect(() => {
    void load(requested);
    return () => controllerRef.current?.abort();
  }, [load, requested]);

  const data = state.kind === "ready" ? state.data : null;
  const scope = data?.scope;
  const showSelector = scope !== undefined && scope.departments.length > 0 && (companyCapable || scope.kind === "company" || scope.departments.length > 1);
  const selectValue = requested === "" && scope && scope.kind !== "company" ? String(scope.department?.id ?? "") : requested;

  return (
    <div className="flex flex-col gap-6 leading-[1.6]">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="mn-page-title text-mn-text">업무 현황</h1>
          {scope?.kind === "self" ? <p className="mt-1 text-sm text-mn-muted">내 현황</p> : null}
        </div>
        {showSelector ? (
          <div className="flex items-center gap-2">
            <label htmlFor={selectId} className="text-sm font-medium">
              부서
            </label>
            <select
              id={selectId}
              value={selectValue}
              onChange={(event) => setRequested(event.target.value)}
              className="mn-focus h-10 rounded-mn-control border border-mn-control bg-mn-bg px-3 text-sm text-mn-text outline-none"
            >
              {companyCapable || scope?.kind === "company" ? <option value="">전사</option> : null}
              {scope?.departments.map((department) => (
                <option key={department.id} value={String(department.id)}>
                  {department.name}
                </option>
              ))}
            </select>
          </div>
        ) : null}
      </header>

      {state.kind === "loading" ? (
        <p role="status" className="text-sm text-mn-muted">
          현황을 불러오는 중…
        </p>
      ) : state.kind === "forbidden" ? (
        <div role="alert" className="rounded-mn-card border border-mn-border bg-mn-surface p-6 text-sm">
          <StatusDot tone="error" label={`볼 수 없습니다 · ${state.message}`} />
        </div>
      ) : state.kind === "error" ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-mn-card border border-mn-border bg-mn-surface p-6">
          <span className="text-sm">
            <StatusDot tone="error" label={`현황을 불러오지 못했습니다 · ${state.message}`} />
          </span>
          <Button size="sm" onClick={() => void load(requested)}>
            다시 시도
          </Button>
        </div>
      ) : data && data.snapshotDate === null ? (
        <p role="status" className="rounded-mn-card border border-mn-border bg-mn-surface p-6 text-sm">
          아직 집계된 통계가 없습니다. 집계는 매일 새벽에 갱신됩니다.
        </p>
      ) : data && data.snapshotDate !== null ? (
        <>
          <section aria-label="요약" className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCard label="총 배정 업무" value={data.kpis.assigned} />
            <MetricCard label="완료율" value={data.kpis.completionRate === null ? "—" : `${data.kpis.completionRate}%`} />
            <MetricCard label="지연 건수" value={data.kpis.overdue} />
            <MetricCard label="1인당 평균 잔여 업무" value={data.kpis.avgOpenPerPerson} hint={`구성원 ${data.kpis.memberCount}명`} />
          </section>

          <section aria-label="구성원별 업무 현황" className="overflow-hidden rounded-mn-card border border-mn-border bg-mn-surface">
            <div className="border-b border-mn-border px-5 py-4">
              <h2 className="text-base font-semibold tracking-tight">구성원별 현황</h2>
            </div>
            {data.members.length === 0 ? (
              <p className="px-5 py-6 text-sm text-mn-muted">표시할 구성원이 없습니다.</p>
            ) : (
              <ul>
                {data.members.map((member) => (
                  <MemberRow key={member.accountId} member={member} />
                ))}
              </ul>
            )}
          </section>

          <section aria-label="집중 관리" className="overflow-hidden rounded-mn-card border border-mn-border bg-mn-surface">
            <div className="border-b border-mn-border px-5 py-4">
              <h2 className="text-base font-semibold tracking-tight">집중 관리</h2>
              <p className="mt-1 text-xs text-mn-muted">지연 업무와 기한이 3일 이내인 업무입니다.</p>
            </div>
            {data.urgentItems.length === 0 ? (
              <p className="px-5 py-6 text-sm text-mn-muted">지연·임박 업무가 없습니다</p>
            ) : (
              <ul className="divide-y divide-mn-border">
                {data.urgentItems.map((item) => (
                  <UrgentRow key={item.itemId} item={item} />
                ))}
              </ul>
            )}
          </section>
        </>
      ) : null}

      {data && data.snapshotDate !== null ? (
        <footer aria-label="통계 기준 안내" className="text-sm text-mn-muted">
          <p className="text-mn-text">전일({formatSnapshotDate(data.snapshotDate)} 기준) 통계 실적입니다.</p>
          <p className="mt-1">
            담당자가 계정으로 확정된 업무만 집계합니다. 직권 종료·삭제·보류 회의록·대체된 업무는 제외하고, 종결 구분 도입 전에 종결된 업무는 완료로
            집계하지 않습니다. 지연은 기한 다음 날부터입니다.
          </p>
        </footer>
      ) : null}
    </div>
  );
}
