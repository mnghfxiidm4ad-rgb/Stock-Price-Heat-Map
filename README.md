# 株価ヒートマップ

日本株と米株の終値を業種ヒートマップで表示します。プラスは赤、マイナスは緑です。リポジトリは非公開です。公開URLはありません。

各営業日のヒートマップ日付（`asof`）に対応する、市場を動かした主要ニュースの箇条書きも JSON として保存します。画面では日付を切り替えるとその日のニュースが出ます。

## 見る方法

`start.bat` を実行すると `http://127.0.0.1:8765` が開きます。GitHub 上の最新 JSON を使うか、`C:\data\日本株` に日足がある場合は日付を遡って表示できます。

## 自動更新（終値）

GitHub Actions が、終値の取得できる時間以降に Yahoo Finance から日足を取り、`data/jp.json` / `data/us.json` をこのリポジトリへ書き戻します。非公開のままでも更新されます。

| 市場 | 取引終了 | 更新時刻（目安） |
|------|----------|------------------|
| 日本株（東証） | 15:00 JST | 17:00 JST と 19:00 JST（月〜金） |
| 米株（NYSE / NASDAQ） | 16:00 ET | 21:30 UTC（夏時間）と 23:00 UTC（冬時間もカバー）（月〜金） |

祝日は前営業日の終値のままになります。Actions タブから手動実行もできます。

## 市場ニュース（JP / US）

終値ヒートマップと同じセッション日 `YYYY-MM-DD` で、主要指標・セクター・見出しを 1 ファイルにまとめます。

| 項目 | 日本株（JP） | 米国株（US） |
|------|--------------|--------------|
| 主要指標 | 日経平均, TOPIX, ドル/円 | S&P 500, NASDAQ, NYダウ, 米10年債利回り |
| 営業日 | 東証カレンダー + 指数足の有無 | NYSE カレンダー + 指数足の有無 |
| 日次実行の目安 | 16:00 JST 以降 | 06:30 JST 以降（夏時間は 05:30 JST 以降） |
| 見出しの情報源 | NHK / Yahoo / 日銀 RSS、Google News、GDELT | Reuters / CNBC / MarketWatch / FRB RSS、Google News、GDELT |

### 保存先

- 日本株: `data/market_news/jp/YYYY-MM-DD.json`
- 米国株: `data/market_news/us/YYYY-MM-DD.json`
- 日付一覧: `data/market_news/jp/index.json` と `data/market_news/us/index.json`

ヒートマップの `data/jp.json` / `data/us.json` の `asof` と同じ日付キーです。フロントは日付変更時に同じ日のニュース JSON を読みます。

### 実行方法

収集ツールは `requirements-update.txt` の依存関係を使います。市況要因の 3〜4 行は Gemini または OpenAI で生成します。

```bat
python -m pip install -r requirements-update.txt
set GEMINI_API_KEY=your-key
```

`OPENAI_API_KEY` でも動きます。キーが無いときはルールベースに落ちます。

過去ログ一括（Backfill）。非営業日は `pandas_market_calendars` / `holidays` で自動スキップします。

```bat
python collect_market_news.py --market jp --start 2024-01-01 --end 2026-08-31
python collect_market_news.py --market us --start 2024-01-01 --end 2026-08-31
python collect_market_news.py --market all --start 2024-01-01 --end 2026-08-31
```

日次（Daily）。引け前に走らせると前営業日になります。GitHub Actions からも同じコマンドを呼びます。

```bat
python collect_market_news.py --market jp --daily
python collect_market_news.py --market us --daily
```

既存ファイルを作り直すときは `--force`。LLM を使わない検証は `--no-llm`。進捗は tqdm、待機は `--sleep` です。

### GitHub Actions

| ワークフロー | 実行時刻 | コマンド |
|--------------|----------|----------|
| `Update JP market news` | 16:00 JST（月〜金） | `python collect_market_news.py --market jp --daily` |
| `Update US market news` | 06:30 JST（夏時間は 05:30 JST） | `python collect_market_news.py --market us --daily` |

リポジトリ Secrets に `GEMINI_API_KEY` または `OPENAI_API_KEY` を入れてください。

### ヒートマップとのつなぎ方

1. 収集結果は静的 JSON なので、ローカルサーバーでも GitHub 上のファイル配信でも同じパスで読めます。
2. ローカル `server.py` は `GET /api/news?market=日本株&asof=YYYY-MM-DD` と `GET /api/news-days` を返します。
3. 画面の日付・市場切替は既存のヒートマップ状態を使い、同じ `asof` のニュースを横に出します。
4. JP の当日は `data/jp.json` の 33 業種平均が取れればそれを優先し、過去日や米株はセクター ETF の騰落を使います。
