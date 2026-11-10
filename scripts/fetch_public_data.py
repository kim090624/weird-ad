"""
식약처가 인정한 기능성 문구를 공공데이터에서 받아 저장한다.

  python scripts/fetch_public_data.py --key 내인증키

결과: data/public/functional_phrases.json
  출처: 식품안전나라 Open API  I2710(건강기능식품 기능성 원료, 고시형), I-0050(개별인정형 원료)
저장해 두면 서버가 인증키 없이도 이 목록을 쓴다 (pipeline/foodsafety.functional_phrases).
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import foodsafety  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True, help="식품안전나라 Open API 인증키")
    a = ap.parse_args()
    phrases = foodsafety.fetch_functional_phrases(key=a.key)
    by_src = {}
    for p in phrases:
        by_src[p["source"]] = by_src.get(p["source"], 0) + 1
    out = {
        "fetched_at": datetime.date.today().isoformat(),
        "source": "식품안전나라 Open API (식품의약품안전처)",
        "services": {k: foodsafety.SOURCES[k] for k in by_src},
        "counts": by_src,
        "phrases": phrases,
    }
    foodsafety.SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    foodsafety.SNAPSHOT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{foodsafety.SNAPSHOT}: {len(phrases)}개 ({by_src})")


if __name__ == "__main__":
    main()
