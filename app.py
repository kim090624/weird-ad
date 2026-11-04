"""
웹 서버 (Flask)

소비자가 브라우저에서 영상을 업로드하면:
  업로드 받기 -> OCR/STT 추출 -> 금지어/오인유발어 판정 -> 결과를 화면에 보여줌

실행 (로컬):
  python3 app.py
  그 다음 브라우저에서 http://127.0.0.1:5000 접속

배포: Dockerfile로 Render 등 클라우드에 올리면 PORT 환경변수를 읽어서
0.0.0.0에 바인딩함 (README의 "클라우드 배포" 참고).

ANTHROPIC_API_KEY 환경변수가 없으면 오인유발어 분석 단계에서 에러 메시지를 그대로 화면에 보여줌
(서버가 죽지는 않음).
"""

import os
import traceback
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, flash

from pipeline.input_handler import InputHandler
from pipeline.extractors import TextExtractionService, OCRExtractor, STTExtractor, check_dependencies
from pipeline.judge_engine import JudgeEngine, AIContextAnalyzer
from data.wordlists_schema import WordLists

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-me")

UPLOAD_TMP_DIR = Path("storage/tmp_uploads")
UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXT = {".mp4", ".mov", ".mkv", ".webm"}


@app.route("/api/health", methods=["GET"])
def health():
    # Render 등 배포 플랫폼의 헬스체크용 + 무료 티어가 잠드는 것을 막는
    # GitHub Actions "깨우기" 핑이 치는 엔드포인트
    return {"status": "ok"}, 200


@app.route("/", methods=["GET"])
def index():
    deps = check_dependencies()
    return render_template("index.html", deps=deps, result=None)


@app.route("/analyze", methods=["POST"])
def analyze():
    deps = check_dependencies()

    file = request.files.get("video")
    if not file or file.filename == "":
        flash("영상 파일을 선택해주세요.")
        return redirect(url_for("index"))

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        flash(f"지원하지 않는 파일 형식입니다: {ext} (지원: {', '.join(ALLOWED_EXT)})")
        return redirect(url_for("index"))

    run_stt = request.form.get("run_stt") == "on"

    # 1. 임시 저장
    tmp_path = UPLOAD_TMP_DIR / file.filename
    file.save(tmp_path)

    try:
        # 2. InputHandler로 정식 저장 위치 이동 + 검증
        input_handler = InputHandler()
        video = input_handler.handle_file_upload(str(tmp_path), file.filename)
        if not input_handler.validate(video):
            flash("유효하지 않은 영상 파일입니다.")
            return redirect(url_for("index"))

        # 3. OCR(+STT) 추출
        extraction_service = TextExtractionService(
            ocr=OCRExtractor(frame_interval_sec=1.0),
            stt=STTExtractor(model_size="small", language="ko"),
        )
        extracted = extraction_service.extract_all(video.video_id, video.saved_path, run_stt=run_stt)
        full_text = extracted.full_text()

        # 4. 판정
        wordlists = WordLists.load(
            banned_path="data/banned_words.sample.json",
            ambiguous_path="data/ambiguous_words.sample.json",
        )
        judge_engine = JudgeEngine(wordlists=wordlists, ai_analyzer=AIContextAnalyzer())

        ai_error = None
        try:
            result = judge_engine.judge(extracted)
        except Exception as e:
            # 오인유발어는 감지됐는데 AI 호출이 실패한 경우 (키 없음, 패키지 없음 등)
            # 서버를 죽이지 않고 에러 내용을 화면에 보여준다
            ai_error = str(e)
            result = None

        return render_template(
            "index.html",
            deps=deps,
            result=result,
            full_text=full_text,
            filename=file.filename,
            ai_error=ai_error,
        )

    except Exception as e:
        traceback.print_exc()
        flash(f"처리 중 오류가 발생했습니다: {e}")
        return redirect(url_for("index"))

    finally:
        if tmp_path.exists():
            tmp_path.unlink()


if __name__ == "__main__":
    # 로컬 테스트: host="127.0.0.1" (내 컴퓨터에서만 접속)
    # 배포(Render 등) 환경: PORT 환경변수를 넘겨주고, 0.0.0.0으로 바인딩해야
    # 플랫폼이 외부 요청을 이 서버로 전달할 수 있음
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
