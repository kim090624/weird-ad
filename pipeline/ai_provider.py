"""
AI 제공자 계층 — 문맥 확인(AI 참고 의견)에 쓸 AI를 고른다.

  GROQ_API_KEY       Groq (OpenAI 호환 API). 있으면 우선 쓴다.
  GROQ_MODEL         기본 openai/gpt-oss-120b.
                     (llama-3.3-70b-versatile 은 2026-08-16 에 무료 계정에서 종료됐다. 모델 이름은 Groq 문서에서 확인)
  ANTHROPIC_API_KEY  Claude. Groq 키가 없을 때 쓴다.
  AI_PROVIDER        groq 또는 anthropic 으로 강제 (선택)

키는 코드와 저장소에 두지 않고 서버 환경변수(Render > Environment)로만 읽는다.
AI가 없거나 실패해도 판정은 규칙만으로 그대로 나온다.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from pipeline.judge_engine import AIUnavailable, _parse_json

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
LABELS = {"groq": "Groq", "anthropic": "Claude"}


def groq_key() -> str | None:
    return os.environ.get("GROQ_API_KEY") or None


def anthropic_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or None


def provider() -> str | None:
    """지금 쓸 제공자 이름 (groq / anthropic) 또는 None"""
    forced = (os.environ.get("AI_PROVIDER") or "").strip().lower()
    if forced == "groq":
        return "groq" if groq_key() else None
    if forced == "anthropic":
        return "anthropic" if anthropic_key() else None
    if groq_key():
        return "groq"
    if anthropic_key():
        return "anthropic"
    return None


def label(name: str | None) -> str:
    return LABELS.get(name or "", "AI")


def groq_model() -> str:
    return os.environ.get("GROQ_MODEL") or DEFAULT_GROQ_MODEL


def groq_json(prompt: str, key: str | None = None, model: str | None = None, timeout: int = 30) -> dict:
    """Groq 에 프롬프트를 보내 JSON 객체 하나를 받는다. 실패하면 AIUnavailable(code)."""
    key = key or groq_key()
    if not key:
        raise AIUnavailable("no_api_key")
    model = model or groq_model()
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "JSON 객체 하나로만 답한다. 설명이나 코드블록을 붙이지 않는다."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 2000,
    }
    if model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"  # 문장 몇 개를 보는 일이라 오래 생각할 필요가 없다
    req = urllib.request.Request(
        GROQ_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "adscan/1.0",  # 기본 파이썬 User-Agent 는 막히는 경우가 있다
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = (json.loads(e.read().decode("utf-8")).get("error") or {}).get("message", "")
        except Exception:
            pass
        code = {401: "bad_key", 403: "forbidden", 429: "rate_limit"}.get(e.code, "ai_failed")
        raise AIUnavailable(code, f"HTTP {e.code} {detail}"[:200]) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AIUnavailable("ai_failed", f"연결 실패: {e}"[:200]) from e
    try:
        raw = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise AIUnavailable("invalid_json", str(data)[:300]) from e
    try:
        out = _parse_json(raw)
    except (ValueError, json.JSONDecodeError) as e:
        raise AIUnavailable("invalid_json", raw[:300]) from e
    if not isinstance(out, dict):
        raise AIUnavailable("invalid_json", raw[:300])
    return out
