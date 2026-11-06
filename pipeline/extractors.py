"""
영상에서 글자 뽑기 — 서버 버전

  화면 글자: ffmpeg로 장면을 뽑고, 화면이 바뀐 장면만 남겨 Tesseract(kor+eng)로 읽는다.
  소리:      ffmpeg로 소리를 16kHz 모노 wav로 뽑아 faster-whisper로 한국어 문장을 만든다.

필요한 것: ffmpeg(ffprobe 포함), tesseract-ocr + 한국어 데이터(kor), pip: pytesseract pillow faster-whisper
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageChops

# 장면 바뀜 감지 (웹 버전과 같은 방식): 96x96 회색조로 줄여 12x12 칸마다 평균 차이를 재고,
# 가장 크게 바뀐 칸이 기준보다 크면 새 장면으로 본다. 화면 전체 평균으로 재면 자막 한 줄 바뀐 것을 놓친다.
SIG = 96
CELLS = 8          # 96 / 12
SCENE_DIFF = 12    # 0~255
MAX_FRAMES = 60    # 이보다 길면 간격을 넓힌다


class ExtractError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _run(args: list[str]) -> subprocess.CompletedProcess:
    r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise ExtractError("video_error", r.stderr.decode(errors="ignore")[-500:])
    return r


def probe(video: Path) -> dict:
    """길이(초)와 소리 트랙 유무"""
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(video)])
    info = json.loads(r.stdout or b"{}")
    try:
        duration = float(info.get("format", {}).get("duration") or 0)
    except ValueError:
        duration = 0.0
    streams = [s.get("codec_type") for s in info.get("streams", [])]
    if "video" not in streams:
        raise ExtractError("video_error", "영상 트랙이 없습니다")
    return {"duration": duration, "has_audio": "audio" in streams}


def extract_frames(video: Path, out_dir: Path, duration: float) -> list[tuple[float, Path]]:
    """1초에 한 장 (길면 최대 MAX_FRAMES장이 되게 간격을 넓혀서)"""
    fps = 1.0 if duration <= MAX_FRAMES else MAX_FRAMES / duration
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf", f"fps={fps:.4f},scale='min(1280,iw)':-2",
          str(out_dir / "f_%04d.png")])
    files = sorted(out_dir.glob("f_*.png"))
    step = 1.0 / fps
    return [(i * step, p) for i, p in enumerate(files)]


def _signature(path: Path) -> Image.Image:
    return Image.open(path).convert("L").resize((SIG, SIG), Image.BILINEAR)


def scene_changed(a: Image.Image, b: Image.Image) -> bool:
    diff = ImageChops.difference(a, b).resize((CELLS, CELLS), Image.BOX)  # 칸마다 평균
    return max(diff.getdata()) > SCENE_DIFF


def keep_changed(frames: list[tuple[float, Path]]) -> list[tuple[float, Path]]:
    kept, last = [], None
    for t, p in frames:
        sig = _signature(p)
        if last is None or scene_changed(sig, last):
            kept.append((t, p))
            last = sig
    return kept


def ocr_frame(path: Path, lang: str = "kor+eng") -> list[str]:
    import pytesseract
    # psm 11: 화면 여기저기 흩어진 글자를 찾아 읽는다. 광고 화면은 여백이 많고 글자가 띄엄띄엄 있어서
    # 기본값(3)은 글자를 놓치고, psm 6은 자막 한 줄을 조각내는 경우가 있었다 (tests 영상으로 비교).
    text = pytesseract.image_to_string(Image.open(path), lang=lang, config="--psm 11")
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def thumbnail(path: Path, size: int = 160) -> str:
    im = Image.open(path).convert("RGB")
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=70)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
# 소리 -> 글자
# ---------------------------------------------------------------------------

class STTUnavailable(Exception):
    pass


_whisper = None


def _whisper_model():
    """모델은 프로세스마다 한 번만 읽는다. 크기는 WHISPER_MODEL 환경변수 (기본 base, 무료 서버 메모리 512MB 기준)."""
    global _whisper
    if _whisper is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise STTUnavailable("faster-whisper가 설치되어 있지 않습니다") from e
        _whisper = WhisperModel(os.environ.get("WHISPER_MODEL", "base"), device="cpu", compute_type="int8")
    return _whisper


def transcribe(video: Path, tmp_dir: Path) -> list[dict]:
    model = _whisper_model()
    wav = tmp_dir / "audio.wav"
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    segs, _ = model.transcribe(str(wav), language="ko", vad_filter=True)
    return [{"text": s.text.strip(), "t": round(s.start, 1), "thumb": None, "src": "voice"} for s in segs if s.text.strip()]


def stt_installed() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def check_dependencies() -> dict[str, bool]:
    status = {"ffmpeg": shutil.which("ffmpeg") is not None, "ffprobe": shutil.which("ffprobe") is not None,
              "tesseract": shutil.which("tesseract") is not None}
    try:
        import pytesseract
        status["pytesseract"] = True
        status["tesseract_kor"] = "kor" in pytesseract.get_languages() if status["tesseract"] else False
    except Exception:
        status["pytesseract"] = False
        status["tesseract_kor"] = False
    status["faster_whisper"] = stt_installed()
    try:
        import anthropic  # noqa: F401
        status["anthropic"] = True
    except ImportError:
        status["anthropic"] = False
    return status
