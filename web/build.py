"""
웹 데모(web_demo.html) 빌드 스크립트.

web/src/index.html 에 두 가지를 채워 넣어 한 파일짜리 페이지를 만든다.
  1. Pretendard 글꼴 (web/fonts/*.woff2) -> data URI @font-face
     (아티팩트 페이지는 외부 글꼴 서버를 쓸 수 없어서 글꼴을 파일 안에 넣는다)
  2. 팀 금지어 사전 (data/dictionary/geumjieo_list.csv) -> JSON

실행: python web/build.py   (저장소 맨 위에서)
결과: web_demo.html
"""

import base64
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "web" / "src" / "index.html"
OUT = ROOT / "web_demo.html"
FONTS = ROOT / "web" / "fonts"
DICT_CSV = ROOT / "data" / "dictionary" / "geumjieo_list.csv"

FONT_FILES = [
    ("Pretendard-Medium.subset.woff2", 500),
    ("Pretendard-Bold.subset.woff2", 700),
    ("Pretendard-ExtraBold.subset.woff2", 800),
]

# 사전의 '사용단계' 값 -> 웹 엔진이 쓰는 규칙 이름
STAGE_MAP = {
    "1단계 금지어": "ban",
    "1단계 조합(전문가+추천동사)": "combo_expert",
    "1단계 조합(질병명+효능동사)": "combo_disease",
}

# 조합 규칙 안에서 각 단어의 역할
ROLE_MAP = {
    "전문가": "expert",
    "추천동사": "verb",
    "질병명": "disease",
    "효능동사": "effect",
}


def font_faces() -> str:
    rules = []
    for name, weight in FONT_FILES:
        b64 = base64.b64encode((FONTS / name).read_bytes()).decode("ascii")
        rules.append(
            "@font-face{font-family:'Pretendard';font-style:normal;"
            f"font-weight:{weight};font-display:swap;"
            f"src:url(data:font/woff2;base64,{b64}) format('woff2');}}"
        )
    return "\n".join(rules)


def dictionary() -> list[dict]:
    entries = []
    with DICT_CSV.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            word = (row.get("표현") or "").strip()
            stage = STAGE_MAP.get((row.get("사용단계") or "").strip())
            # '확인 필요' 행(예: "보아탕 유사명칭")은 실제 단어가 아니라 메모라서 검사에 쓰지 않는다
            if not word or not stage:
                continue
            cat = (row.get("분류") or "").strip()
            entries.append(
                {
                    "word": word,
                    "cat": cat,
                    "stage": stage,
                    "role": ROLE_MAP.get(cat, ""),
                    "basis": (row.get("근거조항") or "").strip(),
                    "source": (row.get("출처") or "").strip(),
                    "note": (row.get("비고") or "").strip(),
                }
            )
    return entries


def main() -> None:
    html = SRC.read_text(encoding="utf-8")
    for marker in ("/*@@FONTS@@*/", "/*@@DICT@@*/"):
        if html.count(marker) != 1:
            raise SystemExit(f"{SRC} 에 {marker} 가 정확히 한 번 있어야 합니다")
    entries = dictionary()
    html = html.replace("/*@@FONTS@@*/", font_faces())
    html = html.replace("/*@@DICT@@*/", json.dumps(entries, ensure_ascii=False))
    OUT.write_text(html, encoding="utf-8")
    print(f"{OUT.name}: {OUT.stat().st_size / 1024:.0f} KB, 사전 {len(entries)}개")


if __name__ == "__main__":
    main()
