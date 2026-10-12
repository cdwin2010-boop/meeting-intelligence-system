"""GPU 감지와 권장 모델 프로파일(작업 77-1). 가짜 nvidia-smi 출력만 쓰며 실제 GPU·네트워크가 필요 없다."""
import copy
import importlib.util
import io
import json
import re
import subprocess
import types
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.platform.hardware import GpuInfo, HardwareReport, detect_gpus, parse_nvidia_smi
from app.platform.profiles import (
    DEFAULT_PROFILES_PATH, ProfileConfigError, load_profiles, parse_profiles, recommend_profile, resolve_profile,
)

ROOT = Path(__file__).resolve().parent.parent
PROFILES = load_profiles()


def smi(*lines, returncode=0):
    """가짜 nvidia-smi 실행기: 주어진 출력 줄을 돌려준다."""
    calls: list[list[str]] = []

    def runner(cmd, timeout):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, returncode, stdout="\n".join(lines) + ("\n" if lines else ""), stderr="")

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def no_torch():
    raise ModuleNotFoundError("torch")


def pick(*lines, ratio=0.7):
    report = detect_gpus(runner=smi(*lines), torch_loader=no_torch)
    return report, recommend_profile(report, PROFILES, ratio)


# ---------------- 감지 ----------------
def test_command_is_argument_list_without_shell_and_exact_query():
    runner = smi("NVIDIA GeForce RTX 3070, 8192, 7900, 551.23")
    report = detect_gpus(runner=runner, torch_loader=no_torch)
    assert runner.calls[0] == ["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version", "--format=csv,noheader,nounits"]
    g = report.target
    assert (g.name, g.total_mb, g.free_mb, g.driver_version) == ("NVIDIA GeForce RTX 3070", 8192, 7900, "551.23")
    assert report.method == "nvidia-smi"


@pytest.mark.parametrize(
    "line, profile",
    [
        ("NVIDIA GeForce RTX 3070, 8192, 8000, 551.23", "gpu_8gb"),
        ("NVIDIA GeForce RTX 3060, 12288, 11900, 551.23", "gpu_12gb"),
        ("NVIDIA GeForce RTX 4060 Ti, 16380, 15900, 551.23", "gpu_16gb_plus"),
        ("NVIDIA GeForce RTX 3090, 24576, 24000, 551.23", "gpu_16gb_plus"),
        ("NVIDIA GeForce GTX 1650, 4096, 4000, 551.23", "cpu_only"),
    ],
)
def test_profile_by_total_vram(line, profile):
    _report, rec = pick(line)
    assert rec.profile.id == profile and rec.fixed is False and rec.reasons


def test_multi_gpu_uses_largest_total_vram_and_records_others():
    report, rec = pick("NVIDIA T400, 4096, 4000, 551.23", "NVIDIA RTX A5000, 24564, 24000, 551.23", "NVIDIA GeForce RTX 3060, 12288, 12000, 551.23")
    assert report.target.name == "NVIDIA RTX A5000" and rec.profile.id == "gpu_16gb_plus"
    assert {g.name for g in report.others} == {"NVIDIA T400", "NVIDIA GeForce RTX 3060"}
    assert any("3개" in w for w in report.warnings)


def test_low_free_vram_steps_down_one_profile_with_reason():
    _report, rec = pick("NVIDIA GeForce RTX 3060, 12288, 6000, 551.23")  # 여유 49%
    assert rec.profile.id == "gpu_8gb"
    assert any("한 단계 낮은" in r for r in rec.reasons)
    _report, borderline = pick("NVIDIA GeForce RTX 3060, 12288, 8602, 551.23")  # 70.0% → 하향 없음
    assert borderline.profile.id == "gpu_12gb"
    _report, custom = pick("NVIDIA GeForce RTX 3060, 12288, 8000, 551.23", ratio=0.5)  # 기준을 낮추면 유지
    assert custom.profile.id == "gpu_12gb"


