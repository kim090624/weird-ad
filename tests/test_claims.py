"""python tests/test_claims.py — 주장구조 엔진 시험 (AI 없음)

1) 기존 엔진(tests/engine_cases.json)과 판정(ban/warn/ok)이 같은지
2) 새 엔진이 더 잘해야 하는 문장 (부정, 경고, OCR 오류, 앞 문장 이어받기, 기능성표시식품 문형)
3) 위험도 점수가 판정과 어긋나지 않는지
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.claims import ClaimsEngine  # noqa: E402
from pipeline.judge_engine import analyze as old_analyze, load_dictionary  # noqa: E402

ENGINE = ClaimsEngine()
OLD = load_dictionary()
CASES = json.loads((ROOT / "tests" / "engine_cases.json").read_text(encoding="utf-8"))["cases"]


def run(text, category="general", product=None):
    return ENGINE.judge([{"text": text, "t": None, "thumb": None, "src": "text"}], category) if product is None else \
        next(ev["result"] for ev in ENGINE.run([{"text": text, "t": None, "thumb": None, "src": "text"}], category, product)
             if ev["type"] == "result")


def rules_of(r):
    return [(c["rule"], c["level"]) for c in r["claims"]]


def test_same_verdict_as_old_engine():
    for c in CASES:
        seg = [{"text": c["text"], "t": None, "thumb": None, "src": "text"}]
        o = old_analyze(seg, OLD, c["category"])
        ov = "ban" if any(h["level"] == "ban" for h in o["hits"]) else ("warn" if o["hits"] else "ok")
        assert run(c["text"], c["category"])["verdict"] == ov, (c["category"], c["text"])


def test_negation_and_warning_are_exempt():
    r = run("이 제품은 당뇨를 치료하지 않습니다.")
    assert r["verdict"] == "ok" and r["claims"][0]["level"] == "exempt"
    r = run("당뇨 완치를 내세운 광고가 적발됐어요.")
    assert r["verdict"] == "ok"
    # '걱정 없는'은 부정이 아니라 효능 주장
    assert run("암 걱정 없는 식단", "general")["verdict"] == "warn"
    assert run("암 걱정 없는 식단은 없습니다", "general")["verdict"] == "ok"


def test_review_context_is_not_exempt():
    r = run("이거 먹고 당뇨 완치됐어요")
    assert r["verdict"] == "ban" and r["claims"][0]["context"] == "review"


def test_ocr_error_is_caught_with_lower_confidence():
    r = run("혈관 정소로 건강하게")
    assert r["verdict"] == "warn" and r["claims"][0]["via"] == "fuzzy"
    exact = run("혈관 청소로 건강하게")
    assert exact["risk"]["score"] > r["risk"]["score"]


def test_pronoun_carries_previous_target():
    r = run("당뇨로 고생하셨죠? 이걸로 잡으세요")
    assert r["verdict"] == "ban"
    assert any(c["via"] == "carry" and c["rule"] == "R1" for c in r["claims"])


def test_drug_replace():
    r = run("이거 먹고 혈당 확 내려갔어요, 약 끊었습니다.")
    assert ("R4", "ban") in rules_of(r) and ("R3", "ban") in rules_of(r)
    assert run("약 끊지 마세요")["verdict"] == "ok"


def test_labeled_food_rules():
    ing = "본 제품에는 장 건강에 도움을 줄 수 있다고 알려진 프로바이오틱스가 들어 있습니다."
    off = "본 제품은 건강기능식품이 아닙니다."
    # 원료 문형 + 필수 문구 -> 허용
    r = run(ing + " " + off)
    assert r["labeled"] and r["verdict"] == "ok" and ("L1", "ok") in rules_of(r)
    # 원료 문형인데 필수 문구가 없으면 확인 필요
    r = run(ing)
    assert not r["labeled"] and r["verdict"] == "warn" and ("L4", "review") in rules_of(r)
    # 완제품 문형: 일반식품은 금지, 문구가 있으면 확인 필요, 건강기능식품은 허용
    assert run("이 음료는 장 건강에 도움을 줄 수 있습니다.")["verdict"] == "ban"
    assert run("이 음료는 장 건강에 도움을 줄 수 있습니다. " + off)["verdict"] == "warn"
    assert run("이 음료는 장 건강에 도움을 줄 수 있습니다.", "health")["verdict"] == "ok"
    # 제외된 기능성(성기능)은 기능성표시식품에서도 금지
    assert run("본 제품에는 성기능에 도움을 줄 수 있다고 알려진 원료가 들어 있습니다. " + off)["verdict"] == "ban"


def test_nutrient_function_is_allowed():
    assert run("칼슘은 뼈와 치아 형성에 필요합니다.", "general")["verdict"] == "ok"


def test_health_product_known_functions():
    prod = {"name": "테스트 관절", "functions": "관절 및 연골 건강에 도움을 줄 수 있음"}
    assert run("관절 건강에 도움", "health", prod)["verdict"] == "ok"
    assert run("혈당 조절에 도움", "health", prod)["verdict"] == "warn"


def test_risk_matches_verdict():
    for c in CASES:
        r = run(c["text"], c["category"])
        s = r["risk"]["score"]
        if r["verdict"] == "ban":
            assert s >= 60, (c["text"], s)
        elif r["verdict"] == "warn":
            assert 0 < s <= 59, (c["text"], s)
        else:
            assert s < 25, (c["text"], s)  # 허용 문장은 낮음


def test_eval_cases():
    """tests/eval_cases.json: 일반 문장은 새 엔진이 정답, 'limit' 문장은 알려진 한계라 아직 틀린다 (고치면 limit 를 지운다)."""
    sys.path.insert(0, str(ROOT / "tests"))
    from eval_compare import evaluate
    for r in evaluate():
        if r.get("limit"):
            assert r["new"] != r["verdict"], ("한계가 풀렸어요. eval_cases.json 에서 limit 를 지우세요", r["text"])
        else:
            assert r["new"] == r["verdict"], (r["text"], r["new"], r["verdict"])


def test_unknown_gives_two_scores():
    r = run("면역력 증진에 좋은 홍삼", "unknown")
    assert r["risk"]["score"] >= 60 and r["risk"]["ifHealth"] < 25


def test_repeated_rule_does_not_inflate():
    one = run("뼈 케어", "general")["risk"]["score"]
    three = run("관절연골, 근육, 뼈 동시케어", "general")["risk"]["score"]
    assert three == one


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print(f"OK: {len(CASES)} cases + claims tests")
