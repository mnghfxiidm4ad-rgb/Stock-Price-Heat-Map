#!/usr/bin/env bash
# Copy only the public heatmap + news files for Cloudflare Pages.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/_site"

rm -rf "$OUT"
mkdir -p "$OUT/static" "$OUT/data"

cp "$ROOT/index.html" "$OUT/index.html"
cp -R "$ROOT/static/." "$OUT/static/"
cp -R "$ROOT/data/quotes" "$OUT/data/quotes"
cp -R "$ROOT/data/market_news" "$OUT/data/market_news"
cp -f "$ROOT/data/jp.json" "$OUT/data/jp.json" 2>/dev/null || true
cp -f "$ROOT/data/us.json" "$OUT/data/us.json" 2>/dev/null || true
cp "$ROOT/deploy/_headers" "$OUT/_headers"

echo "prepared $OUT"
find "$OUT" -type f | wc -l
