"""python tests/eval_compare.py — 예전 사전 엔진과 주장구조 엔진을 같은 문장으로 나란히 비교한다.

tests/eval_cases.json 의 verdict 가 정답. 'limit' 표시가 있는 문장은 알려진 한계다.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.claims import ClaimsEngine  # noqa: E402
from pipeline.judge_engine import analyze as old_analyze, load_dictionary  # noqa: E402

CASES = json.loads((ROOT / "tests" / "eval_cases.json").read_text(encoding="utf-8"))["cases"]


def evaluate():
    new, old = ClaimsEngine(ai=False), load_dictionary()
    rows = []
    for c in CASES:
        seg = [{"text": c["text"], "t": None, "thumb": None, "src": "text"}]
        o = old_analyze(seg, old, c["category"])
        ov = "ban" if any(h["level"] == "ban" for h in o["hits"]) else ("warn" if o["hits"] else "ok")
        r = new.judge(seg, c["category"])
        rows.append({**c, "old": ov, "new": r["verdict"], "risk": r["risk"]["score"]})
    return rows


def main():
    rows = evaluate()
    normal = [r for r in rows if not r.get("limit")]
    limits = [r for r in rows if r.get("limit")]
    print(f"{'정답':4} {'예전':4} {'새':4} 위험도  문장")
    for r in rows:
        mark = lambda v: "O" if v == r["verdict"] else "X"
        print(f"{r['verdict']:4} {r['old']:4}{mark(r['old'])} {r['new']:4}{mark(r['new'])} {r['risk']:4}   {r['text'][:46]}" + ("  [한계]" if r.get("limit") else ""))
    for name, key in (("예전 엔진", "old"), ("주장구조 엔진", "new")):
        ok = sum(1 for r in normal if r[key] == r["verdict"])
        print(f"{name}: 일반 문장 {ok}/{len(normal)} 정답")
    print(f"알려진 한계 {len(limits)}개 중 새 엔진이 맞힌 것: {sum(1 for r in limits if r['new'] == r['verdict'])}개")
    return rows


if __name__ == "__main__":
    main()
