"""python tests/test_ai_provider.py — AI 제공자(Groq/Claude)와 AI 참고 의견 시험 (네트워크 없이 가짜 응답)

1) 환경변수로 제공자를 고르는지
2) Groq 호출 모양(주소, 인증, 모델)과 응답 해석, HTTP 오류 코드
3) AI 답 검증 (번호 범위, 모양이 틀린 답)
4) 주장구조 엔진: AI 의견이 판정·위험도를 바꾸지 않고, 실패해도 규칙 판정이 그대로 나오는지
"""
import io
import json
import os
import sys
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import ai_provider  # noqa: E402
from pipeline.claims import ClaimsEngine  # noqa: E402
from pipeline.judge_engine import AIUnavailable  # noqa: E402

FAKE_KEY = "test-key-not-real"
AI_ENV = ("GROQ_API_KEY", "GROQ_MODEL", "ANTHROPIC_API_KEY", "AI_PROVIDER")


def env(**kw):
    clean = {k: v for k, v in os.environ.items() if k not in AI_ENV}
    clean.update(kw)
    return mock.patch.dict(os.environ, clean, clear=True)


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def groq_reply(content: str):
    return FakeResp(json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8"))


def raises(fn, code):
    try:
        fn()
    except AIUnavailable as e:
        assert e.code == code, (e.code, code)
        return
    raise AssertionError(f"AIUnavailable({code}) 이 나야 한다")


def test_provider_selection():
    with env():
        assert ai_provider.provider() is None
    with env(ANTHROPIC_API_KEY=FAKE_KEY):
        assert ai_provider.provider() == "anthropic"
    with env(GROQ_API_KEY=FAKE_KEY, ANTHROPIC_API_KEY=FAKE_KEY):
        assert ai_provider.provider() == "groq"
    with env(GROQ_API_KEY=FAKE_KEY, ANTHROPIC_API_KEY=FAKE_KEY, AI_PROVIDER="anthropic"):
        assert ai_provider.provider() == "anthropic"
    with env(ANTHROPIC_API_KEY=FAKE_KEY, AI_PROVIDER="groq"):
        assert ai_provider.provider() is None  # 강제했는데 키가 없으면 쓰지 않는다


def test_groq_request_and_parse():
    seen = {}

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return groq_reply('```json\n{"misleading": true, "summary": "s", "items": []}\n```')

    with env(GROQ_API_KEY=FAKE_KEY), mock.patch("urllib.request.urlopen", fake_urlopen):
        out = ai_provider.groq_json("문장")
    assert out["misleading"] is True
    assert seen["url"] == ai_provider.GROQ_URL
    assert seen["auth"] == "Bearer " + FAKE_KEY
    assert seen["body"]["model"] == "openai/gpt-oss-120b"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["reasoning_effort"] == "low"

    with env(GROQ_API_KEY=FAKE_KEY, GROQ_MODEL="openai/gpt-oss-20b"), \
            mock.patch("urllib.request.urlopen", fake_urlopen):
        ai_provider.groq_json("문장")
    assert seen["body"]["model"] == "openai/gpt-oss-20b"


def test_groq_errors():
    def http_error(code):
        def f(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(b'{"error":{"message":"m"}}'))
        return f

    with env(GROQ_API_KEY=FAKE_KEY):
        for status, code in ((401, "bad_key"), (403, "forbidden"), (429, "rate_limit"), (500, "ai_failed")):
            with mock.patch("urllib.request.urlopen", http_error(status)):
                raises(lambda: ai_provider.groq_json("p"), code)
        with mock.patch("urllib.request.urlopen", lambda req, timeout=0: groq_reply("JSON 아님")):
            raises(lambda: ai_provider.groq_json("p"), "invalid_json")
        with mock.patch("urllib.request.urlopen", lambda req, timeout=0: FakeResp(b'{"choices": []}')):
            raises(lambda: ai_provider.groq_json("p"), "invalid_json")
    with env():
        raises(lambda: ai_provider.groq_json("p"), "no_api_key")


def test_validate_review():
    out = ai_provider.validate_review({
        "summary": " 요약 ",
        "items": [
            {"n": 1, "misleading": True, "reason": "이유" * 200},
            {"n": 1, "misleading": False, "reason": "중복"},
            {"n": 9, "misleading": False, "reason": "범위 밖"},
            {"n": "2", "misleading": "모름", "reason": 3},
            "쓰레기",
        ],
    }, 2)
    assert out["summary"] == "요약"
    assert [it["n"] for it in out["items"]] == [1, 2]
    assert out["items"][0]["misleading"] is True and len(out["items"][0]["reason"]) == 200
    assert out["items"][1] == {"n": 2, "misleading": None, "reason": ""}
    assert out["misleading"] is True  # 전체 값이 없으면 항목에서 정한다

    raises(lambda: ai_provider.validate_review([], 1), "invalid_json")
    raises(lambda: ai_provider.validate_review({"items": []}, 1), "invalid_json")
    raises(lambda: ai_provider.validate_review({"summary": "s", "items": [{"n": 5}]}, 1), "invalid_json")


class FakeAI:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def available(self):
        return True

    def label(self):
        return "Groq"

    def review(self, hits, sentences, category):
        self.calls.append(len(hits))
        if self.error:
            raise self.error
        return ai_provider.validate_review(self.reply, len(hits))


def judge(engine, text, category="general"):
    return engine.judge([{"text": text, "t": None, "thumb": None, "src": "text"}], category)


WARN_TEXT = "암 걱정 없는 식단"           # 확인 필요 (규칙)
BAN_TEXT = "당뇨를 치료하는 차"            # 금지 (규칙)


def test_ai_opinion_does_not_change_verdict():
    base = ClaimsEngine(ai=False)
    # AI가 "문제 없다"고 해도 규칙 판정(warn)과 점수는 그대로
    fake = FakeAI({"misleading": False, "summary": "괜찮아 보여요", "items": [{"n": 1, "misleading": False, "reason": "r"}]})
    eng = ClaimsEngine(ai=fake)
    r0, r1 = judge(base, WARN_TEXT), judge(eng, WARN_TEXT)
    assert r0["verdict"] == r1["verdict"] == "warn"
    assert r0["risk"] == r1["risk"]
    assert r1["ai"]["role"] == "opinion" and r1["ai"]["source"] == "ai" and r1["ai"]["provider"] == "Groq"
    assert r1["ai_error"] is None and fake.calls == [1]


def test_ai_only_for_review():
    fake = FakeAI({"summary": "s", "items": [{"n": 1, "misleading": True, "reason": "r"}]})
    eng = ClaimsEngine(ai=fake)
    r = judge(eng, "맛있는 보리차")
    assert r["verdict"] == "ok" and r["ai"] is None and fake.calls == []
    r = judge(eng, BAN_TEXT)
    review = [h for h in r["hits"] if h["level"] == "review"]
    assert r["verdict"] == "ban"
    assert (r["ai"] is None) == (not review)  # 금지 문장만 있으면 묻지 않는다


def test_ai_failure_falls_back_to_rules():
    base = judge(ClaimsEngine(ai=False), WARN_TEXT)
    for err, code in ((AIUnavailable("rate_limit"), "rate_limit"), (RuntimeError("boom"), "ai_failed")):
        r = judge(ClaimsEngine(ai=FakeAI(error=err)), WARN_TEXT)
        assert r["ai"] is None and r["ai_error"]["code"] == code
        assert r["verdict"] == base["verdict"] and r["risk"] == base["risk"]
    # 모양이 틀린 답도 버리고 규칙만
    r = judge(ClaimsEngine(ai=FakeAI({"misleading": True})), WARN_TEXT)
    assert r["ai"] is None and r["ai_error"]["code"] == "invalid_json"


def test_no_key_means_no_ai_step():
    with env():
        eng = ClaimsEngine()
        assert not eng.ai.available()
        r = judge(eng, WARN_TEXT)
        assert r["ai"] is None and r["ai_error"] is None


def test_groq_end_to_end_with_fake_http():
    content = json.dumps({"misleading": True, "summary": "암 예방으로 읽힐 수 있어요", "items": [{"n": 1, "misleading": True, "reason": "r"}]},
                         ensure_ascii=False)
    with env(GROQ_API_KEY=FAKE_KEY), mock.patch("urllib.request.urlopen", lambda req, timeout=0: groq_reply(content)):
        events = list(ClaimsEngine().run([{"text": WARN_TEXT, "t": None, "thumb": None, "src": "text"}], "general"))
    r = events[-1]["result"]
    assert r["ai"]["provider"] == "Groq" and r["ai"]["items"][0]["misleading"] is True and r["verdict"] == "warn"
    assert any(ev.get("step") == "ai" and ev.get("state") == "done" for ev in events)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK: ai provider tests")