def test_low_free_vram_on_lowest_profile_only_warns():
    _report, rec = pick("NVIDIA GeForce GTX 1650, 4096, 500, 551.23")
    assert rec.profile.id == "cpu_only" and any("가장 낮은 프로파일" in w for w in rec.warnings)


@pytest.mark.parametrize("failure", ["missing", "timeout", "error", "empty", "garbage"])
def test_failures_fall_back_to_cpu_without_exception(failure):
    def runner(cmd, timeout):
        if failure == "missing":
            raise FileNotFoundError("nvidia-smi")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, timeout)
        if failure == "error":
            return subprocess.CompletedProcess(cmd, 9, stdout="", stderr="NVIDIA-SMI has failed")
        if failure == "empty":
            return subprocess.CompletedProcess(cmd, 0, stdout="  \n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="이상한 출력\nfoo,bar,baz,qux\n", stderr="")

    report = detect_gpus(runner=runner, torch_loader=no_torch)
    assert report.target is None and report.method == "none" and report.gpus == ()
    assert report.warnings
    rec = recommend_profile(report, PROFILES)
    assert rec.profile.id == "cpu_only"


def test_timeout_is_five_seconds_by_default_and_passed_to_runner():
    seen = []

    def runner(cmd, timeout):
        seen.append(timeout)
        raise FileNotFoundError

    detect_gpus(runner=runner, torch_loader=no_torch)
    assert seen and set(seen) == {5.0}


def test_parse_skips_bad_lines_and_handles_commas_in_name():
    gpus, warnings = parse_nvidia_smi("NVIDIA, Foo Card, 8192, 8000, 551.23\nbad line\nName, abc, 1, 2\nOK Card, 4096, 9000, 1.0\n")
    assert [g.name for g in gpus] == ["NVIDIA, Foo Card"] and len(warnings) == 3


def test_torch_fallback_when_smi_missing():
    def runner(cmd, timeout):
        raise FileNotFoundError

    props = types.SimpleNamespace(name="NVIDIA GeForce RTX 3070", total_memory=8 * 1024 * 1024 * 1024)
    cuda = types.SimpleNamespace(
        is_available=lambda: True, device_count=lambda: 1, get_device_properties=lambda i: props,
        mem_get_info=lambda i: (7 * 1024 * 1024 * 1024, 8 * 1024 * 1024 * 1024),
    )
    report = detect_gpus(runner=runner, torch_loader=lambda: types.SimpleNamespace(cuda=cuda))
    assert report.method == "torch" and report.target.total_mb == 8192 and report.target.free_mb == 7168
    assert recommend_profile(report, PROFILES).profile.id == "gpu_8gb"


def test_torch_without_cuda_or_broken_torch_is_cpu():
    def runner(cmd, timeout):
        raise FileNotFoundError

    no_cuda = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False))
    assert detect_gpus(runner=runner, torch_loader=lambda: no_cuda).target is None

    def boom():
        raise RuntimeError("broken")

    assert detect_gpus(runner=runner, torch_loader=boom).target is None


def test_detection_without_anything_installed_does_not_raise():
    # 실제 nvidia-smi·torch 가 없는 환경에서도 예외 없이 보고서를 돌려준다(있으면 그 결과)
    report = detect_gpus()
    assert isinstance(report, HardwareReport) and report.method in ("nvidia-smi", "torch", "none")


# ---------------- LLM_PROFILE 고정 ----------------
def test_fixed_profile_skips_detection():
    def detector():
        raise AssertionError("감지하면 안 됩니다")

    rec, report = resolve_profile("gpu_12gb", PROFILES, detector)
    assert rec.profile.id == "gpu_12gb" and rec.fixed is True and report is None
    with pytest.raises(ProfileConfigError, match="gpu_99"):
        resolve_profile("gpu_99", PROFILES, detector)


