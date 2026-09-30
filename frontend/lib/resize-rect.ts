/*
 * 창 크기 조절 계산 (순수 함수, import 없음 → node --test 로 바로 단위 테스트)
 * 끄는 변만 움직이고 반대쪽 변은 고정한다. 결과는 최소 크기 이상, 화면(여백 제외) 안.
 */

export interface Rect {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** 끄는 방향: x/y 각각 -1(왼쪽/위쪽 변), 0(안 움직임), 1(오른쪽/아래쪽 변) */
export interface ResizeEdge {
  x: -1 | 0 | 1;
  y: -1 | 0 | 1;
}

export interface Limits {
  /** 화면 폭·높이 (px) */
  viewportWidth: number;
  viewportHeight: number;
  /** 창이 화면 가장자리와 띄울 여백 (px) */
  margin: number;
  /** 창 최소 폭·높이 (px) */
  minWidth: number;
  minHeight: number;
}

/**
 * 시작 위치(start)에서 포인터가 (dx, dy)만큼 움직였을 때의 새 창 위치.
 * 끄는 변만 움직이고, 최소 크기보다 작아지거나 화면 여백 밖으로 나가지 않게 멈춘다.
 */
export function resizeRect(start: Rect, edge: ResizeEdge, dx: number, dy: number, limits: Limits): Rect {
  const { viewportWidth, viewportHeight, margin, minWidth, minHeight } = limits;
  let { left, top, right, bottom } = start;
  if (edge.x === 1) right = clampRange(right + dx, left + minWidth, viewportWidth - margin);
  if (edge.x === -1) left = clampRange(left + dx, margin, right - minWidth);
  if (edge.y === 1) bottom = clampRange(bottom + dy, top + minHeight, viewportHeight - margin);
  if (edge.y === -1) top = clampRange(top + dy, margin, bottom - minHeight);
  return { left, top, right, bottom };
}

/**
 * 화면 크기가 바뀌었을 때 등: 창을 화면(여백 제외) 안으로 넣는다.
 * 먼저 크기를 화면 한도로 줄이고(최소 크기는 지킴), 그다음 위치를 안쪽으로 민다.
 */
export function fitRect(rect: Rect, limits: Limits): Rect {
  const { viewportWidth, viewportHeight, margin, minWidth, minHeight } = limits;
  const width = clampRange(rect.right - rect.left, minWidth, viewportWidth - 2 * margin);
  const height = clampRange(rect.bottom - rect.top, minHeight, viewportHeight - 2 * margin);
  const left = clampRange(rect.left, margin, viewportWidth - margin - width);
  const top = clampRange(rect.top, margin, viewportHeight - margin - height);
  return { left, top, right: left + width, bottom: top + height };
}

/** 가운데 정렬된 창을 rect 자리로 옮기려면 얼마나 밀어야 하는지 (translate 값) */
export function offsetFromCenter(rect: Rect, viewportWidth: number, viewportHeight: number): { x: number; y: number } {
  const width = rect.right - rect.left;
  const height = rect.bottom - rect.top;
  return { x: rect.left - (viewportWidth - width) / 2, y: rect.top - (viewportHeight - height) / 2 };
}

/** min~max 로 자른다. 화면이 최소 크기보다 작으면(max < min) min 을 지킨다 */
function clampRange(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max));
}
