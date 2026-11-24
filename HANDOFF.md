# 애드스캔 인수인계 (2026-11-24)

새 Claude Code 세션에서 이 파일부터 읽고 시작하면 된다.

## 1. 프로젝트
- 이름: 애드스캔 (부당광고 판별). 소비자가 화면 녹화한 식품 광고 영상을 올리면 OCR(Tesseract)과 STT(faster-whisper)로 글자·음성을 뽑고, 부당광고 위험을 판정한다.
- 목표: 한국코드페어 해커톤. 공개 Render 링크와 발표 자료. 배점은 아이디어 20, 기술의 적정성 20, 구현의 완성도 20, 실용성 30, 발표 10.

## 2. 저장소와 배포
- GitHub: https://github.com/kim090624/weird-ad (계정 kim090624)
- 브랜치
  - `main`: 예전 엔진 (JudgeEngine, 금지어 사전 + AI 문맥). Render 서비스 `weird-ad-first`로 만드는 중.
  - `claims-engine`: 새 엔진 (주장구조, AI 없이 동작). Render `weird-ad.onrender.com`이 이 브랜치를 배포 중. 마지막 커밋 b70f40d, 푸시됨.
- Render: 서비스 하나는 브랜치 하나만 배포한다. 환경변수는 Render 대시보드 > Environment에서 사용자가 직접 넣는다.
- 엔진 선택: `pipeline/run.py`의 `make_engine()`. 기본 `claims`, `ADSCAN_ENGINE=legacy`면 예전 엔진.

## 3. 새 엔진 구조 (pipeline/claims/)
- 문장 → (대상 종류: 질병/기능/금지어/사례/전문가/의약품대체, 강도 4치료·3예방·2개선·1도움·0, 맥락: 직접·후기·부정·경고, 확신: 1.0 정확 / 0.85 OCR 유사 / 0.75 앞 문장에서 이어받음) → 판정표(rules.py) → 근거(ban/review/ok/exempt).
- 위험도: s = 규칙점수 × 확신, R = 100(1 − ∏(1 − s_i)). 금지 있음이면 R≥60, 없으면 R≤59. 구간 0–24 낮음, 25–59 주의, 60–100 높음. 같은 문장에서 같은 규칙은 최댓값 한 번만 센다. 제품 종류가 "모름"이면 일반식품 기준으로 계산하고 `ifHealth`(건강기능식품이라면)를 참고로 따로 낸다.
- 제품 종류: 일반식품 / 건강기능식품 / 모름 + 기능성표시식품은 "건강기능식품이 아닙니다" 문구와 원료 문형(INGREDIENT_FORM)으로 자동 감지.
- 규칙: R0 금지어, E1~E3 전문가, R1 질병+강도≥2, R2 질병만, R3 일반식품 기능 주장, R4 의약품 대체, R5 과장, R6 영양성분 기능(ok), L1~L4 기능성표시식품, H* 건강기능식품(C003 승인 기능 확인), C1 사례 표현(검토).
- 데이터: data/claims/cases.csv(62개), approved_functions.csv(16), diseases_extra.csv(약 48개, 초안).
- 테스트: tests/test_claims.py, tests/eval_cases.json(18개, 한계 3개), tests/eval_compare.py. 새 엔진 16/16 정상, 한계 0/3, 예전 엔진 7/16. 평가 사례를 내가 직접 썼기 때문에 정확도 근거로는 약하다.
- 한계 3가지: 후기 암시(질병명 없음), 앞 문장 없는 "이 병", STT가 동사를 잘못 들은 경우.

## 4. 지금 하던 일: Groq 연결 (Claude 대신)
사용자가 Groq 키를 채팅에 붙여넣고 "클로드 대신 이 키로 연결하자"고 했다.

보안 규칙
- 키는 코드, 파일, 커밋, 메모리, 폼에 넣지 않는다. 서버 환경변수 `GROQ_API_KEY`로만 읽는다. Render에서 사용자가 직접 설정한다.
- 채팅에 노출된 키이므로 설정 뒤에 Groq 콘솔에서 새로 발급(회전)하라고 안내한다.
- Bash는 네트워크가 막혀 있어서 키로 실제 호출 테스트는 할 수 없다. 가짜 응답으로 테스트한다.

