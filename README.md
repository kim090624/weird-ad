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
5. **제품 종류별 규칙** (`data/dictionary/functional_claims.csv`, **초안 — 팀 확인 필요**):
   처음에 "건강기능식품인가요?"를 표시 사진과 함께 묻는다 (예 / 아니요 / 모르겠어요).
   - 기능성 대상 + 기능성 동사 (관절 + 케어, 면역 + 증진): 일반식품은 금지, 건강기능식품은 허용
   - 기능성 대상만 (혈당 걱정되시죠?): 일반식품은 문맥 확인, 건강기능식품은 허용
   - 기능성 대상 + 과장 (감량 + 보장): 종류와 상관없이 금지
   - 모르겠어요: 더 엄격한 일반식품 기준으로 보고, 건강기능식품이라면 허용됐을 결과에
     "건강기능식품이라면 이 표현은 허용될 수 있어요"를 붙인다.
   - 터미널: `python main.py --text "..." --category health|general|unknown`
6. **공공데이터 (식품안전나라 Open API, `pipeline/foodsafety.py`)** — 인증키 `FOODSAFETY_API_KEY`가 있을 때
   - **C003 건강기능식품 품목제조 신고** (약 4만 6천 개): 화면 글자 속 제품명을 찾아 등록된 건강기능식품이면
     사용자 답과 상관없이 건강기능식품 기준으로 바꾸고, 그 제품이 **인정받은 기능성**을 결과에 보여 준다.
     인정받은 기능성에 없는 효과를 광고하면 "인정 밖 기능"으로 표시한다.
     (잘못 맞히지 않게 제품명 조각이 2개 이상 겹치거나 제품명과 똑같을 때만 인정)
   - **I2710 기능성 원료(고시형) + I-0050 개별인정형 원료**: 식약처가 인정한 기능성 문구
     ("관절 건강에 도움을 줄 수 있음" 등)를 받아, 일반식품이 같은 문구를 쓰면 공식 문구를 근거로 금지 표시.
     서버가 켜질 때 받아 두고, `python scripts/fetch_public_data.py --key 인증키`로 `data/public/`에 저장해 둘 수도 있다.
   - 인증키가 없으면 이 단계만 건너뛰고 나머지는 그대로 동작한다.
   - 인증키: 식품안전나라 → 공공데이터 활용 → 인증키 신청 (무료). `sample` 키는 시험용이라 검색 결과가 5건까지만 나온다.
7. **AI 문맥 확인** (예전 엔진): 금지 표현이 없고 문맥 확인 대상이 있을 때만 AI(Groq, 없으면 Claude)에 묻는다.
   키가 없거나 실패하면 `pipeline/context_rules.py`(규칙)로 대신 판단한다.
   AI 키(`GROQ_API_KEY`, `ANTHROPIC_API_KEY`)가 없으면 그 표현을 "확인이 필요한 표현"으로 보여 주고 끝낸다.

## 주장구조 엔진 (claims-engine 브랜치, 판정은 AI 없이)

단어가 있는지가 아니라 **문장이 무엇을 주장하는지**로 판정한다 (`pipeline/claims/`).

1. **주장 추출** (`extract.py`): 문장 → 대상(질병·기능성·금지 표현·적발 사례 표현·약 끊기·전문가 추천) + 강도(치료 4 / 예방 3 / 개선 2 / 도움 1) + 맥락(직접 / 후기 / 부정 / 경고·보도) + 확신(글자 그대로 1.0 / OCR 보정 0.85 / 앞 문장에서 이어받음 0.75).
   부정("치료하지 않습니다")과 경고·보도("적발됐어요")는 면제, 후기는 면제가 아니다. 글자가 깨진 경우(혈관 정소)는 자모 비교로 찾는다.
2. **판정표** (`rules.py`): 제품 종류 × 대상 종류 × 강도 × 문형 → 금지 / 확인 필요 / 허용 / 면제. 규칙마다 번호(R0~R6, H, L, E, C)와 근거 조항이 붙는다.
3. **제품 종류 세 가지**: 일반식품 / 건강기능식품 / **기능성표시식품**(식약처 고시 「부당한 표시 또는 광고로 보지 아니하는 식품등의 기능성 표시 또는 광고에 관한 규정」 제6조).
   사용자에게 더 묻지 않고, 광고 글자에 "건강기능식품이 아닙니다"가 있는지와 문형(원료 기준 "~에 도움을 줄 수 있다고 알려진 ~가 들어 있습니다" / 완제품 기준)으로 가른다.
   허용 조건(영양성분 기준, 함량, 제조업소)은 제품 정보가 있어야 확인되므로 아직 판정에 넣지 않았다.
