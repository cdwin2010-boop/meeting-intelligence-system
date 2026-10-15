"use client";

/*
 * v2 회의록 올리기 (시안 docs/ui-v2-mockups/04-upload.html)
 * - 입력: 음성 파일(선택 또는 끌어다 놓기), 회의명(비우면 서버가 파일명으로 채움), 회의 일시(필수, 브라우저 현지 시각 → 오프셋 붙은 ISO)
 * - 회의 일시(작업 88-4): 처음에는 빈칸이다. 파일을 고르면 파일의 수정 시각을, "녹음하기"로 고르면 녹음을 시작한 시각을 제안값으로 미리 채우고
 *   등록자가 확인한다(직접 고친 값은 덮어쓰지 않음). 제안값이 없으면 빈칸 + 필수 안내. 비어 있거나 현재보다 미래면 서버를 부르지 않고 안내한다
 * - POST /api/meetings/upload(202) 성공 시 /v2/meetings/{meetingId} 로 이동. 처리(전사·추출)는 서버 백그라운드
 * - 허용 형식(415)·용량(413)은 서버 설정이 판정한다. 오류는 서버 문구로 보여 주고 같은 입력으로 다시 시도할 수 있다
 * - 참석자는 같은 고객사 계정 중 여러 명 선택(선택 사항, 목록 조회 실패해도 올리기는 가능)
 * - 회의 유형은 필수(화면에서만 강제, 서버는 선택 입력). 프로젝트 회의면 내가 참여자인 진행 중 프로젝트를 함께 보낸다
 * - 모바일(768 미만 또는 터치, 작업 88-2): 음성 파일을 올리기 직전에 Wi-Fi 연결 판정(lib/v2/network.ts)을 하고, wifi·ethernet 이 아니거나 알 수 없으면
 *   안내 팝업을 띄운다. [업로드 계속]은 바로 올리고, [등록 보류]는 기기 보류 목록(IndexedDB)에 저장한다. 목록의 [지금 올리기]는 같은 흐름을 쓴다.
 *   올리기에 성공하면 상세로 이동하면서 "대기열 등록" 안내를 한 번 보인다(lib/v2/upload-notice.ts).
 */
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useId, useRef, useState, type DragEvent, type FormEvent } from "react";

import { Button, StatusDot } from "@/components/mono";
import { EngineChoiceField, useEngineTestMode } from "@/components/v2/EngineChoice";
import { FakeEngineNotice, useEngineIsFake, useUploadOptions } from "@/components/v2/EngineNotice";
import type { EngineChoice } from "@/lib/v2/engine-test";
import { isManager } from "@/lib/v2/types";
import { useAuth } from "@/components/v2/AuthProvider";
import { FilePicker } from "@/components/v2/FilePicker";
import { MeetingTypeField, ProjectField } from "@/components/v2/MeetingClassFields";
import { detailPath, V2_MEETINGS_PATH } from "@/components/v2/meeting-display";
import { ParticipantPicker } from "@/components/v2/ParticipantPicker";
import { PendingUploadList } from "@/components/v2/PendingUploadList";
import { useProcessing } from "@/components/v2/ProcessingProvider";
import { WifiNoticeDialog } from "@/components/v2/WifiNoticeDialog";
import { isAbortError } from "@/lib/v2/errors";
import {
  isFutureMeetingAt,
  MEETING_AT_FUTURE_MESSAGE,
  MEETING_AT_REQUIRED_MESSAGE,
  suggestionFromTimestamp,
  toOffsetIso,
} from "@/lib/v2/meeting-at";
import { shouldShowWifiNotice } from "@/lib/v2/network";
import {
  addPending,
  getPendingFile,
  listPending,
  PendingStorageError,
  PENDING_UNAVAILABLE_MESSAGE,
  removePending,
  type PendingMeta,
} from "@/lib/v2/pending-uploads";
import { setUploadNotice } from "@/lib/v2/upload-notice";
import type { MeetingType } from "@/lib/v2/meeting-types";
import { downloadTranscriptTemplate, TRANSCRIPT_TEMPLATE_FILE_NAME, uploadMeeting } from "@/lib/v2/meetings";
import {
  isTranscriptKind,
  kindForFileName,
  kindMismatchHint,
  matchesTranscriptExt,
  sourceKindOf,
  SOURCE_KIND_LABEL,
  SOURCE_KINDS,
  transcriptExtOf,
  type SourceKind,
} from "@/lib/v2/source-kind";
import { getProject } from "@/lib/v2/projects";
import { clearUploadDraft, loadUploadDraft, saveUploadDraft, UPLOAD_PATH } from "@/lib/v2/upload-return";

