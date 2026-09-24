import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import polars as pl
import pytest
from fastapi.testclient import TestClient

from btcml.api import create_app
from btcml.api.progress import parse_roadmap
from btcml.api.services import pick_bucket
from btcml.config import Feed, load_config
from btcml.data.resample import resample_ohlcv
from btcml.data.storage import write_parquet_atomic
from btcml.news.collector import open_db, poll_feed, record_poll

ROOT = Path(__file__).resolve().parents[1]


def _minutes(n: int) -> pl.DataFrame:
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return pl.DataFrame(
        {
            "open_time": [t0 + timedelta(minutes=i) for i in range(n)],
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


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "config.toml").write_text(
        'symbol = "BTCUSDT"\ndata_dir = "data"\n[binance]\narchive_url = "a"\napi_url = "b"\n'
        '[history]\n"1m" = 1\n[news]\npoll_seconds = 60\n'
        '[[news.feeds]]\nname = "feed_a"\nurl = "http://a"\n'
    )
    (tmp_path / "TODO.md").write_text("# T\n## Phase 0\n- [x] one\n- [ ] two\n")
    return load_config(tmp_path / "config.toml")


@pytest.fixture
def client(cfg, monkeypatch):
    monkeypatch.delenv("BTCML_API_TOKEN", raising=False)
    return TestClient(create_app(cfg))


def _with_candles(cfg, n=600):
    m1 = _minutes(n)
    write_parquet_atomic(m1, cfg.klines_dir("1m") / "monthly" / "BTCUSDT-1m-2024-01.parquet")
    write_parquet_atomic(resample_ohlcv(m1, "5m", "1m"), cfg.bars_path("5m"))


def test_parse_roadmap_groups_and_wrapped_lines():
    text = (
        "intro\n## Phase A (done)\n- [x] a\n- [X] b\n## Track\n**API side**\n"
        "- [ ] c that\n      wraps\n\n**Frontend**\n- [x] d\n## Empty\ntext\n"
    )
    a, track = parse_roadmap(text)
    assert (a["status"], a["done"], a["total"]) == ("done", 2, 2)
    assert track["status"] == "in_progress"
    assert track["items"][0] == {"text": "c that wraps", "done": False, "group": "API side"}
    assert track["items"][1]["group"] == "Frontend"


def test_repo_roadmap_parses():
    titles = [s["title"] for s in parse_roadmap((ROOT / "TODO.md").read_text(encoding="utf-8"))]
    assert titles[0].startswith("Phase 0") and any("Dashboard" in t for t in titles)


def test_pick_bucket():
    assert pick_bucket(60, 600 * 60, 1000) == 60  # fits: no downsampling
    assert pick_bucket(60, 86400 * 30, 1000) == 3600
    assert pick_bucket(600, 3600 * 10, 20) == 1800  # multiple of 10m, not 900s
    assert pick_bucket(1, 10**9, 10) == 7 * 86400


def test_health_and_status(client):
    assert client.get("/api/v1/health").json()["status"] == "ok"
    s = client.get("/api/v1/status").json()
    assert (s["done"], s["total"]) == (1, 2)
    assert s["sections"][0]["status"] == "in_progress"


def test_no_data_yet(client):
    cov = client.get("/api/v1/data/coverage").json()
    assert [c["available"] for c in cov] == [False] * 5
    r = client.get("/api/v1/data/1m/quality")
    assert r.status_code == 404 and "btcml download" in r.json()["detail"]
    assert "build-bars" in client.get("/api/v1/data/5m/candles").json()["detail"]
    assert client.get("/api/v1/news/feeds").json()[0]["healthy"] is False
    assert client.get("/api/v1/news/latest").json() == []


def test_quality_and_coverage(cfg, client):
    _with_candles(cfg)
    q = client.get("/api/v1/data/1m/quality").json()
    assert q["rows"] == 600 and q["missing_rows"] == 0
    q5 = client.get("/api/v1/data/5m/quality").json()
    assert q5["rows"] == 120 and q5["incomplete_bars"] == 0
    cov = {c["interval"]: c for c in client.get("/api/v1/data/coverage").json()}
    assert cov["1m"]["stored_rows"] == 600 and not cov["1s"]["available"]


def test_candles_window_and_downsampling(cfg, client):
    _with_candles(cfg)
    latest = client.get("/api/v1/data/1m/candles", params={"max_points": 10}).json()
    assert latest["bucket_seconds"] == 60 and len(latest["t"]) == 10
    assert latest["close"][-1] == 100.5 + 599

    r = client.get(
        "/api/v1/data/1m/candles",
        params={"start": "2024-01-01T00:00:00", "end": "2024-01-01T10:00:00", "max_points": 20},
    ).json()
    assert r["bucket_seconds"] == 1800 and len(r["t"]) == 20
    assert r["open"][0] == 100.0 and r["close"][0] == 100.5 + 29
    assert r["high"][0] == 102.0 + 29 and r["volume"][0] == 30.0
    assert r["t"][1] - r["t"][0] == 1800

    bad = client.get(
        "/api/v1/data/1m/candles", params={"start": "2024-01-02", "end": "2024-01-01"}
    )
    assert bad.status_code == 422


def test_news_endpoints(cfg, client):
    rss = (
        b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
        b"<item><title>BTC up</title><link>http://x/1</link><guid>1</guid></item>"
        b"</channel></rss>"
    )
    conn = open_db(cfg)
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=rss)))
    record_poll(conn, "feed_a", poll_feed(conn, http, Feed("feed_a", "http://a")))
    record_poll(conn, "feed_a", None, "boom")  # a failure keeps the last ok time
    conn.execute("UPDATE articles SET backfill = 0")
    conn.commit()

    (feed,) = client.get("/api/v1/news/feeds").json()
    assert (feed["articles"], feed["live_articles"]) == (1, 1)
    assert feed["last_error"] == "boom" and feed["last_ok_at"] and feed["healthy"]
    latest = client.get("/api/v1/news/latest", params={"source": "feed_a"}).json()
    assert latest[0]["title"] == "BTC up"
    assert client.get("/api/v1/news/latest", params={"source": "nope"}).json() == []
    assert client.get("/api/v1/news/volume").json()[0]["articles"] == 1


def test_token(client, monkeypatch):
    monkeypatch.setenv("BTCML_API_TOKEN", "s3cret")
    assert client.get("/api/v1/health").status_code == 200  # stays public
    assert client.get("/api/v1/status").status_code == 401
    bad = client.get("/api/v1/status", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401
    good = client.get("/api/v1/status", headers={"Authorization": "Bearer s3cret"})
    assert good.status_code == 200
    assert client.get("/api/v1/status", params={"token": "s3cret"}).status_code == 200


def test_committed_openapi_is_current():
    spec = create_app(load_config()).openapi()
    committed = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))
    assert committed == spec, "run: uv run btcml openapi"
