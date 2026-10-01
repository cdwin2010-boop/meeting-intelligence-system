from fastapi import FastAPI

app = FastAPI(title="Meeting Intelligence Backend (v2)")


@app.get("/api/health")
def health() -> dict[str, str]:
    # 서버 생존 확인용. DB는 건드리지 않는다
    return {"status": "ok"}