4. **위험도 점수**: 주장 하나 s = 규칙 기본점수 × 확신, 영상 전체 R = 100 × (1 − Π(1 − s)). 금지가 있으면 60 이상, 없으면 59 이하.
   0~24 낮음 / 25~59 주의 / 60~100 높음. 규칙 가중치에서 나온 참고 점수이며 위반 확률이 아니다. "모르겠어요"를 고르면 건강기능식품이라면의 점수를 따로 보여 준다.
5. **데이터**: `data/claims/cases.csv`(식약처 적발 사례 표현 62개, 출처·쪽수 포함), `approved_functions.csv`(기능성 대조 16개), `diseases_extra.csv`(질병명 초안).
   질병명은 정식 질병 분류(한국표준질병사인분류)로 교체해야 한다.

6. **AI 참고 의견** (선택): "확인 필요" 문장만 AI에게 묻고, 문장마다 이유를 붙인다. **판정과 위험도는 규칙이 정하고 AI는 바꾸지 못한다.**
   AI 답은 모양(번호·참거짓·문장 길이)을 검사하고, 키가 없거나 답이 틀리거나 호출이 실패하면 이 단계만 빠진다.

- 기본 엔진은 주장구조 엔진이다. 예전 사전 엔진은 `ADSCAN_ENGINE=legacy`로 켠다.
- 시험: `python tests/test_claims.py`, 예전 엔진과 비교: `python tests/eval_compare.py`.
  `tests/eval_cases.json`은 평가 틀이다. 지금 문장은 설계 확인용이라 정확도 근거가 아니고, 적발 사례·정상 광고 각 30개 이상으로 채워야 한다.
- 못 하는 것: 영상 속 장면, 질병명 없는 후기 암시, 앞 문장 없이 나온 "이 병", 동사에서 난 STT 오류.
- claude.ai 아티팩트(브라우저 안 판단)는 아직 예전 방식이라 위험도가 나오지 않는다.

## 두 가지로 쓸 수 있다

| | 서버 (Render) | claude.ai 아티팩트 |
|---|---|---|
| 여는 사람 | 누구나 링크로 | claude.ai에 로그인한 사람 |
| 화면 글자 | Tesseract (서버) | Claude가 장면 이미지를 읽음 |
| 소리 | faster-whisper (서버) | 못 들음, 나레이션을 직접 적는 칸 |
| AI 비용 | 서버 키 (Groq 또는 Anthropic) | 여는 사람의 claude.ai 사용량 |

같은 `web_demo.html`을 쓴다. 서버가 내보낼 때 `<meta name="adscan-mode" content="server">`를 붙이고,
화면은 이 표시가 있으면 `/api/analyze`를, 없으면 Claude를 부른다.

## 배포 (Render, undersign-assist와 같은 방식)

1. https://render.com 가입 (GitHub 계정으로)
2. New → **Blueprint** → `kim090624/weird-ad` 선택. `render.yaml`대로 설정이 채워진다.
3. Environment에 `GROQ_API_KEY` 입력 (없어도 동작하고, AI 참고 의견만 빠진다). 모델은 `GROQ_MODEL`(기본 `openai/gpt-oss-120b`).
   Groq 키가 없으면 `ANTHROPIC_API_KEY`(Claude)를 쓴다. 둘 다 있을 때 Claude로 고정하려면 `AI_PROVIDER=anthropic`.
   키는 저장소에 넣지 않는다. 채팅·메신저 등에 노출된 키는 Groq 콘솔에서 지우고 새로 발급한다.
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

AI 키는 PowerShell `setx GROQ_API_KEY "gsk_..."`(터미널 다시 열기), Mac/Linux `export GROQ_API_KEY=gsk_...`. Claude를 쓰면 `ANTHROPIC_API_KEY`.

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
