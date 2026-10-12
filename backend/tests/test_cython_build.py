"""핵심 3개 모듈 Cython 선별 빌드(작업 80-1). Cython·C 컴파일러가 모두 있을 때만 실제 빌드 통합 테스트를 실행한다."""
import ast
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("setup_cython_mod", ROOT / "setup_cython.py")
setup_cython = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup_cython)

EXPECTED = {
    "app/pipeline/prompts.py",
    "app/licensing/verifier.py",
    "app/pipeline/chunked_extract.py",
}


def test_target_list_is_exactly_the_three_core_modules():
    assert set(setup_cython.TARGET_MODULES) == EXPECTED and len(setup_cython.TARGET_MODULES) == 3
    paths = setup_cython.validate_targets()
    assert all(p.is_file() for p in paths)
    assert [setup_cython.module_name(r) for r in setup_cython.TARGET_MODULES] == [
        "app.pipeline.prompts", "app.licensing.verifier", "app.pipeline.chunked_extract",
    ]


@pytest.mark.parametrize(
    "targets, message",
    [
        (("app/pipeline/prompts.py",), "3개"),
        (("app/pipeline/prompts.py",) * 3, "3개"),
        (("app/pipeline/prompts.py", "app/licensing/verifier.py", "app/pipeline/__init__.py"), "일반"),
        (("app/pipeline/prompts.py", "app/licensing/verifier.py", "config.txt"), "일반"),
        (("app/pipeline/prompts.py", "app/licensing/verifier.py", "../setup_cython.py"), "app/ 밖"),
        (("app/pipeline/prompts.py", "app/licensing/verifier.py", "app/pipeline/nope.py"), "없습니다"),
    ],
)
def test_target_validation_rejects_bad_lists(targets, message):
    with pytest.raises(setup_cython.TargetError) as info:
        setup_cython.validate_targets(ROOT, targets)
    assert message in str(info.value)


def test_missing_cython_is_clear_error(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "Cython", None)
    monkeypatch.setitem(sys.modules, "Cython.Build", None)
    assert setup_cython.main(["--out", "unused"]) == 3
    err = capsys.readouterr().err
    assert "Cython" in err and "pip" not in err.lower()


def test_list_mode_does_not_build(capsys):
    assert setup_cython.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert all(setup_cython.module_name(r) in out for r in EXPECTED)


def test_script_does_not_touch_sources():
    text = (ROOT / "setup_cython.py").read_text(encoding="utf-8")
    for forbidden in ("unlink(", "os.remove", "shutil.rmtree", "write_text(", "write_bytes("):
        assert forbidden not in text


# ---- 대상 모듈 정적 점검: Cython 비호환·동적 구조 금지 ----
FORBIDDEN_CALLS = {"exec", "eval", "compile", "__import__", "globals", "locals", "setattr", "delattr"}


@pytest.mark.parametrize("rel", sorted(EXPECTED))
def test_targets_have_no_dynamic_import_or_global_state(rel):
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # 내장 함수 호출(이름 호출)만 금지 목록과 비교한다. re.compile 같은 메서드 호출은 해당 없음
            name = func.id if isinstance(func, ast.Name) else ""
            attr = func.attr if isinstance(func, ast.Attribute) else ""
            if name in FORBIDDEN_CALLS or attr in {"import_module", "reload"}:
                problems.append(f"{name}@{node.lineno}")
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            problems.append(f"global/nonlocal@{node.lineno}")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            if any(n.split(".")[0] == "importlib" for n in names):
                problems.append(f"importlib@{node.lineno}")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "sys" and node.attr == "modules":
            problems.append(f"sys.modules@{node.lineno}")
    assert problems == [], problems


def test_only_module_level_imports_in_targets():
    """함수 안 import 가 없다(순환 import 회피용 지연 import 는 컴파일 후 동작 차이를 만든다)."""
    for rel in EXPECTED:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            inner = [n.lineno for n in ast.walk(fn) if isinstance(n, (ast.Import, ast.ImportFrom))]
            assert inner == [], (rel, fn.name, inner)


def test_targets_are_plain_importable_without_compilation():
    from app.licensing import verifier
    from app.pipeline import chunked_extract, prompts

    assert all(str(m.__file__).endswith(".py") for m in (verifier, prompts, chunked_extract))


# ---- 실제 빌드(컴파일러·Cython 둘 다 있을 때만) ----
def _toolchain_available() -> bool:
    if importlib.util.find_spec("Cython") is None:
        return False
    return any(shutil.which(c) for c in ("cl", "gcc", "clang", "cc"))


@pytest.mark.skipif(not _toolchain_available(), reason="Cython 또는 C 컴파일러가 없어 실제 빌드 테스트를 건너뜀")
def test_real_build_and_import_from_compiled_artifacts(tmp_path):
    out = tmp_path / "out"
    produced = setup_cython.build(ROOT, out)
    assert len(produced) == 3
    # 배포 패키지 구성: app 전체를 복사한 뒤 대상 3개의 .py 를 빼고 산출물을 같은 위치에 둔다
    deploy = tmp_path / "deploy"
    shutil.copytree(ROOT / "app", deploy / "app", ignore=shutil.ignore_patterns("__pycache__"))
    for rel in EXPECTED:
        (deploy / rel).unlink()
    for artifact in produced:
        relative = artifact.relative_to(out)
        shutil.copy2(artifact, deploy / relative)
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "import app.pipeline.prompts as a, app.licensing.verifier as b, app.pipeline.chunked_extract as c;"
        "print(all(m.__file__.endswith(('.pyd', '.so')) for m in (a, b, c)))"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code, str(deploy)], capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0 and result.stdout.strip() == "True", result.stderr[-500:]
