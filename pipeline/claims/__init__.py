"""주장구조 엔진 — 문장을 (대상 + 강도 + 맥락)으로 쪼개 판정표로 판정한다. AI 없이 동작한다."""

from pipeline.claims.engine import ClaimsEngine, analyze  # noqa: F401
from pipeline.claims.lexicon import load_lexicon  # noqa: F401
