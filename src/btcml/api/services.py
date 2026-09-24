"""Read-only queries behind the API. Nothing here writes to data/."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl

from ..config import INTERVAL_SECONDS, Config
from ..data.quality import check
from ..data.storage import kline_files

RAW_INTERVALS = ("1s", "1m")  # downloaded; the others are built from 1m
NICE_BUCKETS = (1, 5, 15, 30, 60, 300, 600, 900, 1800, 3600, 4 * 3600, 86400, 7 * 86400)


def _files(cfg: Config, interval: str) -> list[str]:
    if interval in RAW_INTERVALS:
        return kline_files(cfg, interval)
    path = cfg.bars_path(interval)
    return [str(path)] if path.exists() else []


def _missing(interval: str) -> FileNotFoundError:
    hint = "btcml download" if interval in RAW_INTERVALS else "btcml build-bars"
    return FileNotFoundError(f"No {interval} data yet. Run: {hint}")


# ---- candles -----------------------------------------------------------------


def coverage(cfg: Config) -> list[dict]:
    now = datetime.now(timezone.utc)
    out = []
    for interval in INTERVAL_SECONDS:
        files = _files(cfg, interval)
        row = {"interval": interval, "available": bool(files), "files": len(files)}
        if files:
            stats = (
                pl.scan_parquet(files)
                .select(
                    pl.col("open_time").min().alias("first"),
                    pl.col("open_time").max().alias("last"),
                    pl.len().alias("stored_rows"),
                )
                .collect()
                .row(0, named=True)
            )
            row |= stats
            row["size_bytes"] = sum(Path(f).stat().st_size for f in files)
            candle_end = stats["last"] + timedelta(seconds=INTERVAL_SECONDS[interval])
            row["lag_seconds"] = max(0, int((now - candle_end).total_seconds()))
        out.append(row)
    return out


_quality_cache: dict[str, tuple[tuple, dict]] = {}


def quality(cfg: Config, interval: str) -> dict:
    """The `btcml check` report, cached until the underlying files change (1s is slow)."""
    files = _files(cfg, interval)
    if not files:
        raise _missing(interval)
    key = tuple((f, Path(f).stat().st_mtime_ns) for f in files)
    cached = _quality_cache.get(interval)
    if cached and cached[0] == key:
        return cached[1]
    report = check(cfg, interval)
    _quality_cache[interval] = (key, report)
    return report


def pick_bucket(step: int, span: float, max_points: int) -> int:
    """Smallest 'nice' bucket (a multiple of step) that keeps span/bucket <= max_points."""
    need = max(step, span / max_points)
    for b in NICE_BUCKETS:
        if b >= need and b % step == 0:
            return b
    return NICE_BUCKETS[-1]


def candles(
    cfg: Config,
    interval: str,
    start: datetime | None,
    end: datetime | None,
    max_points: int,
) -> dict:
    """OHLCV in [start, end), downsampled to at most ~max_points buckets.

    With no start, returns the latest window of max_points candles.
    """
    files = _files(cfg, interval)
    if not files:
        raise _missing(interval)
    step = INTERVAL_SECONDS[interval]
    lf = pl.scan_parquet(files)
    if end is None:
        last = lf.select(pl.col("open_time").max()).collect().item()
        end = last + timedelta(seconds=step)
    if start is None:
        start = end - timedelta(seconds=step * max_points)
    bucket = pick_bucket(step, (end - start).total_seconds(), max_points)

    df = (
        lf.filter((pl.col("open_time") >= start) & (pl.col("open_time") < end))
        .unique("open_time", keep="last")
        .sort("open_time")
        .collect()
    )
    if bucket > step and df.height:
        df = df.group_by_dynamic("open_time", every=f"{bucket}s").agg(
            pl.col("open").first(),
            pl.col("high").max(),
            pl.col("low").min(),
            pl.col("close").last(),
            pl.col("volume").sum(),
        )
    return {
        "interval": interval,
        "bucket_seconds": bucket,
        "start": start,
        "end": end,
        "t": (df["open_time"].dt.epoch("s")).to_list(),
        **{k: df[k].to_list() for k in ("open", "high", "low", "close", "volume")},
    }


# ---- news --------------------------------------------------------------------


def _news_conn(cfg: Config) -> sqlite3.Connection | None:
    if not cfg.news_db.exists():
        return None
    conn = sqlite3.connect(f"file:{cfg.news_db.as_posix()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _query(conn: sqlite3.Connection | None, sql: str, params=()) -> list[dict]:
    if conn is None:
        return []
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    except sqlite3.OperationalError:  # table missing (db from an older collector)
        return []


def news_feeds(cfg: Config) -> list[dict]:
    """Per configured feed: article counts and the collector heartbeat."""
    conn = _news_conn(cfg)
    try:
        counts = {
            r["source"]: r
            for r in _query(
                conn,
                """SELECT source, COUNT(*) AS articles,
                          SUM(backfill = 0) AS live_articles,
                          MAX(first_seen_at) AS last_article_at
                   FROM articles GROUP BY source""",
            )
        }
        status = {r["source"]: r for r in _query(conn, "SELECT * FROM feed_status")}
    finally:
        if conn:
            conn.close()

    stale_after = timedelta(seconds=3 * cfg.poll_seconds)
    now = datetime.now(timezone.utc)
    out = []
    names = [f.name for f in cfg.feeds] + sorted(set(counts) - {f.name for f in cfg.feeds})
    for name in names:
        c, s = counts.get(name, {}), status.get(name, {})
        last_ok = s.get("last_ok_at")
        out.append(
            {
                "source": name,
                "configured": name in {f.name for f in cfg.feeds},
                "articles": c.get("articles", 0),
                "live_articles": c.get("live_articles") or 0,
                "last_article_at": c.get("last_article_at"),
                "last_poll_at": s.get("last_poll_at"),
                "last_ok_at": last_ok,
                "last_new": s.get("last_new"),
                "last_error": s.get("last_error"),
                "healthy": bool(last_ok)
                and now - datetime.fromisoformat(last_ok) <= stale_after,
            }
        )
    return out


def news_latest(cfg: Config, limit: int, source: str | None) -> list[dict]:
    conn = _news_conn(cfg)
    sql = """SELECT source, title, link, published_at, first_seen_at, backfill
             FROM articles {where} ORDER BY first_seen_at DESC, published_at DESC LIMIT ?"""
    try:
        if source:
            return _query(conn, sql.format(where="WHERE source = ?"), (source, limit))
        return _query(conn, sql.format(where=""), (limit,))
    finally:
        if conn:
            conn.close()


def news_volume(cfg: Config, hours: int) -> list[dict]:
    """Live (non-backfill) articles per UTC hour over the last `hours` hours."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(
        timespec="milliseconds"
    )
    conn = _news_conn(cfg)
    try:
        rows = _query(
            conn,
            """SELECT substr(first_seen_at, 1, 13) AS hour, COUNT(*) AS articles
               FROM articles WHERE backfill = 0 AND first_seen_at >= ?
               GROUP BY hour ORDER BY hour""",
            (since,),
        )
    finally:
        if conn:
            conn.close()
    return [
        {"hour": datetime.fromisoformat(r["hour"] + ":00:00+00:00"), "articles": r["articles"]}
        for r in rows
    ]
