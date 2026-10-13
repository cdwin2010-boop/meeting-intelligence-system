"use client";

/*
 * 왼쪽 메뉴 "처리 현황": 내가 올린 회의록의 처리 진행·완료·실패를 어느 화면에서나 본다.
 * - 항목: 회의명(말줄임) + 상태 글자(색만으로 구분하지 않음). 처리 중은 "처리 중 · 경과 mm:ss"(서버가 준 경과 초 + 응답 뒤 흐른 시간)
 *   완료: "처리 완료" [열기][닫기] / 실패: 한국어 이유+오류 코드 [열기]([다시 처리]는 canReprocess 일 때만) [닫기] / 내용 없음: "내용 없음, 파일 확인 요청" [열기][닫기]
 * - 최대 3건만 보이고 나머지는 "외 N건"(펼치기 버튼, 키보드 가능). 상태가 바뀌면 aria-live(polite)로 읽힌다.
 * - 3단계(전사·업무 추출·5개 항목) 진행 표시는 V3.0 과제라 만들지 않는다.
 */
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/mono";
import { JobStatusChip, QueueNotes } from "@/components/v2/JobStatusChip";
import { ProcessNow } from "@/components/v2/ProcessNow";
import { useProcessing, type TrackedItem } from "@/components/v2/ProcessingProvider";
import { ReasonDialog, type ReasonAction } from "@/components/v2/ReasonDialog";
import { detailPath } from "@/components/v2/meeting-display";
import { engineChoiceOf, useEngineTestMode } from "@/components/v2/EngineChoice";
import { useAuth } from "@/components/v2/AuthProvider";
import { reprocessMeeting } from "@/lib/v2/meetings";
import { isManager } from "@/lib/v2/types";
import { describeProcessingError } from "@/lib/v2/processing-errors";
import { formatElapsed, isActiveStatus } from "@/lib/v2/processing-status";

const VISIBLE_LIMIT = 3;

function ItemView({
  item, now, viewing, onOpen, onReprocess, onDismiss, onProcessed,
}: {
  item: TrackedItem;
  now: number;
  /** 지금 이 회의록 상세를 보고 있음(경로의 회의록 id 가 같음) */
  viewing: boolean;
  onOpen: () => void;
  onReprocess: () => void;
  onDismiss: () => void;
  /** 즉시 변환 요청 뒤 처리 현황 다시 받기 */
  onProcessed: () => void;
}) {
  const active = isActiveStatus(item.status);
  // 즉시 변환: 서버가 항목에 내려준 canProcessNow 만 본다(직급·상세 조회로 다시 판단하지 않는다)
  const canProcessNow = item.status === "queued" && item.canProcessNow === true;
  const elapsed = item.elapsedSec + Math.max(0, now - item.receivedAt) / 1000;
  return (
    <li className="flex flex-col gap-2 rounded-mn-control border border-mn-border p-2 text-xs">
      <span title={item.title} className="truncate text-sm font-medium text-mn-text">
        {item.title || "(제목 없음)"}
      </span>
      {active ? (
        <span className="flex flex-col items-start gap-1">
          <JobStatusChip status={item.status} label={`${item.status === "queued" ? "대기 중" : "처리 중"} · 경과 ${formatElapsed(elapsed)}`} />
          <QueueNotes status={item.status} queueAhead={item.queueAhead} prioritized={item.prioritized} workerAlive={item.workerAlive} />
          {item.status === "queued" ? <ProcessNow meetingId={item.meetingId} allowed={canProcessNow} onDone={onProcessed} /> : null}
        </span>
      ) : item.status === "completed" ? (
        <JobStatusChip status="completed" />
      ) : item.status === "no_content" ? (
        <JobStatusChip status="no_content" label="내용 없음, 파일 확인 요청" />
      ) : (
        <span className="flex flex-col items-start gap-1">
          <JobStatusChip status="failed" label={`처리 실패 · ${describeProcessingError(item.errorCode)}`} />
          {item.errorCode ? <span className="text-mn-muted">오류 코드 <span className="font-mn-mono">{item.errorCode}</span></span> : null}
        </span>
      )}
      <span className="flex flex-wrap gap-1">
        {viewing ? (
          <span aria-label={`${item.title} 보는 중`} className="inline-flex h-8 items-center px-1 text-xs text-mn-muted max-md:min-h-11">
            보는 중
          </span>
        ) : (
          <Button size="sm" aria-label={`${item.title} 열기`} onClick={onOpen} className="max-md:min-h-11 max-md:min-w-11">
            열기
          </Button>
        )}
        {item.status === "failed" && item.canReprocess ? (
          <Button size="sm" aria-label={`${item.title} 다시 처리`} onClick={onReprocess} className="max-md:min-h-11">
            다시 처리
          </Button>
        ) : null}
        {!active ? (
          <Button size="sm" aria-label={`${item.title} 닫기`} onClick={onDismiss} className="max-md:min-h-11 max-md:min-w-11">
            닫기
          </Button>
        ) : null}
      </span>
    </li>
  );
}

