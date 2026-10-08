"use client";

/*
 * 유사 업무 검색 팝업(회의록 상세 업무 원장, 작업 71-1 파트 B).
 * - 열면 GET /api/action-items/{id}/similar 를 호출한다(로딩, 오류+재시도, 빈 결과 "비슷한 과거 업무가 없습니다"). 닫으면 요청을 취소한다
 * - 결과 카드(점수 높은 순): 업무명·담당자·기한·상태 라벨·점수(숫자+막대)·이유 칩·회의록 제목·일시(링크, 같은 탭)
 * - 카드 동작은 이 업무(찾는 기준 업무)의 allowedActions 로만 정한다: request_supersede → [대체 요청], supersede_item → [직권 대체]
 *   둘 다 사유 팝업(ReasonDialog)을 거치고, 서버 거부(403·409·422)는 팝업 안 알림으로 보이고 입력은 유지된다
 * - 직권 종료·직권 취소는 넣지 않는다: 카드의 "회의록 열기" 링크로 과거 회의록에서 기존 종결·삭제를 쓴다
 * - 포커스 트랩·Esc 닫기·열었던 버튼으로 포커스 복귀는 Modal 이 처리한다
 */
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button, Modal, StatusDot } from "@/components/mono";
import { formatDateTime, V2_MEETINGS_PATH } from "@/components/v2/meeting-display";
import { ReasonDialog, type ReasonAction } from "@/components/v2/ReasonDialog";
import { can, ITEM_ACTION } from "@/lib/v2/actions";
import { isAbortError } from "@/lib/v2/errors";
import { createChangeRequest, type ActionItem } from "@/lib/v2/meetings";
import { getSimilarItems, SIMILAR_STATUS_LABEL, supersedeItem, type SimilarItem } from "@/lib/v2/supersede";

type LoadState = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; items: SimilarItem[] };

const errorMessage = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

interface SimilarItemsDialogProps {
  meetingId: number;
  /** 찾는 기준 업무. null 이면 닫힘 */
  item: ActionItem | null;
  onClose: () => void;
  /** 대체 요청·직권 대체가 성공한 뒤 부른다(화면 갱신과 결과 안내). 끝나면 이 팝업도 닫힌다 */
  onCompleted: (message: string) => Promise<void>;
}