def test_auto_profile_runs_detector():
    called = []

    def detector():
        called.append(1)
        return detect_gpus(runner=smi("X, 8192, 8000, 1"), torch_loader=no_torch)

    rec, report = resolve_profile("auto", PROFILES, detector)
    assert called and rec.profile.id == "gpu_8gb" and report is not None


# ---------------- 프로파일 파일 ----------------
def raw():
    return json.loads(DEFAULT_PROFILES_PATH.read_text(encoding="utf-8"))


def test_default_profiles_file_is_valid_and_follows_policy():
    ids = [p.id for p in PROFILES.profiles]
    assert ids == ["cpu_only", "gpu_8gb", "gpu_12gb", "gpu_16gb_plus"]
    assert "초안" in PROFILES.draft_notice
    text = DEFAULT_PROFILES_PATH.read_text(encoding="utf-8").lower()
    assert "qwen 2.5 7b instruct" in text
    for banned in ("exaone", "3b", "http://", "https://", ".gguf", ".safetensors"):
        assert banned not in text
    for p in PROFILES.profiles:  # 설정 키 값은 실제 설정으로 통과한다
        Settings(_env_file=None, **{k.lower(): v for k, v in p.settings.items()})


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d["profiles"][1].update(min_vram_mb=5000), "겹칩니다"),
        (lambda d: d["profiles"][1].update(min_vram_mb=7000), "빈 구간"),
        (lambda d: d["profiles"][0].update(min_vram_mb=1000), "0MB 부터"),
        (lambda d: d["profiles"][3].update(max_vram_mb=40000), "상한이 없어야"),
        (lambda d: d["profiles"][1].update(id="cpu_only"), "중복"),
        (lambda d: d["profiles"][1].update(unknown_key=1), "unknown_key"),
        (lambda d: d["profiles"][1].update(min_vram_mb=-5), "min_vram_mb"),
        (lambda d: d["profiles"][1]["settings"].update(NO_SUCH_KEY="1"), "알 수 없는 설정 키"),
        (lambda d: d["profiles"][1]["settings"].update(EXTRACT_CHUNK_MAX_CHARS="abc"), "EXTRACT_CHUNK_MAX_CHARS"),
        (lambda d: d["profiles"][1]["settings"].update(EXTRACT_CHUNK_OVERLAP_CHARS="9999"), "gpu_8gb"),
        (lambda d: d.pop("draft_notice"), "draft_notice"),
        (lambda d: d["profiles"][2]["llm"].pop("model"), "model"),
    ],
)
def test_invalid_profile_files_are_rejected_with_clear_error(mutate, message):
    data = copy.deepcopy(raw())
    mutate(data)
    with pytest.raises(ProfileConfigError) as info:
        parse_profiles(json.dumps(data, ensure_ascii=False))
    assert message in str(info.value)


def test_not_json_and_missing_file_are_clear_errors(tmp_path):
    with pytest.raises(ProfileConfigError):
        parse_profiles("{not json")
    with pytest.raises(ProfileConfigError, match="읽을 수 없습니다"):
        load_profiles(tmp_path / "missing.json")


