"""
③ 판정표 + 위험도 점수.

판정은 AI가 아니라 이 표가 한다. 칸(대상 종류 × 강도 × 제품 종류 × 문형)이 정해지면 결과는 항상 같다.

제품 종류
  general 일반식품, health 건강기능식품, unknown(모름: 일반식품 기준 + '건강기능식품이라면' 점수를 따로 낸다)
  기능성표시식품은 사용자에게 묻지 않고 광고 글자에 '건강기능식품이 아닙니다' 문구가 있는지로 판단한다
  (식약처 고시 「부당한 표시 또는 광고로 보지 아니하는 식품등의 기능성 표시 또는 광고에 관한 규정」 제6조).

점수
  주장 하나: s = 규칙 기본점수 × 확신(1.0 / 0.85 / 0.75)
  영상 전체: R = 100 × (1 − Π(1 − s))   (여러 건이면 쌓이되 100을 넘지 않는다)
  금지가 하나라도 있으면 60 이상, 없으면 59 이하로 맞춘다 (결과 문구와 숫자가 어긋나지 않게)
  구간: 0~24 낮음 / 25~59 주의 / 60~100 높음
  이 숫자는 규칙 가중치에서 나온 값이고 아직 평가 데이터로 보정되지 않았다. '위반 확률'이 아니다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pipeline.claims import lexicon as L

BASIS_DISEASE = "질병 예방·치료 표방 · 시행령 별표1 제1호"
BASIS_DRUG = "의약품 대체·오인 · 시행령 별표1 제2호(확인 필요)"
BASIS_FUNC = "일반식품의 기능성 표방 · 시행령 별표1 제3호(확인 필요)"
BASIS_EXAG = "인정 범위를 넘는 효과 보장 · 시행령 별표1 제4호(확인 필요)"
BASIS_LABELED = "기능성표시식품은 원료 기준 문형만 · 식약처 고시 제6조제1항제1호"
BASIS_DISCLAIMER = "광고에 '본 제품은 건강기능식품이 아닙니다' 문구가 필요해요 · 식약처 고시 제6조제3항"
BASIS_EXCLUDED = "기능성표시식품에서 제외된 기능성 · 식약처 고시 제4조제2항"

BANDS = ((60, "높음"), (25, "주의"), (0, "낮음"))


@dataclass
class Finding:
    claim: object
    level: str            # ban / review / ok / exempt
    rule: str
    tag: str
    title: str
    base: float
    basis: str
    reason: str = ""      # review 일 때 문맥 확인 이유 (context_rules 와 같은 이름)
    if_health: bool = False

    @property
    def score(self) -> float:
        if self.level == "exempt":
            return 0.0
        return round(self.base * self.claim.conf, 4)


def _title(c, extra: str = "") -> str:
    return f"{c.target} + {c.intensity_word or c.intensity}" if c.level else c.target


def judge(c, category: str, ctx: dict) -> Finding:
    """주장 하나를 판정한다. category 는 general 또는 health (unknown 은 호출하는 쪽에서 둘 다 부른다)."""
    if c.context in ("negated", "warning"):
        why = "부정하는 문장이에요" if c.context == "negated" else "적발·경고를 전하는 글이에요"
        return Finding(c, "exempt", "X1", "면제", c.target, 0.0, why)

    if c.kind == "ban":
        return Finding(c, "ban", "R0", "금지 표현", c.target, 0.95, f"{c.entry.cat} · {c.entry.basis}")

    if c.kind == "expert":
        if c.sub == "strong":
            return Finding(c, "ban", "E1", "금지 조합", c.target, 0.90, f"전문가 추천·보증 · {c.entry.basis}")
        if c.sub == "use":
            return Finding(c, "review", "E2", "문맥 확인", c.target, 0.45,
                           "전문가와 '사용'이 함께 나왔어요. '사용'은 다른 뜻으로도 많이 써서 문맥을 봐야 해요 (시행령 별표1 제5호라목).",
                           reason="expert_use")
        return Finding(c, "review", "E3", "문맥 확인", c.target, 0.35,
                       "전문가가 언급됐어요. 추천·보증이 아니라 연구·개발 참여 사실만 밝힌 거라면 괜찮아요 (시행령 별표1 제5호라목).",
                       reason="expert_only")

    if c.kind == "drug_replace":
        return Finding(c, "ban", "R4", "의약품 대체", c.target, 0.90, BASIS_DRUG)

    if c.kind == "disease":
        if c.exag:
            return Finding(c, "ban", "R5", "과장 표현", f"{c.target} + {c.exag}", 0.92, BASIS_EXAG)
        if c.level >= 2:
            return Finding(c, "ban", "R1", "금지 조합", _title(c), 0.92 if c.level >= 3 else 0.88,
                           f"{BASIS_DISEASE}" if not c.entry.basis else f"질병 예방·치료 표방 · {c.entry.basis}")
        if c.level == 1:
            return Finding(c, "review", "R2", "문맥 확인", _title(c), 0.55,
                           "질병 이름과 '좋다·도움' 같은 말이 함께 나왔어요. 질병에 효과가 있다고 받아들일 수 있는지 봐야 해요 (시행령 별표1 제1호).",
                           reason="disease_only")
        return Finding(c, "review", "R2", "문맥 확인", c.target, 0.40,
                       "질병 이름이 나왔어요. 질병을 예방·치료한다고 받아들일 수 있는지 봐야 해요 (시행령 별표1 제1호).",
                       reason="disease_only")

    if c.kind == "function":
        return _function(c, category, ctx)

    if c.kind == "case":
        word_kind = c.entry.cat
        # 건강기능식품은 인정 기능성을 쓸 수 있어서, '인정 기능성 확인' 유형의 문맥 검토 표현은 따로 보지 않는다
        if category == "health" and c.sub == "문맥 검토" and word_kind.startswith("거짓·과장"):
            return Finding(c, "ok", "C0", "사례 표현", c.target, 0.0, "건강기능식품의 인정 기능성일 수 있어요")
        base = 0.55 if c.sub == "우선 검토" else 0.40
        meta = ctx["lex"].case_meta[c.entry.note]
        return Finding(c, "review", "C1", "사례 표현", c.target, base,
                       f"{word_kind} · {c.entry.basis} · 출처: {c.entry.source} ({meta['evidence_type']})",
                       reason="case")
    raise ValueError(c.kind)


def _function(c, category: str, ctx: dict) -> Finding:
    text = c.evidence
    nospace = re.sub(r"\s+", "", text)
    labeled = ctx["disclaimer"]

    if c.exag:
        return Finding(c, "ban", "R5", "과장 표현", f"{c.target} + {c.exag}", 0.90, BASIS_EXAG)
    if L.NUTRIENT_FUNC.search(text):
        return Finding(c, "ok", "R6", "영양 기능 표시", c.target, 0.0, "영양성분 기능 표시 ('~에 필요')는 일반식품도 쓸 수 있어요")

    if category == "health":
        if c.level >= 4:
            return Finding(c, "ban", "H1", "금지 조합", _title(c), 0.85, "기능성을 질병 치료 수준으로 표현 · 시행령 별표1 제1호")
        if c.level == 3:
            return Finding(c, "review", "H2", "문맥 확인", _title(c), 0.60,
                           "기능성에 '예방'을 붙였어요. 질병 발생 위험 감소 기능성으로 인정된 문구인지 봐야 해요.", reason="func_only")
        product = ctx.get("product")
        if product:
            approved = re.sub(r"\s+", "", product.get("functions", ""))
            words = [w for w in re.split(r"\s+", c.target) if len(w) >= 2]
            if approved and not (re.sub(r"\s+", "", c.target) in approved or any(w in approved for w in words)):
                return Finding(c, "review", "H3", "인정 밖 기능", c.target, 0.50,
                               f"이 제품({product['name']})이 인정받은 기능성에 없는 내용이에요 (식품안전나라 C003)", reason="unapproved_func")
            return Finding(c, "ok", "H0", "인정 기능성", c.target, 0.0, "이 제품이 인정받은 기능성 범위예요")
        return Finding(c, "ok", "H0", "인정 기능성", c.target, 0.05, "제품의 인정 기능성은 확인하지 못했어요")

    # 일반식품 (모름 포함)
    if L.EXCLUDED_FUNC.search(text) and labeled:
        return Finding(c, "ban", "L3", "기능성 표현", c.target, 0.80, BASIS_EXCLUDED)
    if c.form == "ingredient":
        if labeled:
            return Finding(c, "ok", "L1", "기능성표시식품 문형", c.target, 0.0,
                           "원료의 기능성을 밝히는 문형이고 '건강기능식품이 아닙니다' 문구도 있어요 (식약처 고시 제6조)")
        return Finding(c, "review", "L4", "필수 문구 없음", c.target, 0.50, BASIS_DISCLAIMER, reason="no_disclaimer")
    if labeled:
        return Finding(c, "review", "L2", "문맥 확인", _title(c), 0.50, BASIS_LABELED, reason="labeled_product_form")
    if c.level >= 1:
        return Finding(c, "ban", "R3", "기능성 표현", _title(c), 0.85, BASIS_FUNC)
    return Finding(c, "review", "R3", "문맥 확인", c.target, 0.40,
                   "몸의 기능(관절, 혈당 등)을 언급했어요. 일반식품이 제품의 기능처럼 내세우면 안 돼요 (시행령 별표1 제3호, 확인 필요).",
                   reason="func_only")


def _overlap(a, b) -> bool:
    return any(x[0] < y[1] and y[0] < x[1] for x in a for y in b)


def judge_all(claims: list, category: str, ctx: dict) -> list[Finding]:
    """모든 주장을 판정하고, 더 센 주장이 이미 덮은 사례 표현은 한 번만 보여 준다."""
    cats = ("general", "health") if category == "unknown" else (category,)
    main = "general" if category == "unknown" else category
    findings = [judge(c, main, ctx) for c in claims]
    if category == "unknown":
        for f in findings:
            alt = judge(f.claim, "health", ctx)
            f.if_health = f.level in ("ban", "review") and alt.level in ("ok", "exempt")
    out = []
    for f in findings:
        if f.claim.kind == "case":
            if any(g is not f and g.claim.kind != "case" and g.claim.sent == f.claim.sent
                   and _overlap(g.claim.ranges, f.claim.ranges)
                   and (g.level in ("ok", "exempt") or g.score >= f.score) for g in findings):
                continue
            if f.level == "ok":
                continue
        out.append(f)
    return out


def risk(findings: list[Finding]) -> dict:
    live = [f for f in findings if f.level in ("ban", "review", "ok") and f.score > 0]
    # '관절, 근육, 뼈 케어'처럼 한 문장에서 같은 규칙이 여러 대상에 걸리면 한 번만 센다 (점수 부풀림 방지)
    best = {}
    for f in live:
        k = (f.claim.sent, f.rule)
        if k not in best or f.score > best[k].score:
            best[k] = f
    live = list(best.values())
    prod = 1.0
    for f in live:
        prod *= 1 - f.score
    r = 100 * (1 - prod)
    if any(f.level == "ban" for f in findings):
        r = max(r, 60.0)
    else:
        r = min(r, 59.0)
    score = int(round(r))
    band = next(name for lo, name in BANDS if score >= lo)
    parts = [{"title": f.title, "rule": f.rule, "level": f.level, "score": int(round(f.score * 100)),
              "sentence": f.claim.evidence} for f in sorted(live, key=lambda x: -x.score)]
    return {"score": score, "band": band, "parts": parts}
