"""
영상(또는 글자) 한 건을 처음부터 끝까지 확인하는 생성기.
웹 서버(app.py)와 터미널(main.py)이 같이 쓴다.

내보내는 이벤트
  {"type": "step", "step": "frames"|"ocr"|"stt"|"dict"|"ai", "state": "doing"|"done"|"skip"|"fail", "sub": "..."}
  {"type": "result", "result": {...}}
  {"type": "error", "code": "...", "message": "..."}
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from pipeline import extractors as X
from pipeline import foodsafety
from pipeline.judge_engine import JudgeEngine


def _step(k, state, sub=""):
    return {"type": "step", "step": k, "state": state, "sub": sub}


def _fmt(t: float) -> str:
    t = int(max(0, t))
    return f"{t // 60}:{t % 60:02d}"


CATEGORY_NAME = {"health": "건강기능식품", "general": "일반식품", "unknown": "모르겠어요"}


def lookup_product(segments: list[dict], category: str):
    """
    공공데이터(식품안전나라 C003)로 화면 글자 속 제품을 찾는다.
    찾으면 사용자가 무엇을 골랐든 건강기능식품 기준으로 바꾼다.
    생성기: 진행 이벤트를 내보내고 마지막에 ("done", product, category) 를 돌려준다.
    """
    if not foodsafety.api_key():
        yield _step("lookup", "skip", "공공데이터 인증키가 없어서 건너뛰어요")
        return None, category
    yield _step("lookup", "doing", "식품안전나라에서 제품을 찾는 중")
    lines = [ln for seg in segments if seg.get("src") != "voice" for ln in seg["text"].splitlines()]
    try:
        product = foodsafety.match_product(lines)
    except Exception:
        product = None
    if not product:
        yield _step("lookup", "done", "등록된 건강기능식품을 찾지 못했어요")
        return None, category
    yield _step("lookup", "done", f"건강기능식품으로 확인: {product['name']}")
    return product, "health"


def run_video(video: Path, engine: JudgeEngine, narration: str = "", use_stt: bool = True, category: str = "unknown"):
    try:
        yield _step("frames", "doing", "영상을 여는 중")
        info = X.probe(video)
        segments: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            frames = X.extract_frames(video, tmp, info["duration"])
            kept = X.keep_changed(frames)
            yield _step("frames", "done", f"{_fmt(info['duration'])} 영상에서 장면 {len(kept)}장")

            prev, n_lines = None, 0
            for i, (t, path) in enumerate(kept):
                yield _step("ocr", "doing", f"장면 {i + 1} / {len(kept)} 읽는 중")
                lines = X.ocr_frame(path)
                text = "\n".join(lines)
                if text and text != prev:
                    segments.append({"text": text, "t": round(t, 1), "thumb": X.thumbnail(path), "src": "screen"})
                    n_lines += len(lines)
                prev = text
            yield _step("ocr", "done", f"글자 {n_lines}줄을 읽었어요")

            if not use_stt:
                yield _step("stt", "skip", "소리 듣기를 껐어요")
            elif not info["has_audio"]:
                yield _step("stt", "skip", "소리가 없는 영상이에요")
            else:
                yield _step("stt", "doing", "소리를 듣고 글자로 바꾸는 중")
                try:
                    voice = X.transcribe(video, tmp)
                    segments.extend(voice)
                    yield _step("stt", "done", f"말 {len(voice)}문장을 들었어요")
                except X.STTUnavailable:
                    yield _step("stt", "skip", "서버에 음성 인식이 설치돼 있지 않아요")

        for line in (narration or "").splitlines():
            if line.strip():
                segments.append({"text": line.strip(), "t": None, "thumb": None, "src": "voice"})

        user_category = category
        product, category = yield from lookup_product(segments, category)
        yield from _with_user_category(engine.run(segments, category, product), user_category)
    except X.ExtractError as e:
        yield {"type": "error", "code": e.code, "message": str(e)}


def run_text(text: str, engine: JudgeEngine, category: str = "unknown"):
    segments = [{"text": t, "t": None, "thumb": None, "src": "text"} for t in text.splitlines() if t.strip()]
    user_category = category
    product, category = yield from lookup_product(segments, category)
    yield from _with_user_category(engine.run(segments, category, product), user_category)


def _with_user_category(events, user_category: str):
    """결과에 사용자가 처음 고른 종류를 같이 적는다 (공공데이터로 바뀌었는지 화면에서 알리려고)"""
    for ev in events:
        if ev["type"] == "result":
            ev["result"]["user_category"] = user_category
        yield ev