# ---------------- 도구 ----------------
def _tool():
    spec = importlib.util.spec_from_file_location("detect_hardware_tool", ROOT / "scripts" / "detect_hardware.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tool_text_and_json_output_write_no_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tool = _tool()
    detector = lambda: detect_gpus(runner=smi("NVIDIA GeForce RTX 3060, 12288, 11900, 551.23"), torch_loader=no_torch)  # noqa: E731
    text = io.StringIO()
    assert tool.main([], detector=detector, out=text) == 0
    body = text.getvalue()
    assert "권장 프로파일: gpu_12gb" in body and "EXTRACT_CHUNK_MAX_CHARS=10000" in body and "초안" in body
    assert "자동 적용하지 않습니다" in body
    js = io.StringIO()
    assert tool.main(["--json"], detector=detector, out=js) == 0
    payload = json.loads(js.getvalue())
    assert payload["profile"]["id"] == "gpu_12gb" and payload["env"]["EXTRACT_CHUNK_MAX_CHARS"] == "10000"
    assert payload["gpus"][0]["totalMb"] == 12288 and payload["detectionMethod"] == "nvidia-smi"
    assert list(tmp_path.iterdir()) == []  # 어떤 파일도 만들지 않았다


def test_tool_fixed_profile_and_bad_profile(capsys):
    tool = _tool()
    out = io.StringIO()
    assert tool.main(["--profile", "gpu_8gb", "--json"], detector=lambda: (_ for _ in ()).throw(AssertionError("감지 금지")), out=out) == 0
    assert json.loads(out.getvalue())["detectionMethod"] == "skipped"
    assert tool.main(["--profile", "nope"], out=io.StringIO()) == 2
    assert "nope" in capsys.readouterr().err


# ---------------- 설정·가드 ----------------
def test_settings_defaults_and_validation():
    s = Settings(_env_file=None)
    assert s.llm_profile == "auto" and s.hardware_min_free_ratio == 0.7
    assert s.hardware_profiles_path == Path("config/model_profiles.json")
    for bad in (0, -0.1, 1.5):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, hardware_min_free_ratio=bad)


def test_platform_modules_are_not_imported_by_the_pipeline_or_api():
    pattern = re.compile(r"app\.platform|from\s+app\s+import\s+[^\n]*\bplatform\b")
    offenders = [
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "app").rglob("*.py")
        if "platform" not in p.relative_to(ROOT / "app").parts[:1] and pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], offenders


# ---------------- 12GB 프로파일 상세화(작업 79-1) ----------------
SETTINGS_KEYS = ("EXTRACT_CHUNK_MAX_CHARS", "EXTRACT_CHUNK_OVERLAP_CHARS", "EXTRACT_LLM_TIMEOUT_SECONDS")


def test_gpu_8gb_values_are_pinned_to_current_config_defaults():
    _report, rec = pick("NVIDIA GeForce RTX 3070, 8192, 8000, 551.23")
    defaults = Settings(_env_file=None)
    assert rec.profile.id == "gpu_8gb"
    assert rec.settings == {
        "EXTRACT_CHUNK_MAX_CHARS": str(defaults.extract_chunk_max_chars),
        "EXTRACT_CHUNK_OVERLAP_CHARS": str(defaults.extract_chunk_overlap_chars),
        "EXTRACT_LLM_TIMEOUT_SECONDS": str(int(defaults.extract_llm_timeout_seconds)),
    }
    assert (rec.profile.llm.model, rec.profile.llm.quantization) == ("Qwen 2.5 7B Instruct", "Q4_K_M")
    assert [c.model for c in rec.profile.llm.candidates] == ["Gemma 4 E4B"]


def test_gpu_12gb_has_separate_primary_candidates_and_notes():
    _report, rec = pick("NVIDIA GeForce RTX 3060, 12288, 11900, 551.23")
    p = rec.profile
    assert p.id == "gpu_12gb" and p.draft is True
    assert (p.llm.model, p.llm.quantization, p.llm.tier) == ("Qwen3-8B", "Q4_K_M", "permissive")
    assert p.llm.options == {"thinking_mode": "off"} and "thinking" in p.llm.note
    assert rec.settings["EXTRACT_CHUNK_MAX_CHARS"] == "10000" and rec.settings["EXTRACT_CHUNK_OVERLAP_CHARS"] == "800"
    by_tier = {t: [c.model for c in p.llm.candidates if c.tier == t] for t in ("permissive", "conditional")}
    assert by_tier["permissive"] == ["Gemma 4 12B", "Mi:dm 2.0 Base 11.5B", "Kanana 1.5 8B Instruct", "Qwen3-14B"]
    assert by_tier["conditional"] == ["Llama 3.x", "Bllossom"]
    assert all(not c.selectable_by_default for c in p.llm.candidates if c.tier == "conditional")
    assert "순차" in p.notes