function Card({
  candidate,
  canRequest,
  canSupersede,
  onRequest,
  onSupersede,
}: {
  candidate: SimilarItem;
  canRequest: boolean;
  canSupersede: boolean;
  onRequest: () => void;
  onSupersede: () => void;
}) {
  const name = candidate.title || "업무명 없음";
  const score = Math.max(0, Math.min(100, Math.round(candidate.score)));
  return (
    <li className="flex flex-col gap-2 rounded-mn-card border border-mn-border bg-mn-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="min-w-0 text-sm font-medium [overflow-wrap:anywhere]">{candidate.title || "(업무명 없음)"}</p>
        <span className="mn-chip mn-chip-neutral whitespace-nowrap">{SIMILAR_STATUS_LABEL[candidate.status] ?? candidate.status}</span>
      </div>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
        <dt className="text-mn-muted">담당자</dt>
        <dd>{candidate.assignee?.name ?? "—"}</dd>
        <dt className="text-mn-muted">기한</dt>
        <dd className="font-mn-mono">{candidate.dueDate ?? "—"}</dd>
        <dt className="text-mn-muted">회의록</dt>
        <dd className="min-w-0 [overflow-wrap:anywhere]">
          <Link href={`${V2_MEETINGS_PATH}/${candidate.meeting.id}`} className="mn-focus rounded-mn-control underline underline-offset-2">
            {candidate.meeting.title || "(제목 없음)"}
          </Link>{" "}
          <span className="font-mn-mono text-mn-muted">{formatDateTime(candidate.meeting.heldAt)}</span>
        </dd>
      </dl>
      <div className="flex items-center gap-3">
        <span className="font-mn-mono text-sm">유사도 {score}점</span>
        <span aria-hidden="true" className="h-2 min-w-0 flex-1 overflow-hidden rounded-mn-badge border border-mn-border bg-mn-bg">
          <span className="block h-full bg-[var(--mn-chip-info-fg)]" style={{ width: `${score}%` }} />
        </span>
      </div>
      {candidate.reasons.length > 0 ? (
        <ul aria-label="유사 이유" className="flex flex-wrap gap-2">
          {candidate.reasons.map((reason) => (
            <li key={reason} className="mn-chip mn-chip-info whitespace-nowrap">
              {reason}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        {canRequest ? (
          <Button size="sm" aria-label={`대체 요청: ${name}`} onClick={onRequest}>
            대체 요청
          </Button>
        ) : null}
        {canSupersede ? (
          <Button size="sm" aria-label={`직권 대체: ${name}`} onClick={onSupersede}>
            직권 대체
          </Button>
        ) : null}
        <Link
          href={`${V2_MEETINGS_PATH}/${candidate.meeting.id}`}
          aria-label={`회의록 열기: ${candidate.meeting.title || "제목 없음"}`}
          className="mn-focus rounded-mn-control text-xs text-mn-muted underline underline-offset-2 hover:text-mn-text"
        >
          회의록에서 직접 처리하려면 회의록 열기
        </Link>
      </div>
    </li>
  );
}

export function SimilarItemsDialog({ meetingId, item, onClose, onCompleted }: SimilarItemsDialogProps) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [reasonAction, setReasonAction] = useState<ReasonAction | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const itemId = item?.id ?? null;
  const open = item !== null;

  const load = useCallback(async (id: number) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ kind: "loading" });
    try {
      const found = await getSimilarItems(id, controller.signal);
      setState({ kind: "ready", items: [...found].sort((a, b) => b.score - a.score) });
    } catch (error) {
      if (isAbortError(error)) return;
      setState({ kind: "error", message: errorMessage(error, "유사 업무를 찾지 못했습니다.") });
    }
  }, []);

  useEffect(() => {
    if (itemId === null) return;
    setReasonAction(null);
    void load(itemId);
    return () => controllerRef.current?.abort();
  }, [itemId, load]);

  if (item === null) return <Modal open={false} onClose={onClose} title="" />;
  const baseName = item.title || "(업무명 없음)";
  const canRequest = can(item.allowedActions, ITEM_ACTION.requestSupersede);
  const canSupersede = can(item.allowedActions, ITEM_ACTION.supersedeItem);

  const openRequest = (old: SimilarItem) =>
    setReasonAction({
      title: "대체 요청",
      target: `${old.title || "(업무명 없음)"} → ${baseName}`,
      confirmLabel: "대체 요청 올리기",
      requireReason: true,
      notice: "과거 업무를 이 업무로 대체해 달라는 요청을 남깁니다. 수락되기 전에는 과거 업무가 그대로 유지됩니다.",
      run: async (reason, signal) => {
        await createChangeRequest(meetingId, { comment: reason, itemId: item.id, kind: "supersede", supersedesItemId: old.itemId }, signal);
      },
    });

  const openSupersede = (old: SimilarItem) =>
    setReasonAction({
      title: "직권 대체",
      target: `${old.title || "(업무명 없음)"} → ${baseName}`,
      confirmLabel: "직권 대체",
      requireReason: true,
      notice: "과거 업무를 이 업무로 바로 대체합니다. 대체된 업무는 할 일·알림에서 빠지고 업무 원장에는 대체됨으로 남습니다.",
      run: async (reason, signal) => {
        await supersedeItem(item.id, { supersedesItemId: old.itemId, reason }, signal);
      },
    });

  // 요청·대체가 성공하면(ReasonDialog 가 onDone 을 부른다) 화면을 갱신하고 이 팝업도 닫는다
  const done = (message: string) => async () => {
    await onCompleted(message);
    onClose();
  };
  const lastKind = reasonAction?.title === "직권 대체" ? "직권 대체" : "대체 요청";

  return (
    <>
      <Modal
        open={open}
        onClose={onClose}
        title="유사 업무 검색"
        description={
          <>
            <span>기준 업무 </span>
            <span className="text-mn-text">{baseName}</span>
            <span> · 같은 프로젝트의 과거 회의록에서 비슷한 열린 업무를 찾습니다. 자동으로 합치지 않습니다.</span>
          </>
        }
        panelClassName="w-full max-w-2xl max-h-[calc(100dvh-2rem)] overflow-y-auto"
      >
        <div className="mt-3 flex flex-col gap-3">
          {state.kind === "loading" ? (
            <p role="status" className="text-sm text-mn-muted">
              유사 업무를 찾는 중…
            </p>
          ) : state.kind === "error" ? (
            <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-mn-card border border-mn-border p-3">
              <span className="text-sm">
                <StatusDot tone="error" label={`유사 업무를 찾지 못했습니다 · ${state.message}`} />
              </span>
              <Button size="sm" onClick={() => void load(item.id)}>
                다시 시도
              </Button>
            </div>
          ) : state.items.length === 0 ? (
            <p className="text-sm text-mn-muted">비슷한 과거 업무가 없습니다</p>
          ) : (
            <ul aria-label="유사 업무 후보" className="flex flex-col gap-3">
              {state.items.map((candidate) => (
                <Card
                  key={candidate.itemId}
                  candidate={candidate}
                  canRequest={canRequest}
                  canSupersede={canSupersede}
                  onRequest={() => openRequest(candidate)}
                  onSupersede={() => openSupersede(candidate)}
                />
              ))}
            </ul>
          )}
          <div className="flex justify-end">
            <Button onClick={onClose}>닫기</Button>
          </div>
        </div>
      </Modal>
      <ReasonDialog
        action={reasonAction}
        onClose={() => setReasonAction(null)}
        onDone={done(lastKind === "직권 대체" ? "직권 대체했습니다" : "대체 요청을 올렸습니다")}
      />
    </>
  );
}