작업 상태 (커밋 안 됨, 작업 폴더에만 있음)
- 새 파일 `pipeline/ai_provider.py`: GROQ_API_KEY 우선, ANTHROPIC_API_KEY 대체, `AI_PROVIDER`로 강제 가능, 기본 모델 `openai/gpt-oss-120b`(`GROQ_MODEL`로 변경). `groq_json()`이 Groq(OpenAI 호환, https://api.groq.com/openai/v1/chat/completions)를 urllib로 호출하고 JSON을 검증, 실패는 `AIUnavailable(code)`.
- 수정 `pipeline/judge_engine.py`: `AIContextAnalyzer`가 `provider()`로 groq/anthropic을 고르도록 바꾸는 중. `review()`의 Groq 분기는 넣었고 문법은 통과. 아직 안 한 것: 413행 근처 진행 문구 "Claude가 …"를 제공자 이름으로, Anthropic 경로의 `last_provider` 설정, 오류 코드 안내문.
- 남은 일
  1. ClaimsEngine에 "AI 참고 의견" 단계를 넣는다. 검토(review) 수준 주장에만 쓰고, 판정과 점수는 규칙이 쥔다. AI 출력은 검증하고 실패하면 규칙만으로 진행. 결과는 기존 `ai` 형태 {misleading, summary, items:[{n, misleading, reason}], source}로 화면에 표시.
  2. render.yaml에 `GROQ_API_KEY`(sync:false), 선택 `GROQ_MODEL` 추가, README 갱신.
  3. 가짜 응답으로 단위 테스트, 전체 테스트 실행.
  4. 모델 이름은 Groq 문서에서 다시 확인. llama-3.3-70b-versatile, llama-3.1-8b-instant는 2026-08-16에 종료됐다.

## 5. 공공데이터 API (식품안전나라)
- 인증키는 https://www.foodsafetykorea.go.kr/api/main.do 에서 로그인 후 인증키신청. 사용자가 직접 가입·신청하고 Render에 `FOODSAFETY_API_KEY`로 넣는다. 가입 중 주소검색 팝업에서 막혀 있었다.
- 키가 없으면 C003 제품 조회 단계는 건너뛰고 승인 기능 확인은 꺼진다.
- 키를 넣은 뒤 할 일: 라이브 사이트에서 제품 조회(예: 뉴케어) 확인.

## 6. 앞으로 할 일
1. 위 Groq 마무리.
2. 조사 보고서 읽기: task_id `wf-ff2f4d9a-5fdc-8f87-b722-097a0f88d7e3` (mcp__claude_ai__read_research_report). 도움말 내용 작성 전에 본다.
3. 소비자 도움말 패널: 첫 화면 우측 상단 아이콘 → "부당광고란?", 법률상 핵심 몇 가지, O/X 퀴즈.
4. 결과 화면에 "왜 위험하다고 봤는지" 논리 설명 (소비자 관점).
5. 시연 영상: ffmpeg로 만든 자막 합성 영상 권장. 메타 광고 라이브러리 스크레이핑은 피한다.
6. 평가 데이터(적발 사례와 정상 광고 각 30개 이상), 실제 영상 STT 확인, 점수 보정, 한국표준질병사인분류 기반 질병 목록, 고시 조문 번호를 원문과 대조(지금 번호는 웹 요약 기반).
7. weird-ad-first에 cron-job.org 핑 추가. claude.ai 아티팩트 JS 모드에는 위험도 점수가 없다.
8. 커밋과 푸시는 사용자가 요청할 때만 한다.

## 7. 사용자 선호
- 반말·대화체, 군더더기 없이 간결하게. 수식은 렌더링되는 표기로.
- 사용자는 "우리"라는 말로 팀 작업처럼 말한다. 심사위원 질문(예: "왜 '혈관 청소'는 있고 '혈관 건강'은 없나")에 답이 되는 설계를 중요하게 본다. 비판적으로 점검해 달라는 요청이 많았다.

## 8. 작업 환경 메모
- 저장소는 이 세션의 `/tmp/ad_checker`에 있었다. 새 세션에서는 GitHub에서 `git clone` 후 `git checkout claims-engine`.
- 아직 푸시되지 않은 변경(ai_provider.py, judge_engine.py)은 이 파일과 함께 커밋해야 새 세션에서 보인다.