export function ProcessingPanel() {
  const { account } = useAuth();
  const engineTest = useEngineTestMode(account !== null); // 엔진 시험 모드(작업 91-1): 재처리 팝업의 엔진 선택
  const router = useRouter();
  const pathname = usePathname();
  const viewingId = /^\/v2\/meetings\/(\d+)$/.exec(pathname)?.[1];
  const { items, dismiss, refresh, announcement } = useProcessing();
  const [expanded, setExpanded] = useState(false);
  const [now, setNow] = useState(() => performance.now());
  const [action, setAction] = useState<ReasonAction | null>(null);

  // 처리 중인 항목이 있을 때만 1초마다 경과 시간을 다시 그린다
  const hasActive = items.some((item) => isActiveStatus(item.status));
  useEffect(() => {
    if (!hasActive) return;
    setNow(performance.now());
    const timer = setInterval(() => setNow(performance.now()), 1000);
    return () => clearInterval(timer);
  }, [hasActive]);

  const shown = expanded ? items : items.slice(0, VISIBLE_LIMIT);
  const hidden = items.length - VISIBLE_LIMIT;

  const openReprocess = (item: TrackedItem) =>
    setAction({
      title: "회의록 재처리", target: item.title, confirmLabel: "재처리하기", requireReason: false,
      notice: "음성이 다시 처리되며 Gemini 모드이면 외부로 전송되고 비용이 생길 수 있습니다.",
      choice: engineTest.testMode && isManager(account?.rank) ? engineChoiceOf(engineTest.defaultEngine) : undefined,
      run: async (_reason, signal, choice) => {
        await reprocessMeeting(item.meetingId, signal, choice || undefined);
      },
    });

  return (
    <section id="processing-panel" aria-label="처리 현황" aria-live="polite" className="flex flex-col gap-2">
      <p className="sr-only" aria-label="처리 상태 변경 안내">
        {announcement}
      </p>
      {items.length > 0 ? (
        <>
          <h2 className="text-xs font-medium text-mn-muted">처리 현황</h2>
          <ul className="flex flex-col gap-2">
            {shown.map((item) => (
              <ItemView
                key={item.jobId}
                item={item}
                now={now}
                viewing={viewingId !== undefined && Number(viewingId) === item.meetingId}
                onOpen={() => router.push(detailPath(item.meetingId))}
                onReprocess={() => openReprocess(item)}
                onDismiss={() => dismiss(item.jobId)}
                onProcessed={refresh}
              />
            ))}
          </ul>
          {hidden > 0 ? (
            <Button size="sm" aria-expanded={expanded} onClick={() => setExpanded((v) => !v)} className="max-md:min-h-11">
              {expanded ? "접기" : `외 ${hidden}건`}
            </Button>
          ) : null}
        </>
      ) : null}
      <ReasonDialog action={action} onClose={() => setAction(null)} onDone={async () => refresh()} />
    </section>
  );
}