const inputClass =
  "mn-focus h-10 w-full rounded-mn-control border border-mn-control bg-mn-bg px-3 text-sm text-mn-text outline-none disabled:opacity-50";

/** 올리기 한 건의 입력(새로 고른 파일 또는 보류 목록의 파일). fromPendingId 가 있으면 보류 목록에서 온 것이다 */
interface Submission {
  file: File;
  title: string;
  heldAt: string;
  participantIds: number[];
  meetingType: MeetingType;
  projectId?: number;
  sourceKind: SourceKind;
  fromPendingId?: string;
}

// useSearchParams 를 쓰는 화면은 Suspense 경계가 필요하다(Next 15)
export default function V2UploadPage() {
  return (
    <Suspense fallback={null}>
      <UploadForm />
    </Suspense>
  );
}

function UploadForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const engineIsFake = useEngineIsFake(true);
  const { xlsxEnabled, pasteMaxChars, loaded: optionsLoaded } = useUploadOptions();
  const { refresh: refreshProcessing } = useProcessing();
  const { account } = useAuth();
  const accountId = account?.id ?? null;
  // 엔진 시험 모드(작업 91-1, 시험 전용): 관리자 이상만 처리 엔진을 고른다. 꺼져 있으면 필드도 요청 값도 없다(현행 동작)
  const engineTest = useEngineTestMode(true);
  const [engineChoice, setEngineChoice] = useState<EngineChoice | null>(null);
  const engineSelectable = engineTest.testMode && isManager(account?.rank);
  const chosenEngine = engineSelectable ? (engineChoice ?? engineTest.defaultEngine) : undefined;
  const titleId = useId();
  const dateId = useId();
  const timeId = useId();
  const fileHintId = useId();
  const controllerRef = useRef<AbortController | null>(null);
  const typeRef = useRef<HTMLInputElement | null>(null);
  const projectRef = useRef<HTMLSelectElement | null>(null);
  const submittingRef = useRef(false); // 빠른 연타로 두 번 보내는 것을 막는다
  const handledQueryRef = useRef<string | null>(null); // 같은 주소 인자를 두 번 처리하지 않는다(개발 모드 이중 실행 포함)

  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [meetingType, setMeetingType] = useState<MeetingType | "">("");
  const [projectId, setProjectId] = useState("");
  const [sourceKind, setSourceKind] = useState<SourceKind>("audio");
  // 웹 직접 입력(붙여넣기, 작업 B-2): 자료 종류는 txt 와 같고(STT 생략) 파일 대신 입력창의 글을 txt 로 올린다
  const [pasteMode, setPasteMode] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const pasteId = useId();
  const [participantIds, setParticipantIds] = useState<number[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [restoredNotice, setRestoredNotice] = useState(false);
  // 회의 일시 제안값(작업 88-4): 사용자가 날짜·시각을 직접 고친 적이 있으면 제안값으로 덮어쓰지 않는다
  const heldAtTouched = useRef(false);
  const heldAtSuggested = useRef(false);
  const [suggestedNotice, setSuggestedNotice] = useState(false);
  const recordStartedAt = useRef<number | null>(null);
  const dateRef = useRef<HTMLInputElement | null>(null);
  // 사용자가 자료 종류를 직접 고른 적이 있는가(그러면 파일을 골라도 종류를 자동으로 바꾸지 않는다)
  const kindPickedByUser = useRef(false);
  // 모바일(768 미만) 또는 터치 기기에서만 "녹음하기"를 보인다(작업 86-1a). PC 에서는 DOM 에도 없다
  const [recordable, setRecordable] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 767px), (pointer: coarse)");
    setRecordable(query.matches);
    const onChange = (event: MediaQueryListEvent) => setRecordable(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  // 등록 보류(작업 88-2): Wi-Fi 안내 팝업 대상, 보류 목록, 항목별 올리기 실패 사유
  const [wifiPrompt, setWifiPrompt] = useState<Submission | null>(null);
  const [deferError, setDeferError] = useState<string | null>(null);
  const [deferring, setDeferring] = useState(false);
  const [pending, setPending] = useState<PendingMeta[]>([]);
  const [pendingErrors, setPendingErrors] = useState<Record<string, string>>({});
  const [deferredNotice, setDeferredNotice] = useState<string | null>(null);
  useEffect(() => {
    if (!recordable || accountId === null) {
      setPending([]);
      return;
    }
    let cancelled = false;
    void listPending(accountId).then((list) => {
      if (!cancelled) setPending(list);
    });
    return () => {
      cancelled = true;
    };
  }, [recordable, accountId]);
  const [templateError, setTemplateError] = useState<string | null>(null);
  const [templateLoading, setTemplateLoading] = useState(false);
  const [pendingNotice, setPendingNotice] = useState<string | null>(null);

  // 새 프로젝트 등록에서 돌아온 경우(resume=1)만 임시 저장값을 복원한다. 메뉴로 들어오면 저장값을 버린다.
  // 처리한 인자(resume, newProject)는 주소에서 지워 새로고침해도 다시 적용되지 않게 한다.
  useEffect(() => {
    const key = searchParams.toString();
    if (handledQueryRef.current === key) return;
    handledQueryRef.current = key;
    const resume = searchParams.get("resume") === "1";
    const newProject = searchParams.get("newProject");
    if (!resume) {
      clearUploadDraft();
    } else {
      const draft = loadUploadDraft();
      if (draft) {
        setTitle(draft.title);
        if (draft.date) setDate(draft.date);
        if (draft.time) setTime(draft.time);
        if (draft.date || draft.time) heldAtTouched.current = true; // 복원된 값은 사용자가 입력한 값
        setParticipantIds(draft.participantIds);
        setMeetingType(draft.meetingType);
        setSourceKind(draft.sourceKind);
        kindPickedByUser.current = true; // 복원된 종류는 사용자가 고른 값
        setRestoredNotice(true);
      }
      clearUploadDraft();
    }
    if (resume && newProject && /^\d+$/.test(newProject)) {
      // 방금 만든 프로젝트: 진행 중이고 내 역할이 있으면 자동 선택, 승인 대기면 안내만. 조회 실패는 조용히 무시
      getProject(Number(newProject))
        .then((p) => {
          if (p.status === "active" && p.myRole) {
            setMeetingType("project");
            setProjectId(String(p.id));
          } else if (p.status === "pending_approval") {
            setPendingNotice(`새 프로젝트 '${p.name}'은 승인 대기 중입니다. 부서장 승인 후 선택할 수 있습니다.`);
          }
        })
        .catch(() => undefined);
    }
    if (key) router.replace(UPLOAD_PATH);
  }, [searchParams, router]);

  // 새 프로젝트 등록으로 떠나기 전에 입력값 저장(파일은 저장하지 않는다)
  function saveDraftForNewProject() {
    saveUploadDraft({ title, date, time, participantIds, meetingType, sourceKind });
  }

  // 화면을 떠나면 진행 중인 전송 취소
  useEffect(() => () => controllerRef.current?.abort(), []);

  /** 파일(또는 녹음)에서 회의 일시 제안값을 채운다. 사용자가 이미 직접 입력했으면 건드리지 않는다 */
  function suggestHeldAt(next: File, source: "file" | "record") {
    if (heldAtTouched.current) return;
    const startedAt = source === "record" ? recordStartedAt.current : null;
    const suggestion = suggestionFromTimestamp(startedAt ?? next.lastModified);
    if (suggestion) {
      setDate(suggestion.date);
      setTime(suggestion.time);
      heldAtSuggested.current = true;
      setSuggestedNotice(true);
    } else {
      setDate("");
      setTime("");
      heldAtSuggested.current = false;
      setSuggestedNotice(false);
    }
  }

  // 엑셀 화면 노출이 꺼져 있으면(기본) 엑셀 종류는 목록에서 빠진다. 서버 응답 뒤 숨김 상태에서 엑셀 종류가 남아 있으면 음성으로 돌린다
  const visibleKinds = SOURCE_KINDS.filter((code) => code !== "transcript_xlsx" || xlsxEnabled);
  useEffect(() => {
    if (optionsLoaded && !xlsxEnabled && sourceKind === "transcript_xlsx") setSourceKind("audio");
  }, [optionsLoaded, xlsxEnabled, sourceKind]);

  function pickFile(next: File | null, source: "file" | "record" = "file") {
    if (next) suggestHeldAt(next, source);
    // 확장자로 자료 종류를 자동 선택한다(.xlsx → 엑셀, .txt → txt, 음성 확장자 → 음성). 직접 바꾼 선택은 그대로 둔다
    const detected = next ? kindForFileName(next.name) : null;
    if (detected && detected !== sourceKind && !kindPickedByUser.current && (detected !== "transcript_xlsx" || xlsxEnabled)) setSourceKind(detected);
    setFile(next);
    setError(null);
    setFormError(null);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragOver(false);
    if (submitting) return;
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) pickFile(dropped, "file");
  }

  // 자료 종류를 바꾸면 고른 파일을 비운다(종류마다 받는 파일이 다르다)
  function pickSourceKind(next: SourceKind) {
    if (next === sourceKind && !pasteMode) return;
    kindPickedByUser.current = true;
    setPasteMode(false);
    setSourceKind(next);
    setFile(null);
    setError(null);
    setFormError(null);
  }

  /** 엑셀 양식 내려받기: 인증 헤더가 필요해 fetch 로 받아 저장한다. 실패해도 올리기 입력은 그대로 둔다 */
  async function onTemplateDownload() {
    setTemplateError(null);
    setTemplateLoading(true);
    try {
      const blob = await downloadTranscriptTemplate();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = TRANSCRIPT_TEMPLATE_FILE_NAME;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      if (isAbortError(err)) return;
      setTemplateError(`양식을 내려받지 못했습니다 · ${err instanceof Error && err.message ? err.message : "서버 오류"}`);
    } finally {
      setTemplateLoading(false);
    }
  }

  // 유형을 바꾸면 프로젝트 선택은 초기화한다(프로젝트 회의가 아니면 projectId 를 보내지 않는다)
  // 텍스트 직접 입력(작업 B-2): 자료 종류는 txt 로 보내고, 파일 대신 입력창의 글을 txt 파일로 만들어 올린다
  function pickPasteMode() {
    kindPickedByUser.current = true;
    setPasteMode(true);
    setSourceKind("transcript_txt");
    setFile(null);
    setError(null);
    setFormError(null);
  }

  function pickType(next: MeetingType) {
    setMeetingType(next);
    setProjectId("");
    setFormError(null);
  }

  async function submit() {
    if (submittingRef.current) return;
    let uploadFile = file;
    if (pasteMode) {
      // 줄바꿈 통일(CRLF·CR → LF)과 BOM 제거 후 검사한다. 빈 입력·최대 글자 수 초과는 서버를 부르지 않는다
      const normalized = pasteText.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n");
      if (!normalized.trim()) {
        setFormError("회의 내용을 입력해 주세요.");
        return;
      }
      if (normalized.length > pasteMaxChars) {
        setFormError(`입력한 글이 너무 깁니다(최대 ${pasteMaxChars.toLocaleString("ko-KR")}자, 현재 ${normalized.length.toLocaleString("ko-KR")}자).`);
        return;
      }
      const base = (title.trim() || "직접 입력 회의록").replace(/[\\/:*?"<>|]/g, "_").slice(0, 80);
      uploadFile = new File([normalized], `${base}.txt`, { type: "text/plain" });
    }
    if (!uploadFile) {
      setFormError(isTranscriptKind(sourceKind) ? `${SOURCE_KIND_LABEL[sourceKind]}을 선택하세요.` : "음성 파일을 선택하세요.");
      return;
    }
    const mismatch = kindMismatchHint(sourceKind, uploadFile.name);
    if (mismatch && !isTranscriptKind(sourceKind)) {
      setFormError(mismatch); // 음성 종류에 자료 파일: 서버를 부르지 않고 원인을 알린다
      return;
    }
    if (isTranscriptKind(sourceKind) && !matchesTranscriptExt(sourceKind, uploadFile.name)) {
      setFormError(`자료 파일은 .${transcriptExtOf(sourceKind)} 파일만 올릴 수 있습니다.`); // 서버를 부르지 않는다
      return;
    }
    const heldAt = toOffsetIso(date, time);
    if (!heldAt) {
      setFormError(MEETING_AT_REQUIRED_MESSAGE);
      dateRef.current?.focus();
      return;
    }
    if (isFutureMeetingAt(heldAt)) {
      setFormError(MEETING_AT_FUTURE_MESSAGE); // 서버도 같은 사유로 거부한다
      dateRef.current?.focus();
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
    setDeferredNotice(null);
    requestUpload({
      file: uploadFile,
      title: title.trim(),
      heldAt,
      participantIds,
      meetingType,
      projectId: meetingType === "project" ? Number(projectId) : undefined,
      sourceKind,
    });
  }

  /** 올리기 직전 판정: 모바일의 음성 파일이 wifi·ethernet 이 아니면(또는 알 수 없으면) 안내 팝업, 아니면 바로 올린다 */
  function requestUpload(submission: Submission) {
    if (submittingRef.current) return;
    if (recordable && submission.sourceKind === "audio" && shouldShowWifiNotice()) {
      setDeferError(null);
      setWifiPrompt(submission);
      return;
    }
    void runUpload(submission);
  }

  async function runUpload(submission: Submission) {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      const accepted = await uploadMeeting(
        {
          file: submission.file,
          title: submission.title,
          heldAt: submission.heldAt,
          participantIds: submission.participantIds,
          meetingType: submission.meetingType,
          projectId: submission.projectId,
          sourceKind: submission.sourceKind,
          engine: chosenEngine,
        },
        controller.signal,
      );
      clearUploadDraft();
      // 보류 목록에서 올린 건은 성공했을 때만 목록에서 지운다(실패하면 남겨 둔다)
      if (submission.fromPendingId) await removePending(submission.fromPendingId);
      refreshProcessing(); // 왼쪽 메뉴 처리 현황에 바로 나타나게 한다(같은 상태 저장소)
      setUploadNotice(accepted.meetingId);
      router.push(detailPath(accepted.meetingId));
    } catch (err) {
      if (isAbortError(err)) return;
      const message = err instanceof Error && err.message ? err.message : "올리지 못했습니다.";
      const pendingId = submission.fromPendingId;
      if (pendingId) setPendingErrors((prev) => ({ ...prev, [pendingId]: message })); // 목록에 남기고 사유를 항목 아래에 보인다
      else setError(message);
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  /** 안내 팝업 [업로드 계속] */
  function onWifiContinue() {
    const submission = wifiPrompt;
    setWifiPrompt(null);
    if (submission) void runUpload(submission);
  }

  /** 안내 팝업 [등록 보류]: 올리지 않고 기기 보류 목록에 저장한다(이미 목록에 있던 항목은 그대로 둔다) */
  async function onWifiDefer() {
    const submission = wifiPrompt;
    if (!submission || deferring) return;
    if (submission.fromPendingId) {
      setWifiPrompt(null);
      return;
    }
    if (accountId === null) {
      setDeferError(PENDING_UNAVAILABLE_MESSAGE);
      return;
    }
    setDeferring(true);
    setDeferError(null);
    try {
      const saved = await addPending(accountId, {
        file: submission.file,
        sourceKind: submission.sourceKind,
        meetingType: submission.meetingType,
        title: submission.title,
        heldAt: submission.heldAt,
        participantIds: submission.participantIds,
        projectId: submission.projectId ?? null,
      });
      setPending((prev) => [...prev, saved]);
      setWifiPrompt(null);
      setFile(null); // 같은 파일이 입력칸과 보류 목록에 둘 다 있지 않게 한다
      setDeferredNotice("등록을 보류했습니다. 아래 등록 보류 목록에서 Wi-Fi 연결 후 올려 주세요.");
    } catch (err) {
      setDeferError(err instanceof PendingStorageError ? err.message : PENDING_UNAVAILABLE_MESSAGE);
    } finally {
      setDeferring(false);
    }
  }

  /** 보류 목록 [지금 올리기]: 같은 업로드 흐름(안내 판정 포함) */
  async function onPendingUploadNow(item: PendingMeta) {
    if (submittingRef.current) return;
    setPendingErrors((prev) => {
      const { [item.id]: _removed, ...rest } = prev;
      return rest;
    });
    const stored = await getPendingFile(item.id);
    if (!stored) {
      setPendingErrors((prev) => ({ ...prev, [item.id]: "기기에 저장된 파일을 찾지 못했습니다. 목록에서 삭제한 뒤 다시 선택해 주세요." }));
      return;
    }
    requestUpload({
      file: stored,
      title: item.title,
      heldAt: item.heldAt,
      participantIds: item.participantIds,
      meetingType: item.meetingType as MeetingType,
      projectId: item.projectId ?? undefined,
      sourceKind: sourceKindOf(item.sourceKind),
      fromPendingId: item.id,
    });
  }

  async function onPendingDelete(item: PendingMeta) {
    if (await removePending(item.id)) {
      setPending((prev) => prev.filter((entry) => entry.id !== item.id));
      setPendingErrors((prev) => {
        const { [item.id]: _removed, ...rest } = prev;
        return rest;
      });
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
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <form
          onSubmit={onSubmit}
          noValidate
          aria-label="회의록 올리기"
          className="flex flex-col gap-5 rounded-mn-card border border-mn-border bg-mn-surface p-6"
        >
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-2 text-sm font-medium">자료 종류</legend>
            <div className="flex flex-wrap gap-2">
              {visibleKinds.map((code) => {
                const checked = sourceKind === code && !pasteMode;
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
                      type="radio"
                      name="sourceKind"
                      value={code}
                      checked={checked}
                      disabled={submitting}
                      onChange={() => pickSourceKind(code)}
                      className="sr-only"
                    />
                    <span aria-hidden="true" className="font-mn-mono">
                      {checked ? "●" : "○"}
                    </span>
                    {SOURCE_KIND_LABEL[code]}
                  </label>
                );
              })}
              <label
                className={[
                  "inline-flex min-h-8 cursor-pointer items-center gap-2 rounded-mn-control border px-3 py-1 text-[13px]",
                  "has-[:focus-visible]:[box-shadow:var(--mn-focus-ring)] has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-50",
                  pasteMode ? "border-mn-accent bg-mn-elevated font-semibold text-mn-text" : "border-mn-control text-mn-muted hover:text-mn-text",
                ].join(" ")}
              >
                <input type="radio" name="sourceKind" value="paste" checked={pasteMode} disabled={submitting} onChange={pickPasteMode} className="sr-only" />
                <span aria-hidden="true" className="font-mn-mono">
                  {pasteMode ? "●" : "○"}
                </span>
                텍스트 직접 입력
              </label>
            </div>
            {pasteMode ? (
              <p role="status" className="text-xs text-mn-text">
                회의 내용을 붙여 넣으면 음성 인식 없이 바로 업무를 추출합니다. "[00:01:23] 화자1: 내용" 형식이면 근거 위치를 볼 수 있고, 시각 표기가 없으면 "근거 위치 보기"는 쓸 수 없습니다.
              </p>
            ) : null}
            {sourceKind === "transcript_txt" && !pasteMode ? (
              <p role="status" className="text-xs text-mn-text">
                전사문(txt)을 올리면 음성 인식 없이 바로 업무를 추출합니다. 시각 표기가 없으면 "근거 위치 보기"는 쓸 수 없습니다.
              </p>
            ) : null}
            {sourceKind === "transcript_xlsx" ? (
              <div className="flex flex-col gap-2">
                <p role="status" className="text-xs text-mn-text">
                  엑셀(.xlsx)을 올리면 음성 인식 없이 바로 업무를 추출합니다. 첫 행에 "발언"(또는 내용) 열이 필요하고 "시각"·"화자" 열은 있으면 읽습니다. 화자 칸이 비어 있으면 화자 표시 없이 적은 그대로 보입니다. 시각이 없으면 "근거 위치 보기"는 쓸 수 없습니다. 비밀번호가 걸린 파일과 매크로 파일은 올릴 수 없습니다.
                </p>
                <div className="flex flex-wrap items-center gap-3">
                  <Button type="button" size="sm" variant="secondary" disabled={templateLoading || submitting} onClick={() => void onTemplateDownload()}>
                    {templateLoading ? "양식 준비 중…" : "엑셀 양식 내려받기"}
                  </Button>
                  {templateError ? (
                    <span role="alert" className="text-xs">
                      <StatusDot tone="error" label={templateError} />
                    </span>
                  ) : null}
                </div>
              </div>
            ) : null}
          </fieldset>

          {/* 텍스트 직접 입력(작업 B-2): 파일 대신 입력창. 최대 글자 수는 서버 옵션(PASTE_INPUT_MAX_CHARS) */}
          {pasteMode ? (
            <div className="flex flex-col gap-2">
              <label htmlFor={pasteId} className="text-sm font-medium">
                회의 내용
              </label>
              <textarea
                id={pasteId}
                value={pasteText}
                rows={12}
                disabled={submitting}
                onChange={(event) => {
                  setPasteText(event.target.value);
                  setFormError(null);
                }}
                aria-describedby={`${pasteId}-count`}
                placeholder={"[00:00:10] 화자1: 오늘 회의를 시작하겠습니다.\n[00:00:20] 화자2: 네, 안건부터 보겠습니다."}
                className="mn-focus w-full resize-y rounded-mn-control border border-mn-control bg-mn-bg px-3 py-2 font-mn-mono text-sm text-mn-text outline-none placeholder:text-mn-muted disabled:opacity-50"
              />
              <p id={`${pasteId}-count`} className={`text-xs ${pasteText.length > pasteMaxChars ? "text-mn-text" : "text-mn-muted"}`}>
                {pasteText.length.toLocaleString("ko-KR")} / {pasteMaxChars.toLocaleString("ko-KR")}자
                {pasteText.length > pasteMaxChars ? " · 글자 수를 넘었습니다. 줄여 주세요." : ""}
              </p>
            </div>
          ) : null}
          {/* 파일: 끌어다 놓기 영역 + 공용 FilePicker(버튼·상태 글자·숨긴 입력) */}
          {!pasteMode ? (
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
            <p className="text-sm font-medium">{isTranscriptKind(sourceKind) ? `${SOURCE_KIND_LABEL[sourceKind]}을 끌어다 놓거나 선택하세요` : "음성 파일을 끌어다 놓거나 선택하세요"}</p>
            <p id={fileHintId} className="text-xs text-mn-muted">
              허용 형식과 최대 용량은 서버 설정을 따르며, 벗어나면 올릴 때 알려 드립니다.
            </p>
            <FilePicker
              file={file}
              buttonLabel={isTranscriptKind(sourceKind) ? "자료 파일 선택" : "음성 파일 선택"}
              inputLabel={isTranscriptKind(sourceKind) ? "자료 파일 선택" : "음성 파일 선택"}
              accept={
                sourceKind === "transcript_txt"
                  ? ".txt,text/plain"
                  : sourceKind === "transcript_xlsx"
                    ? ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    : xlsxEnabled
                      ? "audio/*,video/mp4,video/webm,.xlsx,.txt" // 자료 파일도 고를 수 있게 하고, 고르면 자료 종류를 자동으로 맞춘다
                      : "audio/*,video/mp4,video/webm,.txt" // 엑셀 화면 노출이 꺼져 있으면 xlsx 는 고르는 목록에서 뺀다(작업 B-2)
              }
              describedBy={fileHintId}
              recordLabel={recordable && sourceKind === "audio" ? "녹음하기" : undefined}
              disabled={submitting}
              className="justify-center"
              wrapName
              onPick={pickFile}
              onRecordClick={() => {
                recordStartedAt.current = Date.now(); // 녹음 시작 시각(회의 일시 제안값)
              }}
            />
            {file && kindMismatchHint(sourceKind, file.name) ? (
              <p role="status" className="text-sm">
                <StatusDot tone="error" label={kindMismatchHint(sourceKind, file.name) ?? ""} />
              </p>
            ) : null}
          </div>
          ) : null}

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
                ref={dateRef}
                onChange={(event) => {
                  heldAtTouched.current = true;
                  setSuggestedNotice(false);
                  setDate(event.target.value);
                }}
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
                onChange={(event) => {
                  heldAtTouched.current = true;
                  setSuggestedNotice(false);
                  setTime(event.target.value);
                }}
                className={`${inputClass} w-[120px] font-mn-mono`}
              />
              <span className="text-xs text-mn-muted">이 컴퓨터의 현지 시각</span>
            </div>
            <span className="text-xs text-mn-muted">상대 날짜(다음 주 화요일 등)를 이 일시 기준으로 계산합니다.</span>
            {suggestedNotice ? (
              <span role="status" className="text-xs text-mn-text">
                파일의 시각을 제안값으로 채웠습니다. 실제 회의가 열린 일시가 맞는지 확인해 주세요.
              </span>
            ) : !date || !time ? (
              <span className="text-xs text-mn-muted">필수 입력입니다. 회의가 열린 날짜와 시각을 입력해 주세요.</span>
            ) : null}
          </fieldset>

          {engineSelectable && chosenEngine ? <EngineChoiceField value={chosenEngine} onChange={setEngineChoice} disabled={submitting} /> : null}

          <MeetingTypeField value={meetingType} onChange={pickType} disabled={submitting} firstRef={typeRef} />
          {meetingType === "project" ? <ProjectField value={projectId} onChange={setProjectId} disabled={submitting} selectRef={projectRef} onLeaveForNewProject={saveDraftForNewProject} /> : null}

          {restoredNotice ? (
            <p role="status" className="text-sm text-mn-text">
              입력하던 내용을 복원했습니다. 파일은 다시 선택해 주세요.
            </p>
          ) : null}
          {pendingNotice ? (
            <p role="status" className="text-sm text-mn-text">
              {pendingNotice}
            </p>
          ) : null}

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

          {deferredNotice ? (
            <p role="status" className="text-sm text-mn-text">
              {deferredNotice}
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

      {/* 등록 보류(모바일만, 작업 88-2) */}
      {recordable ? (
        <PendingUploadList
          items={pending}
          errors={pendingErrors}
          busy={submitting}
          onUploadNow={(item) => void onPendingUploadNow(item)}
          onDelete={onPendingDelete}
        />
      ) : null}
      <WifiNoticeDialog
        open={wifiPrompt !== null}
        alreadyPending={wifiPrompt?.fromPendingId !== undefined}
        error={deferError}
        deferring={deferring}
        onContinue={onWifiContinue}
        onDefer={() => void onWifiDefer()}
        onClose={() => setWifiPrompt(null)}
      />
    </>
  );
}
