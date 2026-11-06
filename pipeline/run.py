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
from pipeline.judge_engine import JudgeEngine


def _step(k, state, sub=""):
    return {"type": "step", "step": k, "state": state, "sub": sub}


def _fmt(t: float) -> str:
    t = int(max(0, t))
    return f"{t // 60}:{t % 60:02d}"


def run_video(video: Path, engine: JudgeEngine, narration: str = "", use_stt: bool = True):
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

        yield from engine.run(segments)
    except X.ExtractError as e:
        yield {"type": "error", "code": e.code, "message": str(e)}


def run_text(text: str, engine: JudgeEngine):
    segments = [{"text": t, "t": None, "thumb": None, "src": "text"} for t in text.splitlines() if t.strip()]
    yield from engine.run(segments)
