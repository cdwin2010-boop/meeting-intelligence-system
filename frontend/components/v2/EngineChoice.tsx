"use client";

/*
 * 엔진 시험 모드(작업 91-1, 시험 전용) 화면 조각.
 * - useEngineTestMode: GET /api/system/engine 의 engineTestMode·engineTestDefault. 꺼져 있거나 조회 실패면 모두 숨긴다(기본값 = 납품 설정).
 * - EngineChoiceField: 회의록 올리기에서 처리 엔진을 고르는 라디오(관리자 이상, 시험 모드일 때만 그려진다).
 * 화면 숨김은 편의이고 실제 거부는 서버가 한다(시험 모드가 꺼져 있으면 engine 값이 있는 요청은 403).
 */
import { useEffect, useId, useState } from "react";

import type { ReasonChoice } from "@/components/v2/ReasonDialog";
import { isAbortError } from "@/lib/v2/errors";
import { ENGINE_CHOICE_LABEL, ENGINE_TEST_NOTICE, type EngineChoice } from "@/lib/v2/engine-test";
import { getEngine } from "@/lib/v2/system";

export interface EngineTestInfo {
  testMode: boolean;
  defaultEngine: EngineChoice;
}

const OFF: EngineTestInfo = { testMode: false, defaultEngine: "default" };

export function useEngineTestMode(enabled = true): EngineTestInfo {
  const [info, setInfo] = useState<EngineTestInfo>(OFF);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    getEngine(controller.signal)
      .then((engine) =>
        setInfo({
          testMode: engine.engineTestMode === true,
          defaultEngine: engine.engineTestDefault === "remote" ? "remote" : "default",
        }),
      )
      .catch((error) => {
        if (!isAbortError(error)) setInfo(OFF);
      });
    return () => controller.abort();
  }, [enabled]);
  return info;
}

/** 재처리 팝업(ReasonDialog)의 처리 엔진 선택 그룹. 기본값은 서버 설정값(ENGINE_TEST_DEFAULT) */
export function engineChoiceOf(defaultEngine: EngineChoice): ReasonChoice {
  return {
    legend: "처리 엔진(시험용)",
    options: (["default", "remote"] as const).map((value) => ({ value, label: ENGINE_CHOICE_LABEL[value] })),
    requiredMessage: "처리 엔진을 선택해 주세요",
    defaultValue: defaultEngine,
  };
}

export function EngineChoiceField({
  value,
  onChange,
  disabled,
}: {
  value: EngineChoice;
  onChange: (value: EngineChoice) => void;
  disabled?: boolean;
}) {
  const name = useId();
  return (
    <fieldset className="flex flex-col gap-2" aria-label="처리 엔진">
      <legend className="text-sm font-medium">
        처리 엔진 <span className="font-normal text-mn-muted">(시험용)</span>
      </legend>
      <div className="flex flex-wrap gap-2">
        {(["default", "remote"] as const).map((option) => (
          <label
            key={option}
            className={`mn-focus-within inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-mn-control border px-3 text-sm ${
              value === option ? "border-mn-accent bg-mn-elevated font-medium" : "border-mn-control text-mn-muted"
            }`}
          >
            <input
              type="radio"
              name={name}
              value={option}
              checked={value === option}
              disabled={disabled}
              onChange={() => onChange(option)}
              className="size-4"
            />
            {ENGINE_CHOICE_LABEL[option]}
          </label>
        ))}
      </div>
      <p className="text-xs text-mn-muted">{ENGINE_TEST_NOTICE}</p>
    </fieldset>
  );
}
