"""
웹 서버 (Flask) — 애드스캔 화면과 분석 API를 함께 내보낸다.

  GET  /              애드스캔 화면 (web_demo.html, 서버 모드)
  POST /api/analyze   영상(video) 또는 글자(text)를 받아 진행 상황과 결과를 한 줄씩 JSON으로 흘려보냄
  GET  /api/health    살아 있는지 확인 (Render 헬스체크, 깨우기 핑)

로컬 실행: python app.py  ->  http://127.0.0.1:5000
배포: Dockerfile + render.yaml (gunicorn으로 실행)

ANTHROPIC_API_KEY 가 없으면 문맥 확인 단계만 "확인 필요"로 남기고 나머지는 그대로 동작한다.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path

from flask import Flask, Response, jsonify, request, stream_with_context

from pipeline.extractors import check_dependencies
from pipeline.judge_engine import JudgeEngine
from pipeline.run import run_text, run_video

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "web_demo.html"
ALLOWED_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200MB

ENGINE = JudgeEngine()
_page_cache = None


def page_html() -> str:
    """빌드된 화면에 '서버 모드' 표시를 넣어 내보낸다. 화면은 이 표시를 보고 Claude 대신 /api/analyze 를 부른다."""
    global _page_cache
    if _page_cache is None:
        html = PAGE.read_text(encoding="utf-8")
        mark = '<meta name="adscan-mode" content="server">'
        _page_cache = "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">" \
                      "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">" \
                      + mark + "</head><body>" + html + "</body></html>"
    return _page_cache


@app.get("/")
def index():
    return Response(page_html(), mimetype="text/html")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/status")
def status():
    deps = check_dependencies()
    deps["ai_key"] = ENGINE.ai.available()
    return jsonify(deps)


@app.post("/api/analyze")
def analyze():
    text = (request.form.get("text") or "").strip()
    narration = (request.form.get("narration") or "").strip()
    use_stt = request.form.get("stt", "1") != "0"
    # 사용자가 처음에 고른 제품 종류: health / general / unknown (모르면 일반식품 기준)
    category = request.form.get("category", "unknown")
    file = request.files.get("video")

    if not file and not text:
        return jsonify({"type": "error", "code": "no_input", "message": "영상이나 글자를 보내 주세요"}), 400

    tmp_dir = None
    if file:
        ext = Path(file.filename or "").suffix.lower()
        if ext not in ALLOWED_EXT:
            return jsonify({"type": "error", "code": "bad_type", "message": f"지원하지 않는 형식이에요: {ext}"}), 400
        # 응답을 흘려보내는 동안 요청 본문은 사라지므로 먼저 파일로 저장해 둔다
        tmp_dir = Path(tempfile.mkdtemp(prefix="adscan_"))
        video = tmp_dir / ("upload" + ext)
        file.save(video)

    def events():
        try:
            gen = run_video(video, ENGINE, narration, use_stt, category) if file else run_text(text, ENGINE, category)
            for ev in gen:
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except Exception as e:  # 예상 못 한 오류도 화면에 알린다
            app.logger.exception("analyze failed")
            yield json.dumps({"type": "error", "code": "server_error", "message": str(e)[:200]}, ensure_ascii=False) + "\n"
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    return Response(stream_with_context(events()), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "1") == "1", threaded=True)
