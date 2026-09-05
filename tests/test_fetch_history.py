from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import fetch_history as fh  # noqa: E402
import data as data_mod  # noqa: E402


def _bars(dates: list[str], close: float = 100.0) -> pd.DataFrame:
    idx = pd.to_datetime(dates)
    return pd.DataFrame(
        {
            "Date": idx,
            "Open": close,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Adj Close": close,
            "Volume": 1000,
        }
    )


class PlanTests(unittest.TestCase):
    def test_empty_downloads(self) -> None:
        today = pd.Timestamp("2026-09-05")
        self.assertEqual(
            fh.plan_ticker(pd.DataFrame(), None, False, today=today),
            "download",
        )

    def test_max_skips_when_full_and_fresh(self) -> None:
        today = pd.Timestamp("2026-09-05")
        existing = _bars(["2000-01-04", "2026-09-04"])
        self.assertEqual(fh.plan_ticker(existing, None, True, today=today), "skip")

    def test_max_incremental_when_stale(self) -> None:
        today = pd.Timestamp("2026-09-05")
        existing = _bars(["2000-01-04", "2026-08-01"])
        self.assertEqual(fh.plan_ticker(existing, None, True, today=today), "incremental")

    def test_one_year_then_max_downloads(self) -> None:
        today = pd.Timestamp("2026-09-05")
        existing = _bars(["2025-09-01", "2026-09-04"])
        self.assertEqual(fh.plan_ticker(existing, None, False, today=today), "download")

    def test_five_year_covers_one_year(self) -> None:
        today = pd.Timestamp("2026-09-05")
        existing = _bars(["2021-01-04", "2026-09-04"])
        want = fh.requested_start("1y", "", today=today)
        self.assertEqual(fh.plan_ticker(existing, want, False, today=today), "skip")

    def test_one_year_does_not_cover_five_year(self) -> None:
        today = pd.Timestamp("2026-09-05")
        existing = _bars(["2025-09-01", "2026-09-04"])
        want = fh.requested_start("5y", "", today=today)
        self.assertEqual(fh.plan_ticker(existing, want, False, today=today), "download")

    def test_force_always_downloads(self) -> None:
        today = pd.Timestamp("2026-09-05")
        existing = _bars(["2000-01-04", "2026-09-04"])
        self.assertEqual(fh.plan_ticker(existing, None, True, today=today, force=True), "download")


class MergeFrameTests(unittest.TestCase):
    def test_merge_keeps_newer_overlap(self) -> None:
        old = _bars(["2024-01-04", "2024-01-05"], close=10)
        new = _bars(["2024-01-05", "2024-01-08"], close=20)
        merged = fh.merge_bars(old, new)
        self.assertEqual(list(merged["Date"].dt.strftime("%Y-%m-%d")), ["2024-01-04", "2024-01-05", "2024-01-08"])
        row = merged.loc[merged["Date"] == pd.Timestamp("2024-01-05")].iloc[0]
        self.assertEqual(float(row["Close"]), 20.0)

    def test_ohlcv_from_multiindex(self) -> None:
        idx = pd.to_datetime(["2020-01-06", "2020-01-07"])
        one = pd.DataFrame(
            {"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Adj Close": 1.5, "Volume": 10},
            index=idx,
        )
        two = one * 2
        raw = pd.concat({"7203.T": one, "7267.T": two}, axis=1)
        frame = fh.ohlcv_frame(raw, "7203.T")
        self.assertEqual(len(frame), 2)
        self.assertEqual(float(frame["Close"].iloc[-1]), 1.5)
        frame2 = fh.ohlcv_frame(raw, "7267.T")
        self.assertEqual(float(frame2["Close"].iloc[-1]), 3.0)

    def test_requested_start_max_is_none(self) -> None:
        self.assertIsNone(fh.requested_start("max", ""))
        start = fh.requested_start("1y", "", today=pd.Timestamp("2026-09-05"))
        self.assertEqual(start, pd.Timestamp("2025-09-05"))


