# 애드스캔 — 부당광고 판별

식품 광고를 녹화한 영상을 올리면 화면 글자와 소리를 글자로 바꾸고, 팀 금지어 사전으로 부당광고 여부를 확인한다.

## 구조

```
app.py                  웹 서버. "/" 화면, "/api/analyze" 분석, "/api/health" 상태
main.py                 터미널에서 같은 분석 (python main.py --video 광고.mp4)
pipeline/
  run.py                영상 한 건을 처음부터 끝까지 (app.py, main.py가 같이 씀)
  extractors.py         ffmpeg 장면 뽑기 → Tesseract 화면 글자 → faster-whisper 소리
  judge_engine.py       팀 사전 규칙 → 문맥 확인만 Claude API
data/dictionary/
  geumjieo_list.csv     팀 금지어 사전 (184개 사용, '확인 필요' 메모 1줄 제외)
  sources.json          사전 출처
web/
  src/index.html        애드스캔 화면 원본
  build.py              글꼴(Pretendard)과 사전을 넣어 web_demo.html 생성
  fonts/                Pretendard 일부 글자 (OFL)
web_demo.html           빌드된 화면 (서버와 claude.ai 아티팩트가 같이 씀)
tests/                  판단엔진 시험 (engine_cases.json: 파이썬과 웹이 같은 결과여야 함)
Dockerfile, render.yaml, .github/workflows/keep-awake.yml   배포
```

## 확인 순서

1. **장면 뽑기**: 1초에 한 장(1분 넘으면 최대 60장), 화면이 바뀐 장면만 남긴다.
   96x96으로 줄여 12x12 칸마다 차이를 재서, 자막 한 줄만 바뀌어도 새 장면으로 본다.
2. **화면 글자**: Tesseract `kor+eng`, `--psm 11`(흩어진 글자 찾기).
   시험 영상에서 `--psm 6`은 자막 한 줄을 조각냈고 `--psm 11`은 그대로 읽었다.
3. **소리**: ffmpeg로 소리만 뽑아 faster-whisper(`base`)가 한국어 문장으로 바꾼다. API 필요 없음.
4. **금지어 사전** (`judge_engine.py`, AI 없이 조건문만):
   - `1단계 금지어` → 금지
   - 한 문장에 `전문가 + 추천동사` 또는 `질병명 + 효능동사` → 금지 조합
   - 전문가만, 질병명만, 전문가 + '사용'(사전 비고: 오탐 많음) → 문맥 확인
   - 사전 밖에서 더한 예외: `공신력`은 `공신`으로 보지 않음. `암`은 앞 글자가 한글이면 뺌
     (뒤에 조사·합성어가 오는 `암이`, `암세포`는 잡음). 긴 낱말을 우선함(`한의사` > `의사`,
     `공진단` 안의 `공진`은 따로 세지 않음).
5. **AI 문맥 확인**: 금지 표현이 없고 문맥 확인 대상이 있을 때만 Claude API에 묻는다.
   `ANTHROPIC_API_KEY`가 없으면 그 표현을 "확인이 필요한 표현"으로 보여 주고 끝낸다.

## 두 가지로 쓸 수 있다

| | 서버 (Render) | claude.ai 아티팩트 |
|---|---|---|
| 여는 사람 | 누구나 링크로 | claude.ai에 로그인한 사람 |
| 화면 글자 | Tesseract (서버) | Claude가 장면 이미지를 읽음 |
| 소리 | faster-whisper (서버) | 못 들음, 나레이션을 직접 적는 칸 |
| AI 비용 | 내 API 키 (콘솔 크레딧) | 여는 사람의 claude.ai 사용량 |

같은 `web_demo.html`을 쓴다. 서버가 내보낼 때 `<meta name="adscan-mode" content="server">`를 붙이고,
화면은 이 표시가 있으면 `/api/analyze`를, 없으면 Claude를 부른다.

## 배포 (Render, undersign-assist와 같은 방식)

1. https://render.com 가입 (GitHub 계정으로)
2. New → **Blueprint** → `kim090624/weird-ad` 선택. `render.yaml`대로 설정이 채워진다.
3. `ANTHROPIC_API_KEY` 입력 (없어도 동작하고, 문맥 확인만 빠진다)
4. Apply → 빌드가 끝나면 `https://weird-ad.onrender.com`

- 무료 서버는 15분 쉬면 잠든다. `keep-awake.yml`이 10분마다 `/api/health`를 깨우지만, 시연 직전엔 직접 한 번 연다.
- 메모리 512MB라 음성 모델은 `base`. 더 정확하게 하려면 유료 인스턴스에서 `WHISPER_MODEL=small`.
- 서비스 이름을 바꾸면 `keep-awake.yml` 안의 주소도 바꾼다.
- 서버 상태: `/api/status` (ffmpeg, tesseract 한국어, 음성 인식, AI 키 유무)

## 내 컴퓨터에서 실행

필요한 것: ffmpeg, Tesseract + 한국어 데이터, Python 3.10+

- Windows: ffmpeg는 https://www.gyan.dev/ffmpeg/builds/ 의 `bin` 폴더를 PATH에 추가.
  Tesseract는 https://github.com/UB-Mannheim/tesseract/wiki 에서 설치하며 Korean 체크,
  `C:\Program Files\Tesseract-OCR`을 PATH에 추가. `tesseract --list-langs`에 `kor`가 있어야 한다.
- Mac: `brew install ffmpeg tesseract tesseract-lang`
- Ubuntu: `sudo apt install ffmpeg tesseract-ocr tesseract-ocr-kor`

```bash
pip install -r requirements.txt
python main.py --check                      # 다 있는지
python app.py                               # http://127.0.0.1:5000
python main.py --video 광고.mp4             # 터미널에서
python main.py --text "의사가 추천하는 건강차"
python tests/test_engine.py                 # 판단엔진 시험
```

`ANTHROPIC_API_KEY`는 PowerShell `setx ANTHROPIC_API_KEY "sk-ant-..."`(터미널 다시 열기), Mac/Linux `export ANTHROPIC_API_KEY=sk-ant-...`.

## 화면 고치기

`web/src/index.html`을 고친 뒤 `python web/build.py`. 사전 CSV를 고쳤을 때도 빌드만 다시 하면
화면에 반영된다(서버는 CSV를 직접 읽는다). 판단 규칙을 고치면 `pipeline/judge_engine.py`와
`web/src/index.html`의 `analyze`를 같이 고치고 `tests/engine_cases.json`으로 둘이 같은지 확인한다.

## 확인한 것 / 못 한 것 (2026-11-06)

- 배포: https://weird-ad.onrender.com 에서 동작 중. Docker 빌드 성공, `/api/status`에서
  ffmpeg, Tesseract 한국어, faster-whisper, anthropic 모두 설치 확인.
- 배포 서버에 `tests/samples/caption_test.webm`을 올려 장면 3장 → 글자 2줄 → 금지 조합 2건 (13.6초).
- 글자 입력: 금지 조합과 문맥 확인이 섞인 문장도 기대대로 판정. 시험 문장 17개에서 파이썬과 웹 판단 결과가 같음.
- 못 한 것: 말소리가 든 영상으로 음성 인식 시험(시험 영상에 소리가 없음), AI 키로 문맥 확인 호출(키 없음).
