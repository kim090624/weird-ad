"""
판단엔진 — 팀 금지어 사전(data/dictionary/geumjieo_list.csv) 규칙을 그대로 적용한다.

웹 버전(web/src/index.html 의 analyze)과 같은 규칙, 같은 결과를 낸다.
두 버전이 같은지는 tests/engine_cases.json 으로 확인한다.

규칙 (사전의 '사용단계' 열)
  1단계 금지어                    -> 나오면 바로 금지 (ban)
  1단계 조합(전문가+추천동사)      -> 한 문장에 둘 다 있으면 금지 조합 (ban)
  1단계 조합(질병명+효능동사)      -> 한 문장에 둘 다 있으면 금지 조합 (ban)
  전문가만 / 질병명만 / 전문가+'사용' -> 문맥 확인 (review). 이것만 AI에게 묻는다.

AI(Claude API)는 문맥 확인 단계에서만 쓴다.
  - 금지 표현이 하나라도 있으면 AI를 부르지 않는다.
  - ANTHROPIC_API_KEY 가 없으면 문맥 확인 대상은 "확인 필요"로 남기고 끝낸다.
"""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DICT = Path(__file__).resolve().parent.parent / "data" / "dictionary" / "geumjieo_list.csv"

STAGE_MAP = {
    "1단계 금지어": "ban",
    "1단계 조합(전문가+추천동사)": "combo_expert",
    "1단계 조합(질병명+효능동사)": "combo_disease",
}
ROLE_MAP = {"전문가": "expert", "추천동사": "verb", "질병명": "disease", "효능동사": "effect"}

# 사전 밖에서 더한 오탐 예외. '공신력'(신뢰)이 '공신'(공신단 계열 부분일치)으로 걸리지 않게 한다.
EXCEPTIONS = {"공신": ["공신력"]}
# '암'은 한 글자라 앞 글자가 한글이면 다른 낱말로 보고 뺀다. 뒤에는 조사·합성어만 허용.
AM_NEXT = set("이을은의도에과와만세환까")

REVIEW_REASON = {
    "expert_only": "전문가가 언급됐어요. 추천·보증이 아니라 연구·개발 참여 사실만 밝힌 거라면 괜찮아요 (시행령 별표1 제5호라목).",
    "expert_use": "전문가와 '사용'이 함께 나왔어요. '사용'은 다른 뜻으로도 많이 써서 문맥을 봐야 해요 (시행령 별표1 제5호라목).",
    "disease_only": "질병 이름이 나왔어요. 질병을 예방·치료한다고 받아들일 수 있는지 봐야 해요 (시행령 별표1 제1호).",
}

_HANGUL = re.compile(r"[가-힣]")
_LATIN = re.compile(r"[A-Za-z]")
_ASCII_WORD = re.compile(r"^[A-Za-z0-9-]+$")


def _is_hangul(c: str | None) -> bool:
    return bool(c) and bool(_HANGUL.match(c))


def _is_latin(c: str | None) -> bool:
    return bool(c) and bool(_LATIN.match(c))


@dataclass
class Entry:
    word: str
    cat: str
    stage: str
    role: str
    basis: str
    source: str
    note: str
    pattern: re.Pattern = field(repr=False, default=None)
    ascii: bool = False


def load_dictionary(path: str | Path = DEFAULT_DICT) -> list[Entry]:
    entries = []
    with Path(path).open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            word = (row.get("표현") or "").strip()
            stage = STAGE_MAP.get((row.get("사용단계") or "").strip())
            # '확인 필요' 행(예: "보아탕 유사명칭")은 단어가 아니라 메모라서 검사에 쓰지 않는다
            if not word or not stage:
                continue
            cat = (row.get("분류") or "").strip()
            chars = list(re.sub(r"\s+", "", word))
            e = Entry(
                word=word,
                cat=cat,
                stage=stage,
                role=ROLE_MAP.get(cat, ""),
                basis=(row.get("근거조항") or "").strip(),
                source=(row.get("출처") or "").strip(),
                note=(row.get("비고") or "").strip(),
            )
            # 글자 사이 띄어쓰기는 무시한다 ("MSG 무첨가" == "MSG무첨가")
            e.pattern = re.compile(r"\s*".join(re.escape(ch) for ch in chars), re.IGNORECASE)
            e.ascii = bool(_ASCII_WORD.match(word))
            entries.append(e)
    return entries


def find_ranges(text: str, e: Entry) -> list[list[int]]:
    out = []
    for m in e.pattern.finditer(text):
        s, en = m.start(), m.end()
        if s == en:
            continue
        prev = text[s - 1] if s > 0 else None
        nxt = text[en] if en < len(text) else None
        if e.ascii and (_is_latin(prev) or _is_latin(nxt)):
            continue
        if e.word == "암" and (_is_hangul(prev) or (_is_hangul(nxt) and nxt not in AM_NEXT)):
            continue
        ex = EXCEPTIONS.get(e.word)
        if ex and any(re.sub(r"\s+", "", text[s : s + len(x) + 4]).startswith(x) for x in ex):
            continue
        out.append([s, en])
    return out


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"\n+|(?<=[.!?。…])\s+", text)
    return [p.strip() for p in parts if p and p.strip()]


