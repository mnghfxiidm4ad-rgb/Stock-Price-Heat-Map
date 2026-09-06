from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_site"


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "static").mkdir(parents=True)
    (OUT / "data").mkdir(parents=True)
    shutil.copy2(ROOT / "index.html", OUT / "index.html")
    shutil.copytree(ROOT / "static", OUT / "static", dirs_exist_ok=True)
    shutil.copytree(ROOT / "data" / "quotes", OUT / "data" / "quotes")
    shutil.copytree(ROOT / "data" / "market_news", OUT / "data" / "market_news")
    for name in ("jp.json", "us.json"):
        src = ROOT / "data" / name
        if src.is_file():
            shutil.copy2(src, OUT / "data" / name)
    headers = ROOT / "deploy" / "_headers"
    if headers.is_file():
        shutil.copy2(headers, OUT / "_headers")
    files = [p for p in OUT.rglob("*") if p.is_file()]
    print(f"prepared {OUT}  files={len(files)}")


if __name__ == "__main__":
    main()
