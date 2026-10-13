/*
 * 등록 보류 목록: 기기 로컬 저장소(IndexedDB)(작업 88-2). 파일(Blob)과 메타데이터만 이 기기 브라우저에 저장하고 서버로 보내지 않는다.
 * - 모든 접근은 try/catch 로 감싼다. IndexedDB 를 쓸 수 없는 환경(시크릿 모드 등)이면 PendingStorageError("unavailable").
 * - 계정별로 나눠 보여 준다(같은 기기를 여럿이 쓸 수 있음): accountId 가 같은 항목만 목록·한도 계산 대상이다.
 * - 한도(건수·합계 용량)는 아래 PENDING_LIMITS 한 곳에서 정한다. 환경변수 NEXT_PUBLIC_PENDING_UPLOAD_MAX_COUNT(기본 5)·
 *   NEXT_PUBLIC_PENDING_UPLOAD_MAX_MB(기본 300)로 바꿀 수 있다. 판정은 pending-limits.ts(순수 함수).
 * - 자동 재업로드는 없다. 올리기는 항상 사용자가 [지금 올리기]를 눌러 시작한다.
 */
import { checkPendingLimits, describeViolation, type PendingLimits } from "./pending-limits";

function positiveNumber(raw: string | undefined, fallback: number): number {
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

export const PENDING_LIMITS: PendingLimits = {
  maxCount: Math.floor(positiveNumber(process.env.NEXT_PUBLIC_PENDING_UPLOAD_MAX_COUNT, 5)),
  maxTotalBytes: positiveNumber(process.env.NEXT_PUBLIC_PENDING_UPLOAD_MAX_MB, 300) * 1024 * 1024,
};

export const PENDING_UNAVAILABLE_MESSAGE = "이 기기에서는 보류할 수 없습니다. Wi-Fi에서 다시 시도해 주세요.";

const DB_NAME = "mi-v2-pending-uploads";
const STORE = "uploads";
const DB_VERSION = 1;

export type PendingErrorKind = "unavailable" | "limit" | "failed";

export class PendingStorageError extends Error {
  constructor(
    public readonly kind: PendingErrorKind,
    message: string,
  ) {
    super(message);
    this.name = "PendingStorageError";
  }
}

/** 목록·재업로드에 쓰는 메타데이터(파일 본문 제외) */
export interface PendingMeta {
  id: string;
  accountId: number;
  fileName: string;
  size: number;
  mimeType: string;
  /** 자료 종류(audio | transcript_txt | transcript_xlsx) */
  sourceKind: string;
  /** 회의 유형 코드 */
  meetingType: string;
  title: string;
  /** 회의 일시(오프셋 붙은 ISO) */
  heldAt: string;
  participantIds: number[];
  projectId: number | null;
  /** 보류한 시각(ISO, UTC) */
  pendingAt: string;
}

interface PendingRecord extends PendingMeta {
  blob: Blob;
}

export type NewPending = Omit<PendingMeta, "id" | "accountId" | "fileName" | "size" | "mimeType" | "pendingAt"> & { file: File };

const unavailable = () => new PendingStorageError("unavailable", PENDING_UNAVAILABLE_MESSAGE);

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    try {
      if (typeof indexedDB === "undefined" || indexedDB === null) {
        reject(unavailable());
        return;
      }
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains(STORE)) request.result.createObjectStore(STORE, { keyPath: "id" });
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(unavailable());
      request.onblocked = () => reject(unavailable());
    } catch {
      reject(unavailable());
    }
  });
}

/** 한 번의 요청을 약속으로 감싸고 DB 연결을 닫는다 */
async function withStore<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDb();
  try {
    return await new Promise<T>((resolve, reject) => {
      try {
        const tx = db.transaction(STORE, mode);
        const request = run(tx.objectStore(STORE));
        tx.oncomplete = () => resolve(request.result);
        tx.onerror = () => reject(new PendingStorageError("failed", "보류 저장소를 사용하지 못했습니다."));
        tx.onabort = () => reject(new PendingStorageError("failed", "보류 저장소를 사용하지 못했습니다."));
      } catch {
        reject(new PendingStorageError("failed", "보류 저장소를 사용하지 못했습니다."));
      }
    });
  } finally {
    db.close();
  }
}

const getAllRecords = () => withStore<PendingRecord[]>("readonly", (store) => store.getAll() as IDBRequest<PendingRecord[]>);

function toMeta(record: PendingRecord): PendingMeta {
  const { blob: _blob, ...meta } = record;
  return meta;
}

/** IndexedDB 를 열 수 있는가(보류 가능 여부). 예외는 던지지 않는다 */
export async function isPendingStorageAvailable(): Promise<boolean> {
  try {
    (await openDb()).close();
    return true;
  } catch {
    return false;
  }
}

/** 이 계정의 보류 목록(오래된 순). 저장소를 못 쓰면 빈 목록 */
export async function listPending(accountId: number): Promise<PendingMeta[]> {
  try {
    const all = await getAllRecords();
    return all
      .filter((record) => record.accountId === accountId)
      .map(toMeta)
      .sort((a, b) => a.pendingAt.localeCompare(b.pendingAt));
  } catch {
    return [];
  }
}

/** 보류에 저장한다. 한도(건수·합계 용량)를 넘으면 저장하지 않고 PendingStorageError("limit") */
export async function addPending(accountId: number, input: NewPending): Promise<PendingMeta> {
  let existing: PendingRecord[];
  try {
    existing = await getAllRecords();
  } catch {
    throw unavailable();
  }
  const mine = existing.filter((record) => record.accountId === accountId);
  const violation = checkPendingLimits(
    mine.map((record) => record.size),
    input.file.size,
    PENDING_LIMITS,
  );
  if (violation) throw new PendingStorageError("limit", describeViolation(violation));
  const { file, ...rest } = input;
  const record: PendingRecord = {
    ...rest,
    id: typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    accountId,
    fileName: file.name,
    size: file.size,
    mimeType: file.type,
    pendingAt: new Date().toISOString(),
    blob: file,
  };
  try {
    await withStore<IDBValidKey>("readwrite", (store) => store.add(record));
  } catch {
    throw unavailable(); // 저장 공간 부족 등: 사용 불가와 같은 안내
  }
  return toMeta(record);
}

/** 보류 항목의 파일을 File 로 되돌린다(없으면 null) */
export async function getPendingFile(id: string): Promise<File | null> {
  try {
    const record = await withStore<PendingRecord | undefined>("readonly", (store) => store.get(id) as IDBRequest<PendingRecord | undefined>);
    return record ? new File([record.blob], record.fileName, { type: record.mimeType }) : null;
  } catch {
    return null;
  }
}

/** 보류 항목을 지운다(기기에 임시 저장된 파일만 지워진다). 실패하면 false */
export async function removePending(id: string): Promise<boolean> {
  try {
    await withStore<undefined>("readwrite", (store) => store.delete(id) as IDBRequest<undefined>);
    return true;
  } catch {
    return false;
  }
}
