"""모델 프로파일(작업 77-1): 설정 파일(config/model_profiles.json)을 Pydantic 으로 검증해 읽고, 감지 결과로 권장 프로파일을 고른다.
코드에는 모델 이름을 두지 않는다(이름·양자화는 설정 파일에만). 모델 파일·다운로드 주소는 다루지 않는다.
현행 처리 파이프라인에서는 쓰지 않는다(도구·테스트 전용)."""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.config import Settings
from app.platform.hardware import HardwareReport

DEFAULT_PROFILES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "model_profiles.json"
AUTO = "auto"


class ProfileConfigError(ValueError):
    """프로파일 파일이 없거나 형식·값이 잘못됨. 메시지는 사람이 그대로 읽어도 되는 문구."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")  # 알 수 없는 키는 오류


class SttSpec(_Strict):
    model: str = Field(min_length=1)
    precision: str = Field(min_length=1)


TIER_PERMISSIVE = "permissive"  # 자유 라이선스: 기본 선택 대상이 될 수 있다
TIER_CONDITIONAL = "conditional"  # 조건부 라이선스: 고객 요청 시에만 쓴다(기본 선택 대상 아님)


class LlmCandidate(_Strict):
    """시험·비교 후보 모델. 이름·양자화·라이선스 표기·비고만 담는다(모델 파일·다운로드 주소는 넣지 않는다)."""

    model: str = Field(min_length=1)
    quantization: str = ""
    license: str = Field(min_length=1)  # 확인된 것만 확정 표기, 아니면 "모델 카드 확인 필요"
    tier: Literal["permissive", "conditional"]
    selectable_by_default: bool
    note: str = ""

    @model_validator(mode="after")
    def _conditional_is_not_default(self) -> "LlmCandidate":
        if self.tier == TIER_CONDITIONAL and self.selectable_by_default:
            raise ValueError(f"후보 {self.model}: tier=conditional 은 selectable_by_default 가 거짓이어야 합니다.")
        return self


class LlmSpec(_Strict):
    """주력(primary) 모델은 model·quantization 이며 확장 필드(license, tier, options, candidates)는 모두 선택이다
    (기존 파일 형식도 그대로 통과한다)."""

    model: str = Field(min_length=1)
    quantization: str = Field(min_length=1)
    license: str = ""
    tier: Literal["permissive", "conditional"] = TIER_PERMISSIVE  # 주력은 반드시 permissive
    note: str = ""
    options: dict[str, str] = Field(default_factory=dict)  # 예: {"thinking_mode": "off"}
    candidates: list[LlmCandidate] = Field(default_factory=list)

    @field_validator("tier")
    @classmethod
    def _primary_is_permissive(cls, value: str) -> str:
        if value != TIER_PERMISSIVE:
            raise ValueError("주력(primary) 모델의 tier 는 permissive 여야 합니다.")
        return value


class Profile(_Strict):
    id: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    description: str = Field(min_length=1)
    min_vram_mb: int = Field(ge=0)  # 이상
    max_vram_mb: int | None = Field(default=None, gt=0)  # 미만(None 이면 상한 없음)
    stt: SttSpec
    llm: LlmSpec
    settings: dict[str, str] = Field(default_factory=dict)  # .env 키 이름 → 값
    notes: str = ""
    draft: bool = False  # 초안 값 여부(실측·시험 전)

    @field_validator("settings")
    @classmethod
    def _known_setting_keys(cls, value: dict[str, str]) -> dict[str, str]:
        known = {name.upper() for name in Settings.model_fields}
        unknown = sorted(k for k in value if k not in known)
        if unknown:
            raise ValueError(f"알 수 없는 설정 키: {', '.join(unknown)}")
        return value


class ProfileFile(_Strict):
    version: int = Field(ge=1)
    draft_notice: str = Field(min_length=1)  # 값은 초안이며 실측으로 확정한다는 문구
    profiles: list[Profile] = Field(min_length=1)


@dataclass(frozen=True)
class Recommendation:
    profile: Profile
    reasons: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    fixed: bool = False  # LLM_PROFILE 로 고정했는가(감지 생략)

    @property
    def settings(self) -> dict[str, str]:
        return dict(self.profile.settings)


@dataclass(frozen=True)
class ProfileSet:
    profiles: tuple[Profile, ...]  # 최소 VRAM 오름차순
    draft_notice: str

    def by_id(self, profile_id: str) -> Profile | None:
        return next((p for p in self.profiles if p.id == profile_id), None)

    @property
    def lowest(self) -> Profile:
        return self.profiles[0]


def _check_ranges(profiles: list[Profile]) -> list[Profile]:
    """id 중복·구간 겹침·빈 구간을 검사한다. 구간은 [min, max) 이며 첫 구간은 0 에서 시작하고 마지막만 상한이 없다."""
    ids = [p.id for p in profiles]
    duplicated = sorted({i for i in ids if ids.count(i) > 1})
    if duplicated:
        raise ProfileConfigError(f"프로파일 id 가 중복됩니다: {', '.join(duplicated)}")
    ordered = sorted(profiles, key=lambda p: p.min_vram_mb)
    if ordered[0].min_vram_mb != 0:
        raise ProfileConfigError(f"VRAM 0MB 부터 시작하는 프로파일이 없습니다(빈 구간: 0~{ordered[0].min_vram_mb}MB).")
    for p in ordered:
        if p.max_vram_mb is not None and p.max_vram_mb <= p.min_vram_mb:
            raise ProfileConfigError(f"프로파일 {p.id} 의 최대 VRAM 이 최소 VRAM 보다 커야 합니다.")
    for prev, nxt in zip(ordered, ordered[1:]):
        if prev.max_vram_mb is None:
            raise ProfileConfigError(f"프로파일 {prev.id} 는 상한이 없어 뒤에 다른 프로파일이 올 수 없습니다(구간 겹침).")
        if prev.max_vram_mb > nxt.min_vram_mb:
            raise ProfileConfigError(f"프로파일 {prev.id} 와 {nxt.id} 의 VRAM 구간이 겹칩니다.")
        if prev.max_vram_mb < nxt.min_vram_mb:
            raise ProfileConfigError(
                f"프로파일 {prev.id} 와 {nxt.id} 사이에 빈 구간이 있습니다({prev.max_vram_mb}~{nxt.min_vram_mb}MB)."
            )
    if ordered[-1].max_vram_mb is not None:
        raise ProfileConfigError(f"마지막 프로파일 {ordered[-1].id} 는 상한이 없어야 합니다(빈 구간).")
    return ordered


def _check_setting_values(profile: Profile) -> None:
    """프로파일의 설정 값 묶음을 실제 설정 모델로 검증한다(타입·범위·서로의 관계)."""
    try:
        Settings(_env_file=None, **{k.lower(): v for k, v in profile.settings.items()})
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"]).upper() or "(설정)"
        raise ProfileConfigError(f"프로파일 {profile.id} 의 설정 값이 올바르지 않습니다: {loc} — {first['msg']}") from None


def parse_profiles(text: str) -> ProfileSet:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ProfileConfigError(f"프로파일 파일이 JSON 이 아닙니다: {str(exc)[:100]}") from None
    try:
        loaded = ProfileFile.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:3]
        )
        raise ProfileConfigError(f"프로파일 파일 형식이 올바르지 않습니다: {details}") from None
    ordered = _check_ranges(loaded.profiles)
    for profile in ordered:
        _check_setting_values(profile)
    return ProfileSet(profiles=tuple(ordered), draft_notice=loaded.draft_notice)


def load_profiles(path: Path | str | None = None) -> ProfileSet:
    target = Path(path) if path else DEFAULT_PROFILES_PATH
    if not target.is_absolute() and not target.exists():
        target = DEFAULT_PROFILES_PATH.parent.parent / target  # backend 폴더 기준 상대 경로도 허용
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        raise ProfileConfigError(f"프로파일 파일을 읽을 수 없습니다: {target.name}") from None
    return parse_profiles(text)


def recommend_profile(report: HardwareReport, profiles: ProfileSet, min_free_ratio: float = 0.7) -> Recommendation:
    """감지 결과로 권장 프로파일을 고른다. 대상 GPU 의 총 VRAM 이 속한 프로파일이 기본이고,
    여유 VRAM 이 총 VRAM 의 min_free_ratio 미만이면 한 단계 낮은 프로파일을 권한다(다른 프로그램이 GPU 를 쓰는 중일 수 있어서)."""
    reasons: list[str] = []
    warnings: list[str] = list(report.warnings)
    target = report.target
    if target is None:
        reasons.append("GPU 를 감지하지 못해 가장 낮은 프로파일(CPU)을 권장합니다.")
        return Recommendation(profiles.lowest, tuple(reasons), tuple(warnings))

    ordered = profiles.profiles
    index = next(
        (i for i, p in enumerate(ordered) if p.min_vram_mb <= target.total_mb and (p.max_vram_mb is None or target.total_mb < p.max_vram_mb)),
        0,
    )
    reasons.append(f"대상 GPU {target.name}: 총 VRAM {target.total_mb}MB → {ordered[index].id}")
    ratio = target.free_mb / target.total_mb if target.total_mb else 0.0
    if ratio < min_free_ratio:
        if index > 0:
            index -= 1
            reasons.append(
                f"여유 VRAM {target.free_mb}MB 가 총량의 {ratio:.0%}(기준 {min_free_ratio:.0%} 미만)라 한 단계 낮은 프로파일 {ordered[index].id} 를 권장합니다."
            )
        else:
            warnings.append(f"여유 VRAM 이 총량의 {ratio:.0%} 로 기준({min_free_ratio:.0%})보다 적지만 이미 가장 낮은 프로파일입니다.")
    return Recommendation(ordered[index], tuple(reasons), tuple(warnings))


def resolve_profile(
    profile_setting: str,
    profiles: ProfileSet,
    detector,  # Callable[[], HardwareReport]
    min_free_ratio: float = 0.7,
) -> tuple[Recommendation, HardwareReport | None]:
    """LLM_PROFILE 이 auto 가 아니면 감지하지 않고 지정 프로파일을 쓴다. 알 수 없는 id 는 ProfileConfigError."""
    if profile_setting.strip().lower() != AUTO:
        chosen = profiles.by_id(profile_setting.strip())
        if chosen is None:
            known = ", ".join(p.id for p in profiles.profiles)
            raise ProfileConfigError(f"LLM_PROFILE 값 {profile_setting!r} 에 해당하는 프로파일이 없습니다(사용 가능: {known}).")
        return Recommendation(chosen, (f"LLM_PROFILE={chosen.id} 로 고정되어 GPU 감지를 건너뜁니다.",), (), fixed=True), None
    report = detector()
    return recommend_profile(report, profiles, min_free_ratio), report
