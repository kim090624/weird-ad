"""
① 주장 추출 — 문장을 '대상 + 강도 + 맥락' 칸으로 쪼갠다. 판정은 하지 않는다 (판정은 rules.py).

  대상(kind)   disease 질병명 / function 기능성 / ban 금지 표현 / case 적발 사례 표현
               expert 전문가 추천 / drug_replace 약 끊기·대신
  강도(level)  4 치료 / 3 예방 / 2 개선 / 1 도움 / 0 없음
  맥락(context) direct 직접 주장 / review 후기 / negated 부정 / warning 경고·보도
               (negated, warning 은 판정에서 면제)
  확신(conf)   1.0 글자 그대로 / 0.85 OCR 보정(자모 비교) / 0.75 앞 문장에서 이어받음
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pipeline.claims import lexicon as L
from pipeline.judge_engine import find_ranges, split_sentences


@dataclass
class Claim:
    sent: int
    kind: str
    target: str
    ranges: list
    level: int = 0
    intensity: str = ""
    intensity_word: str = ""
    context: str = "direct"
    exag: str = ""
    conf: float = 1.0
    via: str = "exact"
    form: str = ""          # function 일 때: ingredient(원료 문형) / product(완제품 문형)
    entry: object = field(default=None, repr=False)
    sub: str = ""           # expert: strong / use / only,  case: 우선 검토 / 문맥 검토
    evidence: str = ""


def sentences_from(segments: list[dict]) -> list[dict]:
    out, seen = [], set()
    for seg in segments:
        for s in split_sentences(seg.get("text", "")):
            key = re.sub(r"\s+", "", s)
            if key in seen:
                continue
            seen.add(key)
            out.append({"text": s, "t": seg.get("t"), "thumb": seg.get("thumb"), "src": seg.get("src", "text")})
    return out


def _clauses(text: str) -> list[tuple[int, int]]:
    spans, pos = [], 0
    for m in L.CLAUSE_SPLIT.finditer(text):
        spans.append((pos, m.start()))
        pos = m.end()
    spans.append((pos, len(text)))
    return [(s, e) for s, e in spans if text[s:e].strip()]


def _mask(text: str, ranges: list) -> str:
    chars = list(text)
    for a, b in ranges:
        for i in range(max(a, 0), min(b, len(chars))):
            chars[i] = " "
    return "".join(chars)


def _intensity(text: str):
    """가장 센 강도 (level, name, word, 끝 위치)"""
    best = (0, "", "", -1)
    for lv, name, pat in L.INTENSITY:
        m = None
        for m in pat.finditer(text):
            pass
        if m and lv > best[0]:
            best = (lv, name, m.group(0), m.end())
    return best


def _inside(r, others) -> bool:
    return any(o[0] <= r[0] and r[1] <= o[1] for o in others)


def _targets(text: str, lex: L.Lexicon) -> list[dict]:
    """한 문장에서 질병·기능성·금지 표현을 찾는다. 긴 낱말이 짧은 낱말을 덮는다."""
    cand = []
    groups = (("disease", lex.diseases), ("function", lex.functions), ("ban", lex.bans))
    for kind, entries in groups:
        for e in entries:
            for a, b in find_ranges(text, e):
                cand.append({"kind": kind, "entry": e, "r": [a, b], "conf": 1.0, "via": "exact"})
    cand.sort(key=lambda t: -(t["r"][1] - t["r"][0]))
    acc = []
    for t in cand:
        if not _inside(t["r"], [a["r"] for a in acc]):
            acc.append(t)
    # 글자가 깨졌을 때를 대비해 자모가 비슷한 것도 찾는다 (질병·기능성만, 3글자 이상)
    for kind, entries in groups[:2]:
        for e in entries:
            if any(a["entry"] is e for a in acc):
                continue
            for a, b in L.fuzzy_ranges(text, e.word):
                if not any(a_ < b and a < b_ for a_, b_ in (x["r"] for x in acc)):
                    acc.append({"kind": kind, "entry": e, "r": [a, b], "conf": 0.85, "via": "fuzzy"})
    acc.sort(key=lambda t: t["r"][0])
    return acc


def _context(sentence: str, clause: str, after: int) -> str:
    """after: 절 안에서 '주장 낱말'이 끝나는 위치. 그 뒤에 부정어가 있으면 부정."""
    if L.WARN.search(sentence):
        return "warning"
    tail = clause[after:] if after >= 0 else clause
    for m in L.NEG.finditer(tail):
        window = clause[max(0, after + m.start() - 8): after + m.end()] if after >= 0 else tail
        if not L.NOT_NEG.search(window):
            return "negated"
    if L.REVIEW.search(sentence):
        return "review"
    return "direct"


def extract(sentences: list[dict], lex: L.Lexicon) -> list[Claim]:
    claims: list[Claim] = []
    prev_targets: list[Claim] = []
    for si, S in enumerate(sentences):
        text = S["text"]
        clauses = _clauses(text)
        targets = _targets(text, lex)
        all_ranges = [t["r"] for t in targets]
        sent_claims: list[Claim] = []

        def clause_of(pos):
            for ci, (a, b) in enumerate(clauses):
                if a <= pos < b or pos == b:
                    return ci
            return len(clauses) - 1

        for t in targets:
            ci = clause_of(t["r"][0])
            a, b = clauses[ci]
            scan = [ci]
            masked = _mask(text, all_ranges)[a:b]
            lv, name, word, end = _intensity(masked)
            if lv == 0:
                # '관절연골, 근육, 뼈 동시케어' — 서술어 없는 짧은 조각은 뒤 절의 동사를 빌린다
                k = ci
                while k < len(clauses) - 1 and not L.PREDICATE_END.search(text[clauses[k][0]:clauses[k][1]].strip()):
                    k += 1
                    sa, sb = clauses[k]
                    lv2, name2, word2, end2 = _intensity(_mask(text, all_ranges)[sa:sb])
                    if lv2:
                        lv, name, word, end, scan = lv2, name2, word2, end2, [ci, k]
                        break
            sa, sb = clauses[scan[-1]]
            clause_text = text[sa:sb]
            exag = ""
            for k in scan:
                m = L.EXAG.search(text[clauses[k][0]:clauses[k][1]])
                if m:
                    exag = m.group(0)
            after = end if lv else (t["r"][1] - sa if scan[-1] == ci else -1)
            ctx = _context(text, clause_text, after)
            c = Claim(sent=si, kind=t["kind"], target=t["entry"].word, ranges=[t["r"]], level=lv, intensity=name,
                      intensity_word=word, context=ctx, exag=exag, conf=t["conf"], via=t["via"], entry=t["entry"],
                      evidence=text)
            if t["kind"] == "function":
                c.form = "ingredient" if L.INGREDIENT_FORM.search(text) else "product"
            sent_claims.append(c)

        # 약 끊기·대신
        for m in L.DRUG_REPLACE.finditer(text):
            ctx = _context(text, text, m.end())
            sent_claims.append(Claim(sent=si, kind="drug_replace", target=m.group(0), ranges=[[m.start(), m.end()]],
                                     context=ctx, evidence=text))

        # 전문가 추천
        exp = _longest(text, lex.experts)
        verbs = _longest(text, lex.expert_verbs)
        if exp:
            strong = [v for v in verbs if v[0].word != "사용"]
            sub = "strong" if strong else ("use" if verbs else "only")
            rng = [r for _, r in exp] + ([r for _, r in strong] if strong else [r for _, r in verbs])
            title = exp[0][0].word + (f" + {strong[0][0].word}" if strong else (" + 사용" if verbs else ""))
            ctx = _context(text, text, max(r[1] for r in rng))
            sent_claims.append(Claim(sent=si, kind="expert", target=title, ranges=rng, context=ctx, sub=sub,
                                     entry=exp[0][0], evidence=text))

        # 적발 사례 표현
        for e in lex.cases:
            hits = [(r, 1.0, "exact") for r in find_ranges(text, e)]
            if not hits:
                hits = [(r, 0.85, "fuzzy") for r in L.fuzzy_ranges(text, e.word)]
            for r, conf, via in hits:
                ci = clause_of(r[0])
                clause_text = text[clauses[ci][0]:clauses[ci][1]]
                ctx = _context(text, clause_text, r[1] - clauses[ci][0])
                sent_claims.append(Claim(sent=si, kind="case", target=e.word, ranges=[r], context=ctx, conf=conf, via=via,
                                         entry=e, sub=lex.case_meta[e.note]["grade"], evidence=text))

        # 대명사: 앞 문장 대상을 이어받는다 ("당뇨로 고생하셨죠?" -> "이걸로 잡으세요")
        own = [c for c in sent_claims if c.kind in ("disease", "function")]
        if not own and L.DEICTIC.search(text) and prev_targets:
            lv, name, word, end = _intensity(text)
            if lv >= 2:
                base = sorted(prev_targets, key=lambda c: (c.kind != "disease", -c.level))[0]
                m = None
                for m in L.INTENSITY[[x[1] for x in L.INTENSITY].index(name)][2].finditer(text):
                    pass
                ctx = _context(text, text, end)
                sent_claims.append(Claim(sent=si, kind=base.kind, target=base.target, ranges=[[m.start(), m.end()]] if m else [],
                                         level=lv, intensity=name, intensity_word=word, context=ctx, conf=0.75, via="carry",
                                         entry=base.entry, form=base.form, evidence=text))
        claims.extend(sent_claims)
        prev_targets = [c for c in sent_claims if c.kind in ("disease", "function") and c.context in ("direct", "review")]
    return claims


def _longest(text: str, entries: list) -> list:
    xs = []
    for e in entries:
        for r in find_ranges(text, e):
            xs.append((e, r))
    xs.sort(key=lambda x: -(x[1][1] - x[1][0]))
    out = []
    for e, r in xs:
        if not _inside(r, [o[1] for o in out]):
            out.append((e, r))
    return out
