"""
주장구조 엔진 — 기존 JudgeEngine 과 같은 모양의 결과를 낸다. 판정과 점수는 규칙만으로 정한다.

  ① 주장 추출 (extract.py)   문장 -> 대상 + 강도 + 맥락 (+ 확신)
  ② 표준화                   대상을 질병 목록·기능성 목록·적발 사례 표현에 연결
  ③ 판정표 (rules.py)        제품 종류 × 대상 종류 × 강도 × 문형 -> 금지 / 확인 필요 / 허용 / 면제
  ④ 근거 표시                해당 문장, 규칙 번호, 법 조항, 위험도 점수
  ⑤ AI 참고 의견 (선택)      '확인 필요' 문장만 AI(Groq/Claude)에게 묻고 이유를 덧붙인다.
                             판정·점수는 바꾸지 않는다. 키가 없거나 실패하면 이 단계만 빠진다.

결과: {"sentences", "hits"(기존 화면과 같은 모양), "claims", "risk", "verdict", "category", "labeled", "limits"}
"""

from __future__ import annotations

import re

from pipeline import foodsafety
from pipeline.claims import lexicon as L
from pipeline.claims import rules
from pipeline.claims.extract import extract, sentences_from
from pipeline.judge_engine import CATEGORIES, AIContextAnalyzer, AIUnavailable

LIMITS = [
    "영상 속 장면(전후 비교 사진, 출연자 복장 등)과 글자로 나오지 않은 암시 표현은 확인하지 못해요.",
    "'확인된 표현이 없어요'는 적법하다는 뜻이 아니라, 이 범위에서는 못 찾았다는 뜻이에요.",
]


def analyze(segments: list[dict], lex: L.Lexicon, category: str = "general", product: dict | None = None) -> dict:
    if category not in CATEGORIES:
        category = "unknown"
    sentences = sentences_from(segments)
    joined = re.sub(r"\s+", "", "".join(s["text"] for s in sentences))
    disclaimer = bool(L.DISCLAIMER.search(joined))
    claims = extract(sentences, lex)
    ctx = {"lex": lex, "disclaimer": disclaimer, "product": product}
    findings = rules.judge_all(claims, category, ctx)

    risk = rules.risk(findings)
    if category == "unknown":
        alt = [rules.judge(f.claim, "health", ctx) for f in findings]
        risk["ifHealth"] = rules.risk(alt)["score"]

    hits = []
    for f in findings:
        if f.level not in ("ban", "review"):
            continue
        S = sentences[f.claim.sent]
        h = {"level": f.level, "tag": f.tag, "title": f.title, "S": S, "ranges": f.claim.ranges, "basis": f.basis,
             "rule": f.rule, "score": int(round(f.score * 100))}
        if f.level == "review":
            h["reason"] = f.reason
        if f.if_health:
            h["ifHealth"] = True
        hits.append(h)

    return {
        "sentences": sentences,
        "hits": hits,
        "claims": [_claim_dict(f) for f in findings],
        "risk": risk,
        "verdict": verdict(findings),
        "category": category,
        "labeled": disclaimer,
        "limits": LIMITS,
        "engine": "claims",
    }


def _claim_dict(f) -> dict:
    c = f.claim
    return {"sentence": c.evidence, "kind": c.kind, "target": c.target, "intensity": c.intensity or "없음",
            "context": c.context, "via": c.via, "confidence": c.conf, "level": f.level, "rule": f.rule,
            "title": f.title, "basis": f.basis, "score": int(round(f.score * 100)), "ranges": c.ranges}


def verdict(findings: list) -> str:
    """ban(금지) / warn(오해 소지 또는 확인 필요) / ok(문제 없어 보임)"""
    if any(f.level == "ban" for f in findings):
        return "ban"
    if any(f.level == "review" for f in findings):
        return "warn"
    return "ok"


class ClaimsEngine:
    """JudgeEngine 과 같은 run()/judge() 인터페이스 (app.py 에서 바꿔 끼울 수 있다)."""

    def __init__(self, lexicon: L.Lexicon | None = None, ai=None):
        self.lex = lexicon or L.load_lexicon()
        # AI 참고 의견. ai=False 로 끈다 (시험용). 기본은 환경변수의 키(GROQ_API_KEY, ANTHROPIC_API_KEY)를 본다.
        self.ai = _NoAI() if ai is False else (ai or AIContextAnalyzer())

    def run(self, segments: list[dict], category: str = "general", product: dict | None = None):
        def step(k, state, sub):
            return {"type": "step", "step": k, "state": state, "sub": sub}

        yield step("dict", "doing", "문장에서 주장을 뽑아 판정표와 대조하는 중")
        result = analyze(segments, self.lex, category, product)
        ban = sum(1 for h in result["hits"] if h["level"] == "ban")
        rev = sum(1 for h in result["hits"] if h["level"] == "review")
        yield step("dict", "done", f"금지 {ban}건, 확인 필요 {rev}건 · 위험도 {result['risk']['score']}")
        ai, ai_error = None, None
        review = [h for h in result["hits"] if h["level"] == "review"]
        if not review:
            yield step("ai", "skip", "규칙으로 판정했어요 (AI에게 물을 문장이 없어요)")
        elif not self.ai.available():
            yield step("ai", "skip", "규칙으로 판정했어요 (AI 키 없음)")
        else:
            yield step("ai", "doing", f"AI({self.ai.label()})가 '확인 필요' 문장 {len(review)}개에 의견을 다는 중")
            try:
                ai = self.ai.review(review, result["sentences"], result["category"])
                ai.update(source="ai", role="opinion", provider=self.ai.label())
                yield step("ai", "done", "AI 참고 의견을 붙였어요 (판정은 규칙 그대로)")
            except AIUnavailable as e:
                ai_error = {"code": e.code}
            except Exception as e:  # 네트워크 등 예상 못 한 오류도 규칙 판정은 그대로 낸다
                ai_error = {"code": "ai_failed", "message": str(e)[:200]}
            if ai is None:
                yield step("ai", "skip", "AI 의견을 받지 못해 규칙으로만 판정했어요")
        result["ai"] = ai
        result["ai_error"] = ai_error
        result["product"] = product
        result["official_count"] = len(foodsafety.functional_phrases())
        yield {"type": "result", "result": result}

    def judge(self, segments: list[dict], category: str = "general") -> dict:
        for ev in self.run(segments, category):
            if ev["type"] == "result":
                return ev["result"]
        raise RuntimeError("판단 결과가 없습니다")


class _NoAI:
    def available(self) -> bool:
        return False

    def label(self) -> str:
        return "AI"


def main(argv=None):
    import argparse
    import json

    ap = argparse.ArgumentParser(description="주장구조 엔진으로 문장을 판정한다 (AI 없음)")
    ap.add_argument("--text", required=True)
    ap.add_argument("--category", default="unknown", choices=CATEGORIES)
    a = ap.parse_args(argv)
    r = ClaimsEngine(ai=False).judge([{"text": a.text, "t": None, "thumb": None, "src": "text"}], a.category)
    print(f"위험도 {r['risk']['score']}/100 ({r['risk']['band']})" + (f" · 건강기능식품이라면 {r['risk']['ifHealth']}" if 'ifHealth' in r['risk'] else ""))
    print("판정:", r["verdict"], "· 기능성표시식품 문구:", "있음" if r["labeled"] else "없음")
    for c in r["claims"]:
        print(f"  [{c['level']}] {c['rule']} {c['title']} ({c['score']}점) — {c['basis']}")
    print(json.dumps(r["risk"]["parts"], ensure_ascii=False)) if False else None


if __name__ == "__main__":
    main()
