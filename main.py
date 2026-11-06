"""
터미널에서 확인하기 (웹 서버와 같은 파이프라인)

  python main.py --check                 필요한 프로그램이 다 있는지
  python main.py --video 광고.mp4        영상 확인 (화면 글자 + 소리)
  python main.py --video 광고.mp4 --no-stt
  python main.py --text "의사가 추천하는 건강차"
"""

import argparse
import json
import sys
from pathlib import Path

from pipeline.extractors import check_dependencies
from pipeline.judge_engine import JudgeEngine
from pipeline.run import run_text, run_video

VERDICT = {"ban": "부당광고 (금지 표현)", "warn": "확인 필요 / 오해 소지", "ok": "문제 없어 보임"}


def print_check():
    engine = JudgeEngine()
    for name, ok in check_dependencies().items():
        print(f"  [{'OK' if ok else '없음'}] {name}")
    print(f"  [{'OK' if engine.ai.available() else '없음'}] ANTHROPIC_API_KEY (문맥 확인 단계에만 필요)")
    print(f"  사전: {len(engine.entries)}개 표현")


def main():
    ap = argparse.ArgumentParser(description="부당광고 판별")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--video")
    ap.add_argument("--text")
    ap.add_argument("--narration", default="")
    ap.add_argument("--no-stt", action="store_true")
    ap.add_argument("--json", action="store_true", help="결과를 JSON으로 출력")
    a = ap.parse_args()

    if a.check:
        print_check()
        return
    if not a.video and not a.text:
        ap.print_help()
        sys.exit(1)

    engine = JudgeEngine()
    events = run_video(Path(a.video), engine, a.narration, not a.no_stt) if a.video else run_text(a.text, engine)
    result = None
    for ev in events:
        if ev["type"] == "step":
            print(f"  [{ev['step']}] {ev['state']:5} {ev['sub']}", file=sys.stderr)
        elif ev["type"] == "error":
            print(f"오류: {ev['code']} {ev.get('message', '')}", file=sys.stderr)
            sys.exit(2)
        else:
            result = ev["result"]

    if a.json:
        for s in result["sentences"]:
            s.pop("thumb", None)
        for h in result["hits"]:
            h["S"].pop("thumb", None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print("\n판정:", VERDICT[result["verdict"]])
    for h in result["hits"]:
        t = f"{int(h['S']['t'] // 60)}:{int(h['S']['t'] % 60):02d} " if h["S"].get("t") is not None else ""
        print(f"  - [{h['tag']}] {h['title']}  {t}\"{h['S']['text']}\"")
    if result.get("ai"):
        print("AI:", result["ai"].get("summary", ""))
    elif result.get("ai_error"):
        print("AI 확인 못 함:", result["ai_error"]["code"])


if __name__ == "__main__":
    main()
