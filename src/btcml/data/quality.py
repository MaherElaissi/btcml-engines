from __future__ import annotations

from datetime import timedelta

import polars as pl

from ..config import INTERVAL_SECONDS, Config
from .storage import kline_files, load_klines


def analyze(df: pl.DataFrame, interval: str, raw_rows: int | None = None) -> dict:
    """Quality metrics for a deduplicated, sorted candle frame."""
    secs = INTERVAL_SECONDS[interval]
    step = timedelta(seconds=secs)
    first, last = df["open_time"][0], df["open_time"][-1]
    expected = int((last - first).total_seconds() // secs) + 1

    gaps = (
        df.select("open_time", pl.col("open_time").diff().alias("d"))
        .filter(pl.col("d") > step)
        .select(
            (pl.col("open_time") - pl.col("d") + step).alias("gap_start"),
            (pl.col("d").dt.total_seconds() // secs - 1).alias("missing"),
        )
        .sort("missing", descending=True)
    )
    o, h, l, c = (pl.col(x) for x in ("open", "high", "low", "close"))
    bad_ohlc = df.filter(
        (h < l) | (h < o) | (h < c) | (l > o) | (l > c) | (l <= 0)
    ).height

    return {
        "interval": interval,
        "rows": df.height,
        "first": first,
        "last": last,
        "duplicates_removed": (raw_rows - df.height) if raw_rows is not None else None,
        "expected_rows": expected,
        "missing_rows": expected - df.height,
        "missing_pct": 100 * (expected - df.height) / expected,
        "gap_count": gaps.height,
        "largest_gaps": gaps.head(5).to_dicts(),
        "bad_ohlc_rows": bad_ohlc,
        "negative_volume_rows": df.filter(pl.col("volume") < 0).height,
        "zero_volume_pct": 100 * df.filter(pl.col("volume") == 0).height / df.height,
    }


def check(cfg: Config, interval: str) -> dict:
    raw_rows = (
        pl.scan_parquet(kline_files(cfg, interval)).select(pl.len()).collect().item()
    )
    return analyze(load_klines(cfg, interval), interval, raw_rows)


def format_report(r: dict) -> str:
    lines = [
        f"== {r['interval']} ==",
        f"rows: {r['rows']:,}   range: {r['first']} -> {r['last']}",
        f"duplicates removed: {r['duplicates_removed']}",
        f"missing candles: {r['missing_rows']:,} of {r['expected_rows']:,} "
        f"({r['missing_pct']:.3f}%) in {r['gap_count']} gaps",
        f"bad OHLC rows: {r['bad_ohlc_rows']}   negative volume: {r['negative_volume_rows']}"
        f"   zero-volume: {r['zero_volume_pct']:.2f}%",
    ]
    for g in r["largest_gaps"]:
        lines.append(f"  gap at {g['gap_start']}: {g['missing']:,} candles")
    if r["interval"] == "1s":
        lines.append(
            "note: seconds without trades appear as zero-volume flat candles, "
            "so a high zero-volume % is normal at 1s."
        )
    elif r["missing_pct"] > 1:
        lines.append("WARNING: more than 1% of candles are missing")
    return "\n".join(lines)
