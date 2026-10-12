"""GPU 를 감지하고 권장 모델 프로파일과 권장 .env 키 값을 출력한다. 어떤 파일도 쓰지 않는다(.env 자동 수정 없음, 출력만).

사용법 (backend 폴더에서):
    python scripts/detect_hardware.py            # 사람이 읽는 출력
    python scripts/detect_hardware.py --json     # JSON 출력
    python scripts/detect_hardware.py --profile gpu_12gb   # 프로파일을 지정(감지 생략, LLM_PROFILE 과 같은 의미)
"""
import argparse
import json
import sys
from pathlib import Path

# backend 폴더를 import 경로에 추가(scripts/ 에서 실행해도 app 패키지를 찾도록)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.platform.hardware import HardwareReport, detect_gpus  # noqa: E402
from app.platform.profiles import ProfileConfigError, Recommendation, load_profiles, resolve_profile  # noqa: E402


def build_payload(rec: Recommendation, report: HardwareReport | None, draft_notice: str) -> dict:
    gpus = []
    if report is not None:
        gpus = [
            {"index": g.index, "name": g.name, "totalMb": g.total_mb, "freeMb": g.free_mb, "driverVersion": g.driver_version,
             "target": report.target is not None and g.index == report.target.index}
            for g in report.gpus
        ]
    p = rec.profile
    return {
        "detectionMethod": report.method if report is not None else "skipped",
        "gpus": gpus,
        "profile": {"id": p.id, "description": p.description, "stt": p.stt.model_dump(), "llm": p.llm.model_dump(), "notes": p.notes},
        "reasons": list(rec.reasons),
        "warnings": list(rec.warnings),
        "env": rec.settings,
        "fixed": rec.fixed,
        "draftNotice": draft_notice,
    }


def render_text(payload: dict) -> str:
    lines = [f"감지 방법: {payload['detectionMethod']}"]
    if payload["gpus"]:
        for g in payload["gpus"]:
            mark = " (대상)" if g["target"] else ""
            driver = f", 드라이버 {g['driverVersion']}" if g["driverVersion"] else ""
            lines.append(f"GPU {g['index']}: {g['name']} — VRAM 총 {g['totalMb']}MB / 여유 {g['freeMb']}MB{driver}{mark}")
    elif payload["detectionMethod"] != "skipped":
        lines.append("GPU: 없음")
    p = payload["profile"]
    lines += [
        "",
        f"권장 프로파일: {p['id']} — {p['description']}",
        f"  STT: {p['stt']['model']} ({p['stt']['precision']})",
        f"  LLM: {p['llm']['model']} ({p['llm']['quantization']})",
    ]
    if p["notes"]:
        lines.append(f"  비고: {p['notes']}")
    lines += ["", "이유:"] + [f"  - {r}" for r in payload["reasons"]]
    if payload["warnings"]:
        lines += ["", "경고:"] + [f"  - {w}" for w in payload["warnings"]]
    lines += ["", "권장 .env 키 값(자동 적용하지 않습니다. 확인 후 backend/.env 에 직접 반영하세요):"]
    lines += [f"  {k}={v}" for k, v in payload["env"].items()] or ["  (없음)"]
    lines += ["", f"※ {payload['draftNotice']}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None, *, detector=detect_gpus, out=None) -> int:
    parser = argparse.ArgumentParser(description="GPU 감지와 권장 모델 프로파일 출력(파일을 쓰지 않음)")
    parser.add_argument("--json", action="store_true", help="JSON 으로 출력")
    parser.add_argument("--profile", default=None, help="프로파일 id 를 지정하면 감지하지 않는다(기본: LLM_PROFILE 설정)")
    parser.add_argument("--profiles-path", default=None, help="프로파일 파일 경로(기본: HARDWARE_PROFILES_PATH)")
    args = parser.parse_args(argv)
    stream = out or sys.stdout
    try:
        profiles = load_profiles(args.profiles_path or settings.hardware_profiles_path)
        rec, report = resolve_profile(
            args.profile or settings.llm_profile, profiles, detector, settings.hardware_min_free_ratio
        )
    except ProfileConfigError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    payload = build_payload(rec, report, profiles.draft_notice)
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else render_text(payload), file=stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