def test_gpu_12gb_with_low_free_vram_steps_down_to_8gb_values():
    _report, rec = pick("NVIDIA GeForce RTX 3060, 12288, 6000, 551.23")
    assert rec.profile.id == "gpu_8gb"
    assert rec.settings["EXTRACT_CHUNK_MAX_CHARS"] == "6000" and rec.profile.llm.model == "Qwen 2.5 7B Instruct"


def test_gpu_16gb_plus_shares_12gb_candidates_with_conservative_chunks():
    by_id = {p.id: p for p in PROFILES.profiles}
    assert by_id["gpu_16gb_plus"].llm.candidates == by_id["gpu_12gb"].llm.candidates
    assert by_id["gpu_16gb_plus"].settings["EXTRACT_CHUNK_MAX_CHARS"] == "8000"  # 기존 값 유지(보수적)


def _minimal_profile_data():
    return {
        "version": 1, "draft_notice": "초안",
        "profiles": [{
            "id": "only", "description": "d", "min_vram_mb": 0, "max_vram_mb": None,
            "stt": {"model": "s", "precision": "int8"}, "llm": {"model": "m", "quantization": "Q4"},
        }],
    }


def test_old_format_without_extension_fields_still_validates():
    ps = parse_profiles(json.dumps(_minimal_profile_data()))
    llm = ps.profiles[0].llm
    assert llm.candidates == [] and llm.options == {} and llm.tier == "permissive" and ps.profiles[0].draft is False


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d["profiles"][0]["llm"].update(tier="conditional"), "permissive"),
        (lambda d: d["profiles"][0]["llm"].update(candidates=[
            {"model": "x", "license": "L", "tier": "conditional", "selectable_by_default": True}]), "selectable_by_default"),
        (lambda d: d["profiles"][0]["llm"].update(candidates=[
            {"model": "x", "license": "L", "tier": "free", "selectable_by_default": True}]), "tier"),
        (lambda d: d["profiles"][0]["llm"].update(candidates=[
            {"model": "x", "license": "L", "tier": "permissive", "selectable_by_default": True, "url": "u"}]), "url"),
        (lambda d: d["profiles"][0]["llm"].update(unknown=1), "unknown"),
    ],
)
def test_tier_and_extension_validation(mutate, message):
    data = _minimal_profile_data()
    mutate(data)
    with pytest.raises(ProfileConfigError) as info:
        parse_profiles(json.dumps(data))
    assert message in str(info.value)


def test_default_profiles_file_has_no_banned_names_or_addresses():
    text = DEFAULT_PROFILES_PATH.read_text(encoding="utf-8").lower()
    for banned in ("exaone", "qwen 2.5 3b", "72b", "gemma 3", "26b", "kms", "ai_rag", "http", ".gguf"):
        assert banned not in text, banned
    assert all(p.draft for p in PROFILES.profiles)


def test_tool_shows_primary_candidates_and_tiers_without_writing_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tool = _tool()
    detector = lambda: detect_gpus(runner=smi("NVIDIA GeForce RTX 3060, 12288, 11900, 551.23"), torch_loader=no_torch)  # noqa: E731
    text = io.StringIO()
    assert tool.main([], detector=detector, out=text) == 0
    body = text.getvalue()
    assert "LLM 주력: Qwen3-8B (Q4_K_M)" in body and "thinking_mode=off" in body
    assert "[자유 라이선스] Gemma 4 12B" in body and "[조건부 — 고객 요청 시에만] Llama 3.x" in body
    assert "초안" in body
    js = io.StringIO()
    assert tool.main(["--json"], detector=detector, out=js) == 0
    profile = json.loads(js.getvalue())["profile"]
    assert profile["primary"]["model"] == "Qwen3-8B" and profile["draft"] is True
    tiers = {c["model"]: (c["tier"], c["selectable_by_default"]) for c in profile["candidates"]}
    assert tiers["Gemma 4 12B"] == ("permissive", True) and tiers["Bllossom"] == ("conditional", False)
    assert list(tmp_path.iterdir()) == []
