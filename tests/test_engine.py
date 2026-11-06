"""python -m pytest tests 또는 python tests/test_engine.py"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.judge_engine import analyze, load_dictionary, verdict  # noqa: E402

CASES = json.loads((ROOT / "tests" / "engine_cases.json").read_text(encoding="utf-8"))["cases"]
ENTRIES = load_dictionary()


def run(text):
    r = analyze([{"text": text, "t": None, "thumb": None, "src": "text"}], ENTRIES)
    return [f"{h['level']}:{h['title']}" for h in r["hits"]]


def test_cases():
    for c in CASES:
        assert run(c["text"]) == c["expect"], c["text"]


def test_verdict():
    assert verdict([{}], [], None) == "ban"
    assert verdict([], [{}], None) == "warn"
    assert verdict([], [{}], {"misleading": False, "items": [{"n": 1, "misleading": True}]}) == "warn"
    assert verdict([], [{}], {"misleading": True, "items": [{"n": 1, "misleading": False}]}) == "ok"
    assert verdict([], [], None) == "ok"


def test_dictionary_size():
    assert len(ENTRIES) == 184


if __name__ == "__main__":
    test_cases(); test_verdict(); test_dictionary_size()
    print(f"OK: {len(CASES)} cases")
