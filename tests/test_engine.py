"""python -m pytest tests 또는 python tests/test_engine.py"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.judge_engine import analyze, load_dictionary, verdict  # noqa: E402

CASES = json.loads((ROOT / "tests" / "engine_cases.json").read_text(encoding="utf-8"))["cases"]
ENTRIES = load_dictionary()


def run(text, category="general"):
    r = analyze([{"text": text, "t": None, "thumb": None, "src": "text"}], ENTRIES, category)
    return [f"{h['level']}:{h['title']}" + (" (ifHealth)" if h.get("ifHealth") else "") for h in r["hits"]]


def test_cases():
    for c in CASES:
        assert run(c["text"], c["category"]) == c["expect"], (c["category"], c["text"])


def test_verdict():
    assert verdict([{}], [], None) == "ban"
    assert verdict([], [{}], None) == "warn"
    assert verdict([], [{}], {"misleading": False, "items": [{"n": 1, "misleading": True}]}) == "warn"
    assert verdict([], [{}], {"misleading": True, "items": [{"n": 1, "misleading": False}]}) == "ok"
    assert verdict([], [], None) == "ok"


def test_dictionary_size():
    assert len([e for e in ENTRIES if e.stage != "func"]) == 184  # 팀 공통 사전
    assert len([e for e in ENTRIES if e.stage == "func"]) > 0     # 제품 종류별 기능성 (초안)


def test_health_food_rules():
    t = "관절연골, 근육, 뼈 동시케어"
    assert run(t, "health") == []                       # 건강기능식품: 허용
    assert run(t, "general") == ["ban:관절 + 케어"]       # 일반식품: 금지
    assert run(t, "unknown") == ["ban:관절 + 케어 (ifHealth)"]  # 모름: 일반식품 기준 + 안내
    assert run("한 달 만에 10kg 감량 보장", "health") == ["ban:감량 + 보장"]  # 과장은 종류와 상관없이 금지


if __name__ == "__main__":
    test_cases(); test_verdict(); test_dictionary_size(); test_health_food_rules()
    print(f"OK: {len(CASES)} cases")
