"""규칙으로 문맥 판단하기 시험. python tests/test_context_rules.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.context_rules import judge_sentence, review  # noqa: E402
from pipeline.judge_engine import AIContextAnalyzer, JudgeEngine  # noqa: E402

# (이유, 문장, 기대: True=의심 / False=허용 / None=판단 어려움)
CASES = [
    ("expert_only", "약사가 직접 개발한 여주 건강즙", False),
    ("expert_only", "한의사가 권하는 건강차", True),
    ("expert_only", "약사가 개발하고 매일 챙겨 먹는 건강즙", True),   # 의심 신호가 우선
    ("expert_only", "의사 선생님 이야기", None),
    ("expert_use", "한의사가 직접 사용하는 원료예요", True),
    ("expert_use", "임산부는 의사와 상담 후 사용하세요", False),
    ("disease_only", "당뇨 걱정되는 분들 주목!", True),
    ("disease_only", "당뇨에 좋은 차", True),
    ("disease_only", "당뇨 환자는 섭취 전 의사와 상담하세요", False),
    ("disease_only", "아토피", None),
    ("func_only", "혈당 걱정되시죠?", True),
    ("func_only", "관절플랜 곡물맛", True),
    ("func_only", "칼슘은 뼈와 치아 형성에 필요", False),
    ("func_only", "피로", None),
]


def test_sentences():
    for reason, text, expect in CASES:
        got = judge_sentence(reason, text)["misleading"]
        assert got is expect, (reason, text, expect, got)


def test_review_shape():
    hits = [{"reason": "disease_only", "S": {"text": "당뇨 걱정되는 분들 주목!"}},
            {"reason": "expert_only", "S": {"text": "약사가 직접 개발한 여주 건강즙"}}]
    r = review(hits)
    assert r["source"] == "rules" and r["misleading"] is True
    assert [it["misleading"] for it in r["items"]] == [True, False]


def test_engine_falls_back_to_rules_without_key():
    engine = JudgeEngine(ai=AIContextAnalyzer(api_key=""))
    engine.ai.api_key = None
    seg = [{"text": "약사가 직접 개발한 여주 건강즙을 만나 보세요.", "t": None, "thumb": None, "src": "text"}]
    res = engine.judge(seg, "general")
    assert res["ai"]["source"] == "rules" and res["ai_error"] == {"code": "no_api_key"}
    assert res["verdict"] == "ok"
    seg = [{"text": "당뇨 걱정되는 분들 주목!", "t": None, "thumb": None, "src": "text"}]
    assert engine.judge(seg, "general")["verdict"] == "warn"
    seg = [{"text": "아토피", "t": None, "thumb": None, "src": "text"}]
    assert engine.judge(seg, "general")["verdict"] == "warn"  # 판단 어려움도 확인 필요로


if __name__ == "__main__":
    test_sentences(); test_review_shape(); test_engine_falls_back_to_rules_without_key()
    print(f"OK: {len(CASES)} sentences")
