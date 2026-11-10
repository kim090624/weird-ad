"""
규칙으로 문맥 판단하기 — AI 없이 "문맥 확인" 표현을 판정한다. (초안 — 팀 검토 필요)

판단엔진이 "문맥 확인"으로 남긴 문장(전문가만, 전문가+사용, 질병명만, 기능성 대상만)을
아래 패턴으로 다시 본다. AI 키가 없거나 AI 호출이 실패했을 때 judge_engine 이 대신 부른다.

결과는 문장마다 셋 중 하나
  misleading=True   의심 신호가 있다 (예: "당뇨 걱정되시죠?")
  misleading=False  허용 신호가 있다 (예: "약사가 개발한", "섭취 전 의사와 상담하세요")
  misleading=None   어느 쪽 신호도 없다 -> 판단 어려움 (사람이 확인)
의심 신호가 허용 신호보다 우선한다 ("약사가 개발하고 직접 추천" -> 의심).

패턴을 고치면 tests/test_context_rules.py 를 같이 고친다.
"""

from __future__ import annotations

import re

# 이유(reason)별 패턴. (정규식, 사람이 읽는 설명)
RULES: dict[str, dict[str, list[tuple[str, str]]]] = {
    # 전문가가 언급됐지만 추천동사는 없음
    "expert_only": {
        "suspicious": [
            (r"권(하|해|장|유)", "전문가가 권한다는 표현"),
            (r"처방", "처방한다는 표현"),
            (r"(직접|매일|꾸준히)\s*(먹|드시|마시|챙겨)", "전문가가 직접 먹는다는 표현"),
            (r"(믿고|인정한|선택한|극찬|강력|애용|만족)", "전문가의 보증처럼 보이는 표현"),
        ],
        "allowed": [
            (r"(개발|연구|설계|제조|참여|연구진|출신)", "연구·개발 참여 사실을 밝힌 표현"),
        ],
    },
    # 전문가 + '사용'
    "expert_use": {
        "suspicious": [
            (r"(가|이|들이|님이|도|께서)\s*(직접\s*)?(사용|애용|쓰는|쓴다)", "전문가가 제품을 사용한다는 표현"),
        ],
        "allowed": [
            (r"사용\s*(전|시|방법|법|량|후)", "사용 방법·주의 안내"),
            (r"(와|과)\s*상담\s*(후|하)", "전문가와 상담하라는 안내"),
        ],
    },
    # 질병명은 있지만 효능동사는 없음
    "disease_only": {
        "suspicious": [
            (r"(걱정|고민|근심)", "질병 걱정을 건드리는 표현"),
            (r"(에|엔)\s*(좋|효과|도움|특효|딱)", "질병에 좋다는 표현"),
            (r"(잡는|잡아|끝|탈출|해결|극복|이기는|막아|없애|싹|안녕|대신)", "질병을 없앤다는 뜻의 표현"),
            (r"(있는|있으신)\s*분", "질병이 있는 사람을 겨냥한 표현"),
            (r"(이라면|라면)\s", "질병이 있는 사람을 겨냥한 표현"),
        ],
        "allowed": [
            (r"(상담|주의|섭취\s*전|복용\s*중|복용하는)", "섭취 전 주의·상담 안내"),
            (r"의약품이\s*아닙니다", "의약품이 아니라는 안내"),
        ],
    },
    # 건강기능식품이 식품안전나라(C003)에 등록된 기능성 밖의 효과를 말함 -> 그 자체로 의심
    "unapproved_func": {
        "suspicious": [(r".", "제품이 인정받은 기능성에 없는 효과를 말하는 표현")],
        "allowed": [],
    },
    # (일반식품) 기능성 대상만
    "func_only": {
        "suspicious": [
            (r"(걱정|고민)", "몸 기능 걱정을 건드리는 표현"),
            (r"(에|엔)\s*(좋|효과|딱|특화)", "몸 기능에 좋다는 표현"),
            (r"(위한|맞춤|전용|플랜|지킴|튼튼|쑥쑥|탈출|잡는)", "몸 기능을 겨냥한 제품처럼 보이는 표현"),
        ],
        "allowed": [
            # 영양소 기능 표시 (예: "칼슘은 뼈와 치아 형성에 필요")
            (r"(칼슘|단백질|비타민|마그네슘|아연|철|식이섬유)\S*\s*(은|는).*(형성|구성|필요)", "영양소의 일반적인 기능 설명"),
        ],
    },
}

def _ira(word: str) -> str:
    """받침이 있으면 '이라', 없으면 '라' (표현이라 / 안내라)"""
    c = word[-1]
    has_final = "가" <= c <= "힣" and (ord(c) - 0xAC00) % 28 != 0
    return word + ("이라" if has_final else "라")


SUMMARY_UNSURE = "규칙으로는 판단하기 어려운 문장이 있어요. 표시한 문장은 직접 확인해 주세요."


def judge_sentence(reason: str, text: str) -> dict:
    """한 문장 판정. {"misleading": True/False/None, "reason": "설명", "pattern": 걸린 정규식}"""
    rules = RULES.get(reason)
    if not rules:
        return {"misleading": None, "reason": "규칙이 없는 유형이라 판단하지 않았어요.", "pattern": None}
    for pat, why in rules["suspicious"]:
        if re.search(pat, text):
            return {"misleading": True, "reason": f"{why}이 있어서 오해 소지가 있어 보여요.", "pattern": pat}
    for pat, why in rules["allowed"]:
        if re.search(pat, text):
            return {"misleading": False, "reason": f"{_ira(why)} 문제없어 보여요.", "pattern": pat}
    return {"misleading": None, "reason": "의심 신호도 허용 신호도 없어서 규칙으로는 판단하기 어려워요.", "pattern": None}


def review(review_hits: list[dict]) -> dict:
    """
    AI(AIContextAnalyzer.review)와 같은 모양으로 돌려준다.
    {"misleading", "summary", "items": [{"n", "misleading", "reason"}], "source": "rules"}
    """
    items = []
    for i, h in enumerate(review_hits):
        r = judge_sentence(h.get("reason", ""), h["S"]["text"])
        items.append({"n": i + 1, "misleading": r["misleading"], "reason": r["reason"]})
    flagged = sum(1 for it in items if it["misleading"] is True)
    unsure = sum(1 for it in items if it["misleading"] is None)
    if flagged:
        summary = f"규칙으로 본 결과, {len(items)}문장 중 {flagged}문장에 오해 소지가 있어 보여요."
    elif unsure:
        summary = SUMMARY_UNSURE
    else:
        summary = "규칙으로 본 결과, 문맥 확인 문장은 문제없어 보여요."
    return {"misleading": bool(flagged), "summary": summary, "items": items, "source": "rules"}
