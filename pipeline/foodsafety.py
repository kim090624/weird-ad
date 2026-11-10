"""
식품안전나라 Open API (식품의약품안전처 공공데이터)

  C003    건강기능식품 품목제조 신고사항 (제품명 PRDLST_NM, 업소명 BSSH_NM, 주된기능성 PRIMARY_FNCLTY)
          -> 화면 글자에서 제품명을 찾아 건강기능식품인지 자동 확인, 그 제품이 인정받은 기능성 가져오기
  I2710   건강기능식품 기능성 원료 (고시형)     -> 식약처가 인정한 기능성 문구
  I-0050  건강기능식품 개별인정형 원료          -> 식약처가 인정한 기능성 문구

인증키: 환경변수 FOODSAFETY_API_KEY (식품안전나라 > 공공데이터 활용 > 인증키 신청, 무료)
        'sample' 키는 시험용이라 한 번에 5건까지만 준다.
주소 형식: http://openapi.foodsafetykorea.go.kr/api/{키}/{서비스}/json/{시작}/{끝}/{검색인자=값}

인터넷이나 키가 없으면 None/빈 목록을 돌려주고, 판단은 이 자료 없이 그대로 진행된다.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path

BASE = "http://openapi.foodsafetykorea.go.kr/api"
TIMEOUT = 8
SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "public" / "functional_phrases.json"
SOURCES = {"I2710": "건강기능식품 기능성 원료(고시형)", "I-0050": "건강기능식품 개별인정형 원료"}


def api_key() -> str | None:
    return os.environ.get("FOODSAFETY_API_KEY") or None


def _get(service: str, start: int, end: int, key: str | None = None, **params) -> tuple[list[dict], int]:
    key = key or api_key()
    if not key:
        return [], 0
    path = f"{BASE}/{urllib.parse.quote(key)}/{service}/json/{start}/{end}"
    for k, v in params.items():
        path += f"/{k}={urllib.parse.quote(str(v))}"
    with urllib.request.urlopen(path, timeout=TIMEOUT) as r:
        data = json.loads(r.read().decode("utf-8"))
    body = data.get(service) or next(iter(data.values()), {})
    code = (body.get("RESULT") or {}).get("CODE", "")
    if code not in ("INFO-000", "INFO-200"):  # INFO-200: 결과 없음
        raise RuntimeError(f"{service} {code} {(body.get('RESULT') or {}).get('MSG', '')}")
    return body.get("row") or [], int(body.get("total_count") or 0)


# ---------------------------------------------------------------------------
# 1. 제품 찾기 (C003)
# ---------------------------------------------------------------------------

def _clean(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣 ]", " ", s).strip()


def _nospace(s: str) -> str:
    return re.sub(r"\s+", "", s)


@lru_cache(maxsize=256)
def search_products(name: str) -> tuple:
    """제품명에 name이 들어간 건강기능식품 (최대 20건)"""
    rows, _ = _get("C003", 1, 20, PRDLST_NM=name)
    return tuple({"name": r.get("PRDLST_NM", ""), "company": r.get("BSSH_NM", ""),
                  "report_no": r.get("PRDLST_REPORT_NO", ""), "functions": r.get("PRIMARY_FNCLTY", "")}
                 for r in rows)


# 제품명이 아닌데 화면에 흔히 나오는 글자
STOPWORDS = {"건강기능식품", "식품의약품안전처", "우수건강기능식품제조기준", "일반식품", "영양성분", "원재료명"}


def candidate_names(lines: list[str], limit: int = 4) -> list[str]:
    """화면 글자 줄 중 제품명일 만한 것: 한글 2글자 이상, 짧은 줄 (광고 문구보다 제품명이 짧다)"""
    seen, out = set(), []
    for ln in lines:
        for part in re.split(r"[/|,·]", ln):
            c = _clean(part)
            if not (2 <= len(_nospace(c)) <= 15) or len(re.findall(r"[가-힣]", c)) < 2:
                continue
            if _nospace(c) not in seen and _nospace(c) not in STOPWORDS:
                seen.add(_nospace(c))
                out.append(c)
    out.sort(key=lambda c: -len(_nospace(c)))  # 긴 것이 덜 흔하다
    return out[:limit]


def match_product(lines: list[str], search=None) -> dict | None:
    """
    화면 글자에서 등록된 건강기능식품을 찾는다. 잘못 맞히지 않게 엄격하게 본다:
    제품명 안에 화면 글자 조각이 2개 이상 들어 있거나, 화면 글자 하나가 제품명과 똑같아야 한다.
    """
    search = search or search_products
    cands = candidate_names(lines)
    if not cands:
        return None
    tokens = {_nospace(c) for c in candidate_names(lines, limit=30)}
    best, best_score = None, 0
    for c in cands:
        try:
            products = search(c)
        except Exception:
            continue
        for p in products:
            pn = _nospace(p["name"])
            score = sum(1 for t in tokens if len(t) >= 2 and t in pn)
            exact = any(t == pn for t in tokens)
            if exact:
                score += 10
            if score > best_score or (score == best_score and best and len(pn) < len(_nospace(best["name"]))):
                best, best_score = p, score
    if best and best_score >= 2:
        return dict(best, matched_by=f"화면 글자 {min(best_score, 10)}개 일치" if best_score < 10 else "제품명 일치")
    return None


# ---------------------------------------------------------------------------
# 2. 식약처가 인정한 기능성 문구 (I2710, I-0050)
# ---------------------------------------------------------------------------

_KEY = re.compile(r"^(.*?)(?:하는\s*데|는\s*데|데|에)\s*도움")


def phrases_from_text(text: str) -> list[str]:
    """'①관절 및 연골 건강에 도움을 줄 수 있음\\n(영문) ...' -> ['관절 및 연골 건강에 도움을 줄 수 있음']"""
    text = re.sub(r"\(영문\).*", "", text)
    parts = re.split(r"[\n\r]+|[①-⑳]|\(\d+\)|\(국문\)", text)
    out = []
    for p in parts:
        p = re.sub(r"\((기타기능|생리활성기능)[^)]*\)", "", p).strip(" .\"“”")
        if "도움" in p and 6 <= len(p) <= 120:
            out.append(p)
    return out


def keys_from_phrase(phrase: str) -> list[str]:
    """'관절 및 연골 건강에 도움을 줄 수 있음' -> ['관절 및 연골 건강'] ; '·'로 묶인 것은 나눈다"""
    m = _KEY.match(phrase)
    if not m:
        return []
    keys = []
    for part in re.split(r"[·ㆍ,]", m.group(1)):
        # '...억제하여 체지방 감소', '...통한 혈액흐름' -> 마지막 덩어리만
        part = re.split(r"(?:하여|해서|통한|통해|으로써|로써)\s+", part.strip())[-1]
        # 'PMO 참밀알부민은 급격한 ...' -> '급격한 ...' (원료 이름 주어 떼기). '합성되는' 같은 동사 끝은 건드리지 않는다
        m2 = re.match(r"^(.{1,30}?)([\uAC00-\uD7A3])(은|는)\s+(.+)$", part)
        if m2 and m2.group(2) not in "하되있없된한" and len(_nospace(m2.group(4))) >= 4:
            part = m2.group(4)
        if len(_nospace(part)) >= 4:
            keys.append(part.strip())
    return keys


def fetch_functional_phrases(key: str | None = None) -> list[dict]:
    """I2710 + I-0050 전체를 받아 기능성 문구 목록으로 만든다 (1000건씩)"""
    out, seen = [], set()
    for svc in ("I2710", "I-0050"):
        start = 1
        while True:
            rows, total = _get(svc, start, start + 999, key=key)
            for r in rows:
                ingredient = r.get("PRDCT_NM") or r.get("RAWMTRL_NM") or ""
                for ph in phrases_from_text(r.get("PRIMARY_FNCLTY", "")):
                    for k in keys_from_phrase(ph):
                        nk = _nospace(k)
                        if nk in seen:
                            continue
                        seen.add(nk)
                        out.append({"key": k, "phrase": ph, "ingredient": ingredient, "source": svc})
            start += 1000
            if not rows or start > total or (key or api_key()) == "sample":
                break
    return out


@lru_cache(maxsize=1)
def functional_phrases() -> tuple:
    """저장해 둔 목록(data/public)을 먼저 쓰고, 없으면 키로 받아 온다. 둘 다 안 되면 빈 목록."""
    if SNAPSHOT.exists():
        try:
            return tuple(json.loads(SNAPSHOT.read_text(encoding="utf-8"))["phrases"])
        except Exception:
            pass
    if api_key():
        try:
            return tuple(fetch_functional_phrases())
        except Exception:
            return ()
    return ()
