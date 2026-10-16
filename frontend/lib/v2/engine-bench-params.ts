/*
 * 벤치마크 변수 입력 검사·프리셋 수정 표시(작업 L-5). 기본값·허용 범위·프리셋은 서버가 내려준 값(bench_params.json 한 곳)만 쓴다(코드에 박지 않는다).
 * 서버도 같은 범위로 다시 검사한다(화면 검사는 편의).
 */
import type { BenchParamDef, BenchPreset } from "./engine-bench";

export type ParamTexts = Record<string, string>;

export function fmt(value: number): string {
  return Number.isInteger(value) ? String(value) : String(value);
}

/** 변수 한 칸의 오류 문장("무엇이/왜/얼마로"). 정상이면 null */
export function fieldError(def: BenchParamDef, text: string): string | null {
  const raw = text.trim();
  if (raw === "") {
    return def.autoValue !== null ? null : `${def.label}을(를) 입력하세요. 기본값은 ${fmt(def.default)}${def.unit}입니다.`;
  }
  if (!/^\d+(\.\d+)?$/.test(raw)) return `${def.label}에는 숫자만 입력할 수 있습니다. ${fmt(def.default)} 같은 숫자로 바꾸세요.`;
  const value = Number(raw);
  if (def.autoValue !== null && value === def.autoValue) return null;
  if (def.type === "int" && !Number.isInteger(value)) return `${def.label}은(는) 정수여야 합니다. ${Math.round(value)} 처럼 소수점 없이 바꾸세요.`;
  if (value < def.min || value > def.max) {
    const clamped = Math.min(Math.max(value, def.min), def.max);
    return `${def.label}은(는) ${fmt(def.min)}~${fmt(def.max)} 범위여야 합니다. 입력한 ${fmt(value)} 은(는) 범위를 벗어납니다. ${fmt(clamped)} 으로 바꾸거나 기본값 ${fmt(def.default)} 을(를) 쓰세요.`;
  }
  return null;
}

/** 화면 텍스트 → 숫자 값(빈칸은 자동값). 오류가 있으면 null */
export function toValues(defs: BenchParamDef[], texts: ParamTexts): Record<string, number> | null {
  const out: Record<string, number> = {};
  for (const def of defs) {
    const text = (texts[def.key] ?? "").trim();
    if (fieldError(def, text) !== null) return null;
    out[def.key] = text === "" ? (def.autoValue ?? def.default) : Number(text);
  }
  return out;
}

export function textsOf(values: Record<string, number>): ParamTexts {
  const out: ParamTexts = {};
  for (const [key, value] of Object.entries(values)) out[key] = String(value);
  return out;
}

/** 프리셋 값(없으면 기본값만)을 기준으로 수정된 항목 목록 */
export function modifiedItems(
  defs: BenchParamDef[],
  defaults: Record<string, number>,
  preset: BenchPreset | null,
  values: Record<string, number>,
): { key: string; label: string; value: number }[] {
  const base = { ...defaults, ...(preset?.values ?? {}) };
  return defs.filter((d) => values[d.key] !== base[d.key]).map((d) => ({ key: d.key, label: d.label, value: values[d.key] }));
}

export function presetSummary(preset: BenchPreset | null, changed: { label: string; value: number }[]): string {
  if (!preset) return changed.length > 0 ? "프리셋 없음(화면 입력)" : "프리셋 없음(기본값)";
  if (changed.length === 0) return `프리셋 ${preset.id} 적용됨`;
  return `프리셋 ${preset.id} 기반, 수정됨(${changed.map((c) => `${c.label}: ${fmt(c.value)}`).join(", ")})`;
}
