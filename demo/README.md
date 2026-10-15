# 데모 영상 녹화(작업 D-1)

가상 샘플 데이터로 약 1분 고객 데모 영상을 만든다. 결과 파일은 저장소가 아니라 `C:\DOTS_demo\<yyyymmdd_hhmm>\` 에 생성된다(git 제외).

## 준비
- Chrome 설치, `frontend` 에서 `npm install`, `npx playwright install ffmpeg`(녹화용)
- MP4 변환용 `ffmpeg`/`ffprobe`(영상 제작 도구로만 사용, 제품 의존성 아님). PATH 에 없으면 환경변수 `FFMPEG_PATH`·`FFPROBE_PATH` 로 지정
- backend 가상환경(`backend\.venv`)

## 실행
```
cd frontend
npm run demo:record
```
- 임시 DB 에 `demo/seed_demo.py` 로 가상 데이터를 시드하고, 백엔드(8102)·프론트(3202)를 띄워 녹화한 뒤 모두 종료한다. 개발 DB·`.env` 는 읽거나 쓰지 않는다.
- 장면마다 화면 글자에서 비밀 패턴(키·http 주소·이메일·경로)을 검사하고, 발견하면 중단한다.
- 영상 속도·길이를 강제로 줄이거나 늘리지 않는다. 자연 녹화 길이가 55~90초(1분 30초 이내)를 벗어날 때만 장면 사이 대기를 조정해 최대 3회 다시 녹화한다.

## 산출물
`demo_1min.mp4`(주), `demo_1min.webm`(원본), `scenario.md`, `scene_timings.csv`, `frames/`(장면별 대표 프레임)
