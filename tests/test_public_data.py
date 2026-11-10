"""공공데이터(식품안전나라) 연결 시험 — 실제 인터넷 없이 가짜 응답으로. python tests/test_public_data.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import foodsafety, run  # noqa: E402
from pipeline.judge_engine import AIContextAnalyzer, JudgeEngine, analyze, load_dictionary  # noqa: E402

ENTRIES = load_dictionary()
# 식품안전나라 응답 모양 그대로 (C003 '뉴케어' 검색, I2710·I-0050 일부)
NUCARE = ({"name": "뉴케어 관절플랜 곡물맛", "company": "대상웰라이프(주) 천안2공장", "report_no": "2",
           "functions": "①관절 및 연골 건강에 도움을 줄 수 있음\n①근육, 결합조직 등 신체조직의 구성성분②뼈와 치아 형성에 필요"},
          {"name": "뉴케어 관절플랜 밀크맛", "company": "대상웰라이프(주) 천안2공장", "report_no": "3",
           "functions": "①관절 및 연골 건강에 도움을 줄 수 있음"})
OFFICIAL = []
for svc, text in [("I-0050", "(국문) 관절 건강에 도움을 줄 수 있음(기타기능II)"),
                  ("I2710", "(1) 탄수화물이 지방으로 합성되는 것을 억제하여 체지방 감소에 도움을 줄 수 있음"),
                  ("I2710", "배변활동 원활에 도움을 줄 수 있음"),
                  ("I-0050", "(국문) 혈소판 응집억제를 통한 혈액흐름 개선에 도움을 줄 수 있음")]:
    for ph in foodsafety.phrases_from_text(text):
        for k in foodsafety.keys_from_phrase(ph):
            OFFICIAL.append({"key": k, "phrase": ph, "ingredient": "", "source": svc})


def seg(t):
    return [{"text": t, "t": None, "thumb": None, "src": "text"}]


def titles(r):
    return [(h["level"], h["tag"], h["title"]) for h in r["hits"]]


def test_official_phrase_bans_general_food():
    # 초안 사전에 없는 표현도 공식 문구로 잡는다
    r = analyze(seg("혈액흐름 개선에 도움을 주는 차"), ENTRIES, "general", official=OFFICIAL)
    assert titles(r) == [("ban", "기능성 표현", "혈액흐름 개선")], titles(r)
    assert r["hits"][0]["official"]["source"] == "I-0050"
    r = analyze(seg("배변활동 원활에 도움을 주는 차"), ENTRIES, "general", official=OFFICIAL)
    assert titles(r) == [("ban", "기능성 표현", "배변 + 도움")] and r["hits"][0]["official"]["source"] == "I2710", titles(r)
    # 초안 규칙이 이미 잡은 문장은 공식 문구 근거로 올라간다
    r = analyze(seg("관절 건강에 도움을 드려요"), ENTRIES, "general", official=OFFICIAL)
    assert titles(r) == [("ban", "기능성 표현", "관절 + 도움")] and "식약처 인정 기능성 문구" in r["hits"][0]["basis"]
    # 건강기능식품이면 허용
    assert analyze(seg("배변활동 원활에 도움을 주는 차"), ENTRIES, "health", official=OFFICIAL)["hits"] == []


def test_unapproved_function_for_found_product():
    p = NUCARE[0]
    r = analyze(seg("면역력 증진에 도움을 줘요"), ENTRIES, "health", official=OFFICIAL, product=p)
    assert ("review", "인정 밖 기능", "면역") in titles(r), titles(r)
    r = analyze(seg("관절 건강 케어"), ENTRIES, "health", official=OFFICIAL, product=p)
    assert not any(h["tag"] == "인정 밖 기능" for h in r["hits"]), titles(r)


def test_lookup_switches_to_health():
    orig_key, orig_search, orig_phr = foodsafety.api_key, foodsafety.search_products, foodsafety.functional_phrases
    try:
        foodsafety.api_key = lambda: "test"
        foodsafety.search_products = lambda q: NUCARE if q in ("뉴케어", "관절플랜", "곡물맛") else ()
        foodsafety.functional_phrases = lambda: tuple(OFFICIAL)
        engine = JudgeEngine(ai=AIContextAnalyzer(api_key=""))
        engine.ai.api_key = None
        evs = list(run.run_text("뉴케어\n관절플랜\n곡물맛\n관절연골, 근육, 뼈 동시케어", engine, "unknown"))
        steps = [(e["step"], e["state"]) for e in evs if e["type"] == "step"]
        res = evs[-1]["result"]
        assert ("lookup", "done") in steps
        assert res["product"]["name"] == "뉴케어 관절플랜 곡물맛"
        assert res["category"] == "health" and res["user_category"] == "unknown"
        assert not any(h["tag"] == "기능성 표현" for h in res["hits"])  # 건강기능식품이라 기능성 표현 허용
    finally:
        foodsafety.api_key, foodsafety.search_products, foodsafety.functional_phrases = orig_key, orig_search, orig_phr


def test_no_key_skips_lookup():
    orig = foodsafety.api_key
    try:
        foodsafety.api_key = lambda: None
        engine = JudgeEngine(ai=AIContextAnalyzer(api_key=""))
        engine.ai.api_key = None
        evs = list(run.run_text("관절 건강 케어", engine, "general"))
        assert ("lookup", "skip") in [(e["step"], e["state"]) for e in evs if e["type"] == "step"]
        assert evs[-1]["result"]["category"] == "general"
    finally:
        foodsafety.api_key = orig


if __name__ == "__main__":
    test_official_phrase_bans_general_food(); test_unapproved_function_for_found_product()
    test_lookup_switches_to_health(); test_no_key_skips_lookup()
    print("OK: public data")