class RunHistoryTests(unittest.TestCase):
    def test_skip_already_fetched_max(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fh.HISTORY_ROOT = root
            dest = fh.history_dir("jp")
            dest.mkdir(parents=True)
            df = _bars(["2000-01-04", "2026-09-04"])
            fh.write_bars(fh.parquet_path("jp", "7203.T"), df)
            fh.save_meta(
                "jp",
                {
                    "7203.T": {
                        "min_day": "2000-01-04",
                        "max_day": "2026-09-04",
                        "rows": 2,
                        "period": "max",
                    }
                },
            )
            calls: list[tuple] = []

            def fake(symbols, period=None, start=None, end=None, retries=5):
                calls.append((list(symbols), period, start))
                return pd.DataFrame()

            code = fh.run_history(
                "jp",
                period="max",
                tickers=["7203.T"],
                enable_keys=False,
                download_fn=fake,
                today=pd.Timestamp("2026-09-05"),
                sleep_sec=0,
            )
            self.assertEqual(code, 0)
            self.assertEqual(calls, [])

    def test_one_year_then_max_fetches_full_range(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            fh.HISTORY_ROOT = Path(raw)
            dest = fh.history_dir("jp")
            dest.mkdir(parents=True)
            fh.write_bars(fh.parquet_path("jp", "7203.T"), _bars(["2025-09-01", "2026-09-04"]))
            calls: list[tuple] = []

            def fake(symbols, period=None, start=None, end=None, retries=5):
                calls.append((list(symbols), period, start))
                idx = pd.bdate_range("2000-01-04", "2026-09-04")
                part = pd.DataFrame(
                    {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Adj Close": 100.0, "Volume": 1000},
                    index=idx,
                )
                return pd.concat({"7203.T": part}, axis=1)

            code = fh.run_history(
                "jp",
                period="max",
                tickers=["7203.T"],
                enable_keys=False,
                download_fn=fake,
                today=pd.Timestamp("2026-09-05"),
                sleep_sec=0,
            )
            self.assertEqual(code, 0)
            self.assertEqual(calls, [(["7203.T"], "max", None)])
            saved = fh.load_existing_bars(fh.parquet_path("jp", "7203.T"))
            self.assertGreater(len(saved), 1000)
            self.assertEqual(pd.Timestamp(saved["Date"].min()).strftime("%Y-%m-%d"), "2000-01-04")
            meta = fh.load_meta("jp")
            self.assertEqual(meta["7203.T"]["period"], "max")

    def test_incremental_requests_only_new_days(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            fh.HISTORY_ROOT = Path(raw)
            fh.write_bars(fh.parquet_path("jp", "7203.T"), _bars(["2000-01-04", "2026-08-01"]))
            fh.save_meta("jp", {"7203.T": {"period": "max", "min_day": "2000-01-04", "max_day": "2026-08-01", "rows": 2}})
            calls: list[tuple] = []

            def fake(symbols, period=None, start=None, end=None, retries=5):
                calls.append((list(symbols), period, start))
                idx = pd.to_datetime(["2026-08-03", "2026-09-04"])
                part = pd.DataFrame(
                    {"Open": 110.0, "High": 111.0, "Low": 109.0, "Close": 110.0, "Adj Close": 110.0, "Volume": 1000},
                    index=idx,
                )
                return pd.concat({"7203.T": part}, axis=1)

            fh.run_history(
                "jp",
                period="max",
                tickers=["7203.T"],
                enable_keys=False,
                download_fn=fake,
                today=pd.Timestamp("2026-09-05"),
                sleep_sec=0,
            )
            self.assertEqual(calls[0][1], None)
            self.assertEqual(calls[0][2], "2026-08-02")
            saved = fh.load_existing_bars(fh.parquet_path("jp", "7203.T"))
            self.assertEqual(pd.Timestamp(saved["Date"].max()).strftime("%Y-%m-%d"), "2026-09-04")
            self.assertEqual(len(saved), 4)


class DataHistoryTests(unittest.TestCase):
    def test_loads_repo_history_parquet(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            data_mod.HISTORY_ROOT = root
            data_mod.DATA_ROOT = root / "unused-local"
            folder = root / "jp"
            folder.mkdir()
            df = pd.DataFrame(
                {
                    "Date": pd.to_datetime(["2026-09-01", "2026-09-02", "2026-09-03"]),
                    "Close": [10.0, 11.0, 12.0],
                    "Volume": [100, 200, 300],
                }
            )
            df.to_parquet(folder / "7203.T.parquet", index=False)
            history = data_mod.load_market_history("日本株")
            self.assertEqual(len(history.bars), 1)
            self.assertEqual(history.bars[0].ticker, "7203.T")
            self.assertIn("2026-09-03", history.days)
            quotes = history.quotes("2026-09-03")
            self.assertEqual(len(quotes), 1)
            self.assertAlmostEqual(quotes[0]["price"], 12.0)


if __name__ == "__main__":
    unittest.main()
