import type { SortDirection } from "./types";

type SortOf<K extends string> = { key: K; direction: SortDirection } | null;

/**
 * 단일 열 3-State 정렬: 없음 → 오름차순 → 내림차순 → 없음.
 * 다른 열을 누르면 그 열의 오름차순부터 시작합니다. (열 키 타입을 제네릭 K로 받아 테이블마다 재사용)
 */
export function nextSort<K extends string>(current: SortOf<K>, key: K): SortOf<K> {
  if (current?.key !== key) return { key, direction: "asc" };
  if (current.direction === "asc") return { key, direction: "desc" };
  return null;
}

/** 현재 정렬 상태 → aria-sort 값 (스크린리더가 정렬 방향을 읽어줍니다) */
export function getAriaSort<K extends string>(
  sort: SortOf<K>,
  key: K,
): "ascending" | "descending" | "none" {
  if (sort?.key !== key) return "none";
  return sort.direction === "asc" ? "ascending" : "descending";
}
