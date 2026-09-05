"""取得進捗の共有（コンソールメーター + JSON）。"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def progress_path(market: str) -> Path:
    code = "us" if str(market).lower() in {"us", "米株"} else "jp"
    return REPO_ROOT / "data" / "history" / code / "progress.json"


@dataclass
class FetchProgress:
    running: bool = False
    market: str = ""
    phase: str = ""
    done: int = 0
    total: int = 0
    saved: int = 0
    skipped: int = 0
    failed: int = 0
    current: str = ""
    message: str = ""
    percent: float = 0.0
    started_at: str = ""
    updated_at: str = ""
    eta_sec: float | None = None
    logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProgressTracker:
    def __init__(self, market: str, total: int, phase: str = "fetch") -> None:
        self.market = "us" if str(market).lower() in {"us", "米株"} else "jp"
        self.total = max(0, int(total))
        self.phase = phase
        self.done = 0
        self.saved = 0
        self.skipped = 0
        self.failed = 0
        self.current = ""
        self.message = ""
        self.started = time.time()
        self.started_at = _now_iso()
        self.logs: list[str] = []
        self._last_paint = 0.0
        self.path = progress_path(self.market)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write(running=True)
        self._paint(force=True)

    def log(self, text: str) -> None:
        line = text.strip()
        if not line:
            return
        self.logs.append(line)
        if len(self.logs) > 40:
            self.logs = self.logs[-40:]
        print(line, flush=True)
        self._write(running=True)

    def update(
        self,
        *,
        done: int | None = None,
        saved: int | None = None,
        skipped: int | None = None,
        failed: int | None = None,
        current: str = "",
        message: str = "",
        phase: str | None = None,
    ) -> None:
        if done is not None:
            self.done = done
        if saved is not None:
            self.saved = saved
        if skipped is not None:
            self.skipped = skipped
        if failed is not None:
            self.failed = failed
        if current:
            self.current = current
        if message:
            self.message = message
        if phase:
            self.phase = phase
        self._write(running=True)
        self._paint()

    def finish(self, message: str = "完了") -> None:
        self.message = message
        self.done = self.total or self.done
        self._write(running=False)
        self._paint(force=True)
        print(file=sys.stderr)

    def _percent(self) -> float:
        if self.total <= 0:
            return 0.0
        return round(100.0 * min(self.done, self.total) / self.total, 1)

    def _eta(self) -> float | None:
        if self.done <= 0 or self.total <= 0 or self.done >= self.total:
            return None
        elapsed = time.time() - self.started
        rate = self.done / elapsed if elapsed > 0 else 0.0
        if rate <= 0:
            return None
        return (self.total - self.done) / rate

    def _write(self, *, running: bool) -> None:
        payload = FetchProgress(
            running=running,
            market=self.market,
            phase=self.phase,
            done=self.done,
            total=self.total,
            saved=self.saved,
            skipped=self.skipped,
            failed=self.failed,
            current=self.current,
            message=self.message,
            percent=self._percent(),
            started_at=self.started_at,
            updated_at=_now_iso(),
            eta_sec=self._eta(),
            logs=list(self.logs),
        )
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def _paint(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_paint < 0.2:
            return
        self._last_paint = now
        pct = self._percent()
        width = 28
        filled = int(width * pct / 100.0) if self.total else 0
        bar = "#" * filled + "-" * (width - filled)
        eta = self._eta()
        eta_txt = f" ETA {int(eta)}s" if eta is not None else ""
        line = (
            f"\r[{bar}] {pct:5.1f}%  {self.done}/{self.total or '?'}  "
            f"保存 {self.saved} スキップ {self.skipped} 失敗 {self.failed}  "
            f"{self.current[:24]}{eta_txt}   "
        )
        print(line, end="", file=sys.stderr, flush=True)


def read_progress(market: str = "jp") -> dict[str, Any]:
    path = progress_path(market)
    if not path.is_file():
        return {
            "running": False,
            "market": "us" if str(market).lower() in {"us", "米株"} else "jp",
            "message": "進捗ファイルがありません",
            "percent": 0,
            "done": 0,
            "total": 0,
            "logs": [],
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"running": False, "market": market, "message": "進捗の読込に失敗", "logs": []}
