from datetime import datetime, timedelta, timezone

import polars as pl

from btcml.data.download import months_back, parse_klines_csv
from btcml.data.quality import analyze
from btcml.data.resample import resample_ohlcv

T0_MS = 1_700_000_000_000  # 2023-11-14 22:13:20 UTC


def _csv(open_time: int, rows: int = 3, step: int = 60_000) -> bytes:
    return "\n".join(
        f"{open_time + i * step},100.0,101.0,99.0,100.5,1.5,{open_time + i * step + step - 1},150.0,10,0.7,70.0,0"
        for i in range(rows)
    ).encode()


def test_parse_milliseconds():
    df = parse_klines_csv(_csv(T0_MS))
    assert df.height == 3
    assert df["open_time"][0] == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


def test_parse_microseconds_matches_milliseconds():
    ms = parse_klines_csv(_csv(T0_MS))
    us = parse_klines_csv(_csv(T0_MS * 1000, step=60_000_000))
    assert ms["open_time"].to_list() == us["open_time"].to_list()


def test_parse_with_header_row():
    header = b"open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"
    assert parse_klines_csv(header + _csv(T0_MS)).height == 3


def test_months_back():
    from datetime import date

    assert months_back(3, date(2026, 1, 15)) == [(2025, 11), (2025, 12), (2026, 1)]


def _minutes(n: int, start=datetime(2024, 1, 1, tzinfo=timezone.utc)) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "open_time": [start + timedelta(minutes=i) for i in range(n)],
            "open": [100.0 + i for i in range(n)],
            "high": [102.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [100.5 + i for i in range(n)],
            "volume": [1.0] * n,
            "quote_volume": [10.0] * n,
            "trades": [5] * n,
            "taker_buy_base": [0.5] * n,
            "taker_buy_quote": [5.0] * n,
        }
    ).with_columns(pl.col("open_time").dt.cast_time_unit("ms"))


def test_resample_5m_drops_partial_last_bucket():
    out = resample_ohlcv(_minutes(12), "5m", "1m")  # 12 minutes -> 2 full buckets
    assert out.height == 2
    first = out.row(0, named=True)
    assert first["open"] == 100.0 and first["close"] == 104.5
    assert first["high"] == 106.0 and first["low"] == 99.0
    assert first["volume"] == 5.0 and first["n_src"] == 5


def test_analyze_finds_gap():
    df = _minutes(10).filter(pl.col("open_time") != datetime(2024, 1, 1, 0, 4, tzinfo=timezone.utc))
    r = analyze(df, "1m")
    assert r["missing_rows"] == 1
    assert r["gap_count"] == 1
    assert r["largest_gaps"][0]["missing"] == 1
    assert r["bad_ohlc_rows"] == 0


def test_check_reads_built_bars(tmp_path):
    from btcml.config import load_config
    from btcml.data.quality import check
    from btcml.data.storage import write_parquet_atomic

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        'symbol = "BTCUSDT"\ndata_dir = "data"\n[binance]\narchive_url = "a"\napi_url = "b"\n'
        '[history]\n"1m" = 1\n[news]\npoll_seconds = 1\nfeeds = []\n'
    )
    cfg = load_config(cfg_path)
    write_parquet_atomic(resample_ohlcv(_minutes(60), "5m", "1m"), cfg.bars_path("5m"))
    r = check(cfg, "5m")
    assert r["rows"] == 12 and r["missing_rows"] == 0 and r["incomplete_bars"] == 0
