"""핵심 3개 모듈만 Cython 으로 컴파일하는 선별 빌드 스크립트(작업 80-1).

사용법 (backend 폴더에서):
    python setup_cython.py --list                 # 대상 목록만 출력(컴파일 안 함)
    python setup_cython.py [--out build/cython]   # 컴파일, 산출물(.pyd/.so)은 --out 아래 패키지 경로로 낸다

원본 .py 는 수정하거나 지우지 않는다. 산출물이 있는 폴더에서는 같은 이름의 .py 보다 확장 모듈이 먼저 import 되므로
호출 코드(FastAPI·worker)는 바뀌지 않는다. 컴파일하지 않아도 .py 로 동일하게 동작한다.
"""
import argparse
import sys
from pathlib import Path

# 컴파일 대상: 이 목록이 유일한 정의다(다른 모듈을 실수로 포함하지 않도록 아래에서 검증한다).
TARGET_MODULES = (
    "app/pipeline/prompts.py",  # ① 프롬프트 템플릿 엔진
    "app/licensing/verifier.py",  # ② 라이선스 검증
    "app/pipeline/chunked_extract.py",  # ③ 의사결정·액션아이템 추출 로직(분할 추론 결합·중복 제거)
)
BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = Path("build") / "cython"


class TargetError(ValueError):
    """대상 목록이 올바르지 않음."""


class CythonMissingError(RuntimeError):
    """Cython 을 불러올 수 없음."""


def validate_targets(root: Path = BACKEND_DIR, targets: tuple = TARGET_MODULES) -> list:
    """대상 목록 검증: 정확히 3개, 중복 없음, root/app 아래의 일반 .py(__init__ 제외), 파일 존재. 절대 경로 목록을 돌려준다."""
    if len(targets) != 3 or len(set(targets)) != 3:
        raise TargetError("컴파일 대상은 서로 다른 3개 모듈이어야 합니다.")
    resolved = []
    app_dir = (root / "app").resolve()
    for rel in targets:
        path = (root / rel).resolve()
        if path.suffix != ".py" or path.name == "__init__.py":
            raise TargetError(f"대상이 일반 .py 모듈이 아닙니다: {rel}")
        if app_dir not in path.parents:
            raise TargetError(f"대상이 app/ 밖에 있습니다: {rel}")
        if not path.is_file():
            raise TargetError(f"대상 파일이 없습니다: {rel}")
        resolved.append(path)
    return resolved


def module_name(rel: str) -> str:
    """'app/pipeline/prompts.py' → 'app.pipeline.prompts'"""
    return ".".join(Path(rel).with_suffix("").parts)


def _import_cython():
    try:
        from Cython.Build import cythonize  # noqa: PLC0415

        return cythonize
    except ImportError:
        raise CythonMissingError("Cython 을 불러올 수 없습니다.") from None


def build(root: Path = BACKEND_DIR, out_dir: Path = DEFAULT_OUT, targets: tuple = TARGET_MODULES) -> list:
    """대상 모듈을 컴파일해 out_dir 아래에 낸다. 컴파일된 산출물 경로 목록을 돌려준다."""
    validate_targets(root, targets)
    cythonize = _import_cython()
    from setuptools import Extension, setup  # noqa: PLC0415

    out = out_dir if out_dir.is_absolute() else root / out_dir
    out.mkdir(parents=True, exist_ok=True)
    extensions = [Extension(module_name(rel), [str(root / rel)]) for rel in targets]
    setup(
        name="ai-dots-core",
        ext_modules=cythonize(
            extensions, language_level=3, build_dir=str(out / "_c"), compiler_directives={"binding": True}, quiet=True
        ),
        script_args=["build_ext", "--build-lib", str(out), "--build-temp", str(out / "_tmp")],
    )
    return sorted(p for p in out.rglob("*") if p.suffix in (".pyd", ".so"))


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="핵심 모듈 Cython 선별 빌드")
    parser.add_argument("--list", action="store_true", help="대상 목록만 출력")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="산출물 폴더(기본 build/cython)")
    args = parser.parse_args(argv)
    try:
        validate_targets()
    except TargetError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    if args.list:
        for rel in TARGET_MODULES:
            print(f"{module_name(rel)}  ({rel})")
        return 0
    try:
        produced = build(BACKEND_DIR, Path(args.out))
    except CythonMissingError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 3
    except SystemExit as exc:  # setuptools 컴파일 실패(컴파일러 없음 등)
        print(f"오류: 컴파일에 실패했습니다({exc}).", file=sys.stderr)
        return 4
    for path in produced:
        print(path.relative_to(BACKEND_DIR) if BACKEND_DIR in path.parents else path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
