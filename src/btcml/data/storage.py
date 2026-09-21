from __future__ import annotations

from datetime import datetime

import polars as pl

from ..config import Config

SCHEMA_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trades",
    "taker_buy_base",
    "taker_buy_quote",
]


def kline_files(cfg: Config, interval: str, *, archive_only: bool = False) -> list[str]:
    """Parquet files for an interval: <dir>/{monthly,daily,api}/*.parquet."""
    base = cfg.klines_dir(interval)
    folders = ("monthly", "daily") if archive_only else ("monthly", "daily", "api")
    return sorted(str(p) for f in folders for p in (base / f).glob("*.parquet"))


def load_klines(
    cfg: Config,
    interval: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> pl.DataFrame:
    """Load all candles for an interval, deduplicated and sorted by open_time.

    start/end must be timezone-aware (UTC). end is exclusive.
    """
    files = kline_files(cfg, interval)
    if not files:
        raise FileNotFoundError(
            f"No {interval} data under {cfg.klines_dir(interval)}. Run: btcml download"
        )
    lf = pl.scan_parquet(files)
    if start is not None:
        lf = lf.filter(pl.col("open_time") >= start)
    if end is not None:
        lf = lf.filter(pl.col("open_time") < end)
    return (
        lf.unique("open_time", keep="last", maintain_order=True)
        .sort("open_time")
        .collect()
    )


def write_parquet_atomic(df: pl.DataFrame, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.write_parquet(tmp, compression="zstd")
    tmp.replace(path)
