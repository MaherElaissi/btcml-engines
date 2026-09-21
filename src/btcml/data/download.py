"""Historical kline download from the Binance public archive, plus REST gap-fill.

Layout written under data/raw/klines/<interval>/:
  monthly/  one file per full month (from the archive)
  daily/    one file per day (current month, or months without a monthly file)
  api/      a single "tail" file: candles from the last archived one up to now (REST)
Files may overlap; load_klines() deduplicates.
"""

from __future__ import annotations

import hashlib
import io
import logging
import time
import zipfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import httpx
import polars as pl

from ..config import INTERVAL_SECONDS, Config
from .storage import SCHEMA_COLUMNS, kline_files, write_parquet_atomic

log = logging.getLogger(__name__)

CSV_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trades",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
]


def normalize_klines(df: pl.DataFrame) -> pl.DataFrame:
    """Raw 12-column kline frame -> typed frame with a UTC open_time.

    Binance spot archives switched from millisecond to microsecond timestamps
    in 2025, so the unit is detected from the magnitude.
    """
    unit = "us" if df["open_time"].max() > 10**14 else "ms"
    return (
        df.select(
            pl.from_epoch("open_time", time_unit=unit)
            .dt.cast_time_unit("ms")
            .dt.replace_time_zone("UTC")
            .alias("open_time"),
            *[
                pl.col(c).cast(pl.Float64)
                for c in ("open", "high", "low", "close", "volume", "quote_volume")
            ],
            pl.col("trades").cast(pl.Int64),
            pl.col("taker_buy_base").cast(pl.Float64),
            pl.col("taker_buy_quote").cast(pl.Float64),
        )
        .sort("open_time")
        .unique("open_time", keep="first", maintain_order=True)
    )


def parse_klines_csv(data: bytes) -> pl.DataFrame:
    has_header = not data[:1].isdigit()  # newer files sometimes carry a header row
    df = pl.read_csv(io.BytesIO(data), has_header=has_header, new_columns=CSV_COLUMNS)
    return normalize_klines(df)


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(60.0),
        follow_redirects=True,
        transport=httpx.HTTPTransport(retries=3),
        headers={"User-Agent": "btcml/0.1"},
    )


def _get(client: httpx.Client, url: str) -> bytes | None:
    r = client.get(url)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.content


def _fetch_zip(client: httpx.Client, url: str) -> pl.DataFrame | None:
    blob = _get(client, url)
    if blob is None:
        return None
    checksum = _get(client, url + ".CHECKSUM")
    if checksum is None:
        log.warning("no checksum file for %s", url)
    else:
        expected = checksum.decode().split()[0].lower()
        if hashlib.sha256(blob).hexdigest() != expected:
            raise ValueError(f"checksum mismatch for {url}")
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return parse_klines_csv(zf.read(zf.namelist()[0]))


def months_back(n: int, today: date) -> list[tuple[int, int]]:
    """The last n calendar months ending with the current one, oldest first."""
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def _archive_url(cfg: Config, kind: str, interval: str, stamp: str) -> str:
    name = f"{cfg.symbol}-{interval}-{stamp}.zip"
    return (
        f"{cfg.archive_url}/data/spot/{kind}/klines/{cfg.symbol}/{interval}/{name}"
    )


def _download_days(client, cfg, interval, y, m, today, stats: Counter) -> None:
    out_dir = cfg.klines_dir(interval) / "daily"
    day = date(y, m, 1)
    while day.month == m and day < today:  # today's file is not published yet
        target = out_dir / f"{cfg.symbol}-{interval}-{day.isoformat()}.parquet"
        if target.exists():
            stats["cached"] += 1
        else:
            df = _fetch_zip(
                client, _archive_url(cfg, "daily", interval, day.isoformat())
            )
            if df is None:
                stats["missing"] += 1
                log.debug("missing daily file %s", day)
            else:
                write_parquet_atomic(df, target)
                stats["downloaded"] += 1
        day += timedelta(days=1)


def download_interval(cfg: Config, interval: str, months: int) -> Counter:
    today = datetime.now(timezone.utc).date()
    out_dir = cfg.klines_dir(interval) / "monthly"
    stats: Counter = Counter()
    with _client() as client:
        for y, m in months_back(months + 1, today):
            stamp = f"{y:04d}-{m:02d}"
            is_current = (y, m) == (today.year, today.month)
            if not is_current:
                target = out_dir / f"{cfg.symbol}-{interval}-{stamp}.parquet"
                if target.exists():
                    stats["cached"] += 1
                    continue
                df = _fetch_zip(client, _archive_url(cfg, "monthly", interval, stamp))
                if df is not None:
                    write_parquet_atomic(df, target)
                    stats["downloaded"] += 1
                    log.info("%s %s: %d rows", interval, stamp, df.height)
                    continue
                log.warning("%s %s: no monthly file, trying daily files", interval, stamp)
            _download_days(client, cfg, interval, y, m, today, stats)
            log.info("%s %s: daily files done", interval, stamp)
    return stats


def fill_recent(cfg: Config, interval: str) -> int:
    """Fetch candles newer than the archive from the REST API. Returns rows written."""
    archived = kline_files(cfg, interval, archive_only=True)
    if not archived:
        raise SystemExit(f"No archived {interval} data yet. Run: btcml download")
    last = pl.scan_parquet(archived).select(pl.col("open_time").max()).collect().item()
    step_ms = INTERVAL_SECONDS[interval] * 1000
    start = int(last.timestamp() * 1000) + step_ms
    now_ms = int(time.time() * 1000)

    rows: list[list] = []
    with _client() as client:
        while start + step_ms <= now_ms:
            r = client.get(
                f"{cfg.api_url}/api/v3/klines",
                params={
                    "symbol": cfg.symbol,
                    "interval": interval,
                    "startTime": start,
                    "limit": 1000,
                },
            )
            r.raise_for_status()
            batch = r.json()
            if batch:
                rows.extend(batch)
                start = batch[-1][0] + step_ms
            else:  # no trades in this window; skip ahead
                start += step_ms * 1000
            time.sleep(0.1)

    rows = [r for r in rows if r[0] + step_ms <= now_ms]  # closed candles only
    target = cfg.klines_dir(interval) / "api" / f"{cfg.symbol}-{interval}-tail.parquet"
    if not rows:
        target.unlink(missing_ok=True)
        return 0
    df = normalize_klines(pl.DataFrame(rows, schema=CSV_COLUMNS, orient="row"))
    assert df.columns == SCHEMA_COLUMNS
    write_parquet_atomic(df, target)
    return df.height
