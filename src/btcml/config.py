from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

INTERVAL_SECONDS = {"1s": 1, "1m": 60, "5m": 300, "10m": 600, "1h": 3600}


@dataclass(frozen=True)
class Feed:
    name: str
    url: str


@dataclass(frozen=True)
class Config:
    symbol: str
    data_dir: Path
    archive_url: str
    api_url: str
    history_months: dict[str, int]
    poll_seconds: int
    feeds: list[Feed]

    def klines_dir(self, interval: str) -> Path:
        return self.data_dir / "raw" / "klines" / interval

    def bars_path(self, timeframe: str) -> Path:
        return self.data_dir / "bars" / f"{self.symbol}-{timeframe}.parquet"

    @property
    def news_db(self) -> Path:
        return self.data_dir / "news" / "news.db"


def load_config(path: Path | None = None) -> Config:
    path = path or Path(__file__).resolve().parents[2] / "config.toml"
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    data_dir = Path(raw["data_dir"])
    if not data_dir.is_absolute():
        data_dir = path.parent / data_dir
    return Config(
        symbol=raw["symbol"],
        data_dir=data_dir,
        archive_url=raw["binance"]["archive_url"].rstrip("/"),
        api_url=raw["binance"]["api_url"].rstrip("/"),
        history_months={k: int(v) for k, v in raw["history"].items()},
        poll_seconds=int(raw["news"]["poll_seconds"]),
        feeds=[Feed(**f) for f in raw["news"]["feeds"]],
    )