def analyze(segments: list[dict], entries: list[Entry]) -> dict:
    """
    segments: [{"text": str, "t": 초 또는 None, "thumb": dataURL 또는 None, "src": "screen"|"voice"|"text"}]
    반환: {"sentences": [...], "hits": [...]}  (웹 버전 analyze 와 같은 모양)
    """
    sentences = []
    seen = set()
    for seg in segments:
        for s in split_sentences(seg.get("text", "")):
            key = re.sub(r"\s+", "", s)
            if key in seen:
                continue
            seen.add(key)
            sentences.append({"text": s, "t": seg.get("t"), "thumb": seg.get("thumb"), "src": seg.get("src", "text")})

    hits = []
    for S in sentences:
        found = []
        for e in entries:
            r = find_ranges(S["text"], e)
            if r:
                found.append((e, r))

        def pick(stage, role=None):
            # 긴 낱말을 앞에 둔다: '한의사'가 '의사'보다 먼저 제목이 되게
            xs = [(e, r) for e, r in found if e.stage == stage and (role is None or e.role == role)]
            return sorted(xs, key=lambda x: -len(x[0].word))

        def ranges(xs):
            return [rg for _, r in xs for rg in r]

        def inside(r, others):
            return all(any(o[0] <= a and b <= o[1] for o in others) for a, b in r)

        bans = pick("ban")
        # 더 긴 금지어 안에 통째로 들어가는 짧은 금지어는 한 번만 보여 준다 ('공진단' 안의 '공진')
        bans = [(e, r) for i, (e, r) in enumerate(bans)
                if not any(j < i and len(f.word) > len(e.word) and inside(r, q) for j, (f, q) in enumerate(bans))]
        for e, r in bans:
            hits.append({"level": "ban", "tag": "금지 표현", "title": e.word, "S": S, "ranges": r,
                         "basis": f"{e.cat} · {e.basis}"})

        expert, verb = pick("combo_expert", "expert"), pick("combo_expert", "verb")
        strong_verb = [(e, r) for e, r in verb if e.word != "사용"]
        if expert and strong_verb:
            hits.append({"level": "ban", "tag": "금지 조합", "title": f"{expert[0][0].word} + {strong_verb[0][0].word}",
                         "S": S, "ranges": ranges(expert + strong_verb),
                         "basis": f"전문가 추천·보증 · {expert[0][0].basis}"})
        elif expert and verb:
            hits.append({"level": "review", "reason": "expert_use", "tag": "문맥 확인",
                         "title": f"{expert[0][0].word} + 사용", "S": S, "ranges": ranges(expert + verb),
                         "basis": REVIEW_REASON["expert_use"]})
        elif expert:
            hits.append({"level": "review", "reason": "expert_only", "tag": "문맥 확인", "title": expert[0][0].word,
                         "S": S, "ranges": ranges(expert), "basis": REVIEW_REASON["expert_only"]})

        disease, effect = pick("combo_disease", "disease"), pick("combo_disease", "effect")
        if disease and effect:
            hits.append({"level": "ban", "tag": "금지 조합", "title": f"{disease[0][0].word} + {effect[0][0].word}",
                         "S": S, "ranges": ranges(disease + effect),
                         "basis": f"질병 예방·치료 표방 · {disease[0][0].basis}"})
        elif disease:
            hits.append({"level": "review", "reason": "disease_only", "tag": "문맥 확인", "title": disease[0][0].word,
                         "S": S, "ranges": ranges(disease), "basis": REVIEW_REASON["disease_only"]})

    return {"sentences": sentences, "hits": hits}


# ---------------------------------------------------------------------------
# AI 문맥 확인 (문맥 확인 대상이 있고 금지 표현이 없을 때만)
# ---------------------------------------------------------------------------

