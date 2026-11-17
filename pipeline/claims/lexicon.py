"""
주장구조 엔진의 말뭉치 — 판정이 아니라 '찾는 데' 쓰는 목록과 패턴.

  질병명      팀 사전(질병명) + data/claims/diseases_extra.csv (초안, 정식 질병 분류로 교체 예정)
  기능성 대상  팀 사전 data/dictionary/functional_claims.csv (기능성 대상)
  금지 표현    팀 사전 1단계 금지어 (한약처방명, 의약품명칭 등)
  전문가·추천동사  팀 사전 1단계 조합(전문가+추천동사)
  사례 표현    data/claims/cases.csv — 식약처 적발 사례에서 뽑은 표현 62개 (우선 검토 31, 문맥 검토 31)
  강도·부정·경고·후기·과장  아래 정규식 (동사 목록이 곧 성능이라 평가 데이터로 보강한다)
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from pipeline.judge_engine import DEFAULT_DICT, Entry, load_dictionary

DATA = Path(__file__).resolve().parent.parent.parent / "data" / "claims"

# ---------------------------------------------------------------------------
# 강도: 같은 절 안의 동사로 정한다. 숫자가 클수록 센 주장.
# ---------------------------------------------------------------------------
INTENSITY = [
    (4, "치료", re.compile(r"완치|치료|나았|나아|낫(?:다|고|는|습|게|네|죠)|고친|고치|고쳐|사라|없애|없어지|퇴치|뿌리\s*뽑|해결|잡아|잡힙|잡는|잡으|끊어|뚫")),
    (3, "예방", re.compile(r"예방|막아|막는|막습|방지|걸리지\s*않|차단")),
    (2, "개선", re.compile(r"개선|회복|감소|낮춰|낮추|낮아|내려|줄여|줄어|떨어|올려|높여|높이|좋아지|강화|증진|증가|활성|조절|보호|해소|제거|배출|청소|억제|완화|해독")),
    (1, "도움", re.compile(r"도움|좋아요|좋은|좋다|좋습|챙기|관리|케어|지켜|튼튼|건강|유지|활력|든든")),
]

# 과장·보장 (팀 사전의 '과장 표현' + 숫자 기간·감량)
EXAG = re.compile(
    r"보장|100\s*%|백\s*퍼|확실|완벽|반드시|무조건|즉시|당장|단기간|기적|영구|평생|24\s*시간"
    r"|\d+\s*(?:일|주|달|개월)\s*(?:만에|안에)|\d+\s*(?:kg|킬로)\s*(?:감량|빠|뺄)|폭식\s*불가능"
)

# 부정: 치료 효과가 없다, 치료제가 아니다. 단 '걱정 없는'은 오히려 효능 주장이라 부정이 아니다.
NEG = re.compile(r"지\s*않|지\s*못|지\s*마|아닙니다|아니에요|아니며|아니고|아닌|없습니다|없어요|할\s*수\s*없|효과\s*없|효과가\s*없|못\s*합니다|아니다")
NOT_NEG = re.compile(r"(?:걱정|부담|염려|고민|문제|스트레스)\s*(?:이|가|도)?\s*(?:전혀\s*)?없")
# 경고·보도 문맥: 적발 사례를 전하는 글이라 제품 주장이 아니다
WARN = re.compile(r"적발|단속|처벌|위반|허위|과장\s*광고|부당\s*광고|속지|속으|소비자\s*피해|주의하세요|조심")
# 후기·체험담: 면제가 아니라 표시만 한다 (체험담도 광고 주장으로 본다)
REVIEW = re.compile(r"먹고|먹었더니|먹어\s*보|써\s*봤|썼더니|했더니|해\s*봤|드시고|복용\s*후|후기|체험|직접\s*먹")

# 약을 끊거나 대신한다 (제2호 의약품 오인, 건강 위해)
DRUG_REPLACE = re.compile(
    r"(?:처방\s*약|혈압약|당뇨약|혈당약|인슐린|스테로이드|주사|약)\s*(?:을|를|은|는|도)?\s*"
    r"(?:끊|안\s*먹|줄였|줄이|대신|필요\s*없|졸업|그만|버렸|중단)"
)

# 앞 문장의 대상을 이어받는 지시어 ("이걸로 잡으세요")
DEICTIC = re.compile(r"이걸|이거|이것|이게|이\s*제품|이\s*병|이\s*증상|그걸|그게|저걸")

# 일반식품이 원료의 기능성을 표시하는 문형 (식약처 고시 제6조제1항제1호)
INGREDIENT_FORM = re.compile(
    r"(?:들어\s*있|함유|포함|원료|성분|추출물).{0,40}(?:알려진|보고된)|(?:알려진|보고된).{0,40}(?:들어\s*있|함유|포함|원료|성분|추출물)"
)
# '본 제품은 건강기능식품이 아닙니다' (고시 제6조제1항제5호, 광고는 제3항)
DISCLAIMER = re.compile(r"건강기능식품이아닙니다|건강기능식품이아님|건강기능식품이아니")
# 기능성 표시식품에서 제외되는 기능성 (고시 제4조제2항: 성기능·생식기, 건강민감 계층)
EXCLUDED_FUNC = re.compile(r"성\s*기능|정력|발기|생식기|임산부|수유부|영유아")
# 영양성분 기능 표시 ("칼슘은 뼈와 치아 형성에 필요") — 일반식품도 쓸 수 있다
NUTRIENT_FUNC = re.compile(
    r"(?:칼슘|철분?|아연|마그네슘|단백질|식이섬유|비타민\s*[A-Za-z]\d*|요오드|셀레늄|구리)\s*(?:은|는|이|가)?\s*.{0,25}필요"
)

CLAUSE_SPLIT = re.compile(r"[,，;；]|\s+그리고\s+|\s+하지만\s+")
PREDICATE_END = re.compile(r"(?:요|다|죠|까|네|군|음|니다|세요)[.!?…\s]*$")


# ---------------------------------------------------------------------------
# 목록 읽기
# ---------------------------------------------------------------------------
_ASCII_WORD = re.compile(r"^[A-Za-z0-9-]+$")


def make_entry(word: str, cat: str, role: str, stage: str = "x", basis: str = "", source: str = "", note: str = "") -> Entry:
    chars = list(re.sub(r"\s+", "", word))
    e = Entry(word=word, cat=cat, stage=stage, role=role, basis=basis, source=source, note=note)
    e.pattern = re.compile(r"\s*".join(re.escape(ch) for ch in chars), re.IGNORECASE)
    e.ascii = bool(_ASCII_WORD.match(word))
    return e


@dataclass
class Lexicon:
    diseases: list
    functions: list
    bans: list
    experts: list
    expert_verbs: list
    cases: list
    case_meta: dict
    approved: list


def load_lexicon() -> Lexicon:
    team = load_dictionary(DEFAULT_DICT)
    diseases = [e for e in team if e.role == "disease"]
    names = {re.sub(r"\s+", "", e.word) for e in diseases}
    with (DATA / "diseases_extra.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            w = (row.get("표현") or "").strip()
            if w and re.sub(r"\s+", "", w) not in names:
                diseases.append(make_entry(w, "질병명", "disease", basis="시행령 별표1 제1호", source="초안 목록", note=row.get("비고", "")))
    cases, meta = [], {}
    with (DATA / "cases.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            e = make_entry(row["word"], row["kind"], "case", stage="case", basis=row["basis"], source=row["source"], note=row["id"])
            cases.append(e)
            meta[row["id"]] = row
    with (DATA / "approved_functions.csv").open(encoding="utf-8", newline="") as f:
        approved = list(csv.DictReader(f))
    functions = [e for e in team if e.role == "ftarget"]
    have = {re.sub(r"\s+", "", e.word) for e in functions}
    # 기능성표시식품에서 제외된 기능성(고시 제4조제2항)도 기능성 대상으로 찾는다
    for w in ("성기능", "정력", "발기", "생식기"):
        if w not in have:
            functions.append(make_entry(w, "기능성 대상", "ftarget", basis="식약처 고시 제4조제2항", source="초안 목록"))
    return Lexicon(
        diseases=diseases,
        functions=functions,
        bans=[e for e in team if e.stage == "ban"],
        experts=[e for e in team if e.role == "expert"],
        expert_verbs=[e for e in team if e.role == "verb"],
        cases=cases,
        case_meta=meta,
        approved=approved,
    )


# ---------------------------------------------------------------------------
# 글자 깨짐(OCR)에 대비한 자모 비교
# ---------------------------------------------------------------------------
_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"


def jamo(s: str) -> str:
    out = []
    for ch in s:
        if "가" <= ch <= "힣":
            c = ord(ch) - 0xAC00
            out.append(_CHO[c // 588])
            out.append(_JUNG[(c % 588) // 28])
            j = _JONG[c % 28]
            if j != " ":
                out.append(j)
        else:
            out.append(ch)
    return "".join(out)


def distance(a: str, b: str, limit: int = 2) -> int:
    """편집거리 (limit 을 넘으면 limit+1)"""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def fuzzy_ranges(text: str, word: str, max_dist: int = 1) -> list[list[int]]:
    """
    word 와 자모 거리가 max_dist 이하인 구간을 찾는다 (글자 수가 같은 구간만).
    word 가 3글자보다 짧으면 오탐이 많아서 찾지 않는다. 띄어쓰기는 무시하고 원래 문장의 위치로 돌려준다.
    """
    w = re.sub(r"\s+", "", word)
    if len(w) < 3 or not re.fullmatch(r"[가-힣]+", w):
        return []
    idx = [i for i, ch in enumerate(text) if not ch.isspace()]
    flat = "".join(text[i] for i in idx)
    wj = jamo(w)
    n = len(w)
    out = []
    for k in range(0, len(flat) - n + 1):
        seg = flat[k : k + n]
        if seg == w or not re.fullmatch(r"[가-힣]+", seg):
            continue
        if distance(jamo(seg), wj, max_dist) <= max_dist:
            out.append([idx[k], idx[k + n - 1] + 1])
    return out
