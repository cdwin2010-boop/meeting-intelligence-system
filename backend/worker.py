"""단독 워커 프로세스 진입점: backend 폴더에서 `python worker.py` 로 실행한다. 본체는 app/worker_loop.py."""
from app.worker_loop import main

if __name__ == "__main__":
    raise SystemExit(main())
