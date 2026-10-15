"""원격(시험용) 엔진 연결 진단 도구(작업 L-4a). 화면·앱 서버 없이 실행하며 GET {주소}/models 한 번만 호출한다.
저장된 원격 설정(.env 기본값 + data/engine_test.json)을 읽는다. 키는 명령줄 인자로 받지 않는다. 출력은 한 줄이며 주소·키는 출력하지 않는다.

사용법 (backend 폴더에서): python scripts/engine_check.py
종료 코드: 0 = 정상, 1 = 실패(분류 출력), 2 = 설정 없음
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import engine_diag  # noqa: E402
from app.services import engine_test as engine  # noqa: E402


def main(out=None) -> int:
    stream = out or sys.stdout
    remote = engine.load_remote_settings()
    if not remote.configured:
        print("설정 없음: 서버 주소와 모델명을 먼저 저장하세요", file=stream)
        return 2
    # 시험 도구이므로 외부 주소를 허용한다(화면의 연결 점검과 같은 기준, 정책 강제 설정은 화면에서만 적용)
    result = engine_diag.diagnose(remote.base_url, remote.api_key, remote.model, allow_external=True)
    status = result.http_status if result.http_status is not None else "-"
    line = f"{result.category} · HTTP {status} · {result.elapsed_ms}ms · {result.hint}"
    if result.candidates:
        line += " · 후보: " + ", ".join(result.candidates)
    print(line, file=stream)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