class AIUnavailable(Exception):
    """API 키나 패키지가 없어서 AI를 부를 수 없을 때. code 로 이유를 구분한다."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def review_prompt(review_hits: list[dict], sentences: list[dict]) -> str:
    context = "\n".join(s["text"] for s in sentences)[:3000]
    items = "\n".join(f"{i + 1}. {h['S']['text']}\n   걸린 이유: {h['basis']}" for i, h in enumerate(review_hits))
    return (
        "너는 식품 등의 표시·광고에 관한 법률 기준으로 광고 문구를 검토하는 보조자다. 법률 판단을 단정하지 말고 '~로 보여요' 말투로 쓴다.\n"
        "기준:\n"
        "- 질병의 예방·치료에 효능이 있는 것으로 인식할 우려가 있는 표시·광고는 금지된다 (시행령 별표1 제1호).\n"
        "- 의사·치과의사·한의사·수의사·약사·한약사·대학교수 등이 제품을 지정·공인·추천·지도·사용한다는 광고는 금지된다. "
        "연구·개발에 참여했다는 사실만 밝히는 것은 허용된다 (시행령 별표1 제5호라목).\n\n"
        f"광고에서 읽은 전체 글자(앞뒤 문맥):\n\"\"\"\n{context}\n\"\"\"\n\n"
        f"검토할 문장:\n{items}\n\n"
        "각 문장이 소비자를 오인하게 할 소지가 있는지 판단하라. JSON 객체 하나로만 답하라:\n"
        '{"misleading": true 또는 false, "summary": "전체 판단 2문장", "items": [{"n": 1, "misleading": true 또는 false, "reason": "한 문장"}]}'
    )


def _parse_json(text: str):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start > 0:
        text = text[start:]
    return json.loads(text)


class AIContextAnalyzer:
    """Claude API로 문맥 확인. 환경변수 ANTHROPIC_API_KEY, (선택) ANTHROPIC_MODEL."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        # 문장 몇 개를 보는 일이라 가벼운 모델로 충분하다. 바꾸려면 ANTHROPIC_MODEL 환경변수.
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")

    def available(self) -> bool:
        return bool(self.api_key)

    def review(self, review_hits: list[dict], sentences: list[dict]) -> dict:
        if not self.api_key:
            raise AIUnavailable("no_api_key")
        try:
            import anthropic
        except ImportError as e:
            raise AIUnavailable("no_sdk", "anthropic 패키지가 없습니다 (pip install anthropic)") from e
        client = anthropic.Anthropic(api_key=self.api_key)
        resp = client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": review_prompt(review_hits, sentences)}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        try:
            data = _parse_json(raw)
        except (ValueError, json.JSONDecodeError) as e:
            raise AIUnavailable("invalid_json", raw[:300]) from e
        if not isinstance(data, dict):
            raise AIUnavailable("invalid_json", raw[:300])
        return data


# ---------------------------------------------------------------------------
# 전체 판정
# ---------------------------------------------------------------------------

class JudgeEngine:
    def __init__(self, dictionary_path: str | Path = DEFAULT_DICT, ai: AIContextAnalyzer | None = None):
        self.entries = load_dictionary(dictionary_path)
        self.ai = ai if ai is not None else AIContextAnalyzer()

    def run(self, segments: list[dict]):
        """
        단계마다 진행 이벤트를 내보내고 마지막에 결과를 내보내는 생성기.
          {"type": "step", "step": "dict"|"ai", "state": "doing"|"done"|"skip"|"fail", "sub": 설명}
          {"type": "result", "result": {"sentences", "hits", "ai", "ai_error", "verdict"}}
        """
        def step(k, state, sub):
            return {"type": "step", "step": k, "state": state, "sub": sub}

        yield step("dict", "doing", f"{len(self.entries)}개 표현과 대조하는 중")
        result = analyze(segments, self.entries)
        ban = [h for h in result["hits"] if h["level"] == "ban"]
        review = [h for h in result["hits"] if h["level"] == "review"]
        yield step("dict", "done", f"금지 {len(ban)}건, 문맥 확인 {len(review)}건")

        ai, ai_error = None, None
        if ban:
            yield step("ai", "skip", "금지 표현이 있어서 바로 판정했어요")
        elif not review:
            yield step("ai", "skip", "확인할 표현이 없어요")
        elif not self.ai.available():
            ai_error = {"code": "no_api_key"}
            yield step("ai", "skip", "AI 키가 없어서 확인 필요로 남겨요")
        else:
            yield step("ai", "doing", f"Claude가 문장 {len(review)}개를 보고 있어요")
            try:
                ai = self.ai.review(review, result["sentences"])
                yield step("ai", "done", "확인했어요")
            except AIUnavailable as e:
                ai_error = {"code": e.code}
                yield step("ai", "fail", "AI 확인을 하지 못했어요")
            except Exception as e:  # 네트워크, 요금, 권한 오류 등
                ai_error = {"code": "ai_failed", "message": str(e)[:200]}
                yield step("ai", "fail", "AI 확인을 하지 못했어요")

        result["ai"] = ai
        result["ai_error"] = ai_error
        result["verdict"] = verdict(ban, review, ai)
        yield {"type": "result", "result": result}

    def judge(self, segments: list[dict]) -> dict:
        """진행 이벤트 없이 결과만"""
        for ev in self.run(segments):
            if ev["type"] == "result":
                return ev["result"]
        raise RuntimeError("판단 결과가 없습니다")


def verdict(ban: list, review: list, ai: dict | None) -> str:
    """ban(금지) / warn(오해 소지 또는 확인 필요) / ok(문제 없어 보임)"""
    if ban:
        return "ban"
    if review and ai is None:
        return "warn"
    if review:
        items = {int(it.get("n", 0)): it for it in (ai.get("items") or []) if isinstance(it, dict)}
        flagged = [i for i in range(len(review)) if (items[i + 1].get("misleading") if (i + 1) in items else ai.get("misleading"))]
        return "warn" if flagged else "ok"
    return "ok"
