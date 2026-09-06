"""Fetch JP/US closing prices into data/jp.json and data/us.json."""

from __future__ import annotations

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parent / "scripts" / "update_quotes.py"), run_name="__main__")
