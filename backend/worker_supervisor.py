"""워커 자동 재기동 감독 진입점: backend 폴더에서 `python worker_supervisor.py` 로 실행한다. 본체는 app/worker_supervisor.py."""
from app.worker_supervisor import main

if __name__ == "__main__":
    raise SystemExit(main())
