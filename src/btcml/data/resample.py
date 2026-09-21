from __future__ import annotations

from datetime import timedelta

import polars as pl

from ..config import INTERVAL_SECONDS, Config
from .storage import load_klines, write_parquet_atomic


def resample_ohlcv(df: pl.DataFrame, timeframe: str, source: str) -> pl.DataFrame:
    """Aggregate candles into a coarser timeframe (windows aligned to the UTC epoch).

    Partial buckets at the start and end of the data are dropped. `n_src` is the
    number of source candles in each bucket, so buckets with gaps stay visible.
    """
    src_s, tf_s = INTERVAL_SECONDS[source], INTERVAL_SECONDS[timeframe]
    if tf_s <= src_s or tf_s % src_s:
        raise ValueError(f"cannot build {timeframe} from {source}")
    first_src = df["open_time"][0]
    last_src_end = df["open_time"][-1] + timedelta(seconds=src_s)
    tf_step = timedelta(seconds=tf_s)

    return (
        df.sort("open_time")
        .group_by_dynamic("open_time", every=timeframe, closed="left", label="left")
        .agg(
            pl.col("open").first(),
            pl.col("high").max(),
            pl.col("low").min(),
            pl.col("close").last(),
            pl.col("volume").sum(),
            pl.col("quote_volume").sum(),
            pl.col("trades").sum(),
            pl.col("taker_buy_base").sum(),
            pl.col("taker_buy_quote").sum(),
            pl.len().alias("n_src"),
        )
        .filter(
            (pl.col("open_time") >= first_src)
            & (pl.col("open_time") + tf_step <= last_src_end)
        )
    )


def build_bars(cfg: Config, timeframe: str, source: str = "1m") -> int:
    bars = resample_ohlcv(load_klines(cfg, source), timeframe, source)
    write_parquet_atomic(bars, cfg.bars_path(timeframe))
    return bars.height
