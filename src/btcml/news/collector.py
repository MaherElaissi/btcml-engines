"""RSS collector. Stores every article with our own first_seen_at timestamp.

Feed publish times are unreliable and RSS has no history, so first_seen_at is
the only time we can trust when later asking "did this news exist before candle X?".
Articles found on a feed's very first poll, or published more than a day before
we saw them, are flagged backfill=1: their first_seen_at must not be used as a
news timestamp.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from ..config import Config, Feed

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(days=1)

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    title         TEXT NOT NULL,
    summary       TEXT,
    link          TEXT,
    published_at  TEXT,
    first_seen_at TEXT NOT NULL,
    backfill      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_seen ON articles(first_seen_at);
"""


def open_db(cfg: Config) -> sqlite3.Connection:
    cfg.news_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cfg.news_db)
    conn.executescript(SCHEMA)
    return conn


def _iso(struct) -> str | None:
    if not struct:
        return None
    return datetime(*struct[:6], tzinfo=timezone.utc).isoformat()


def poll_feed(conn: sqlite3.Connection, client: httpx.Client, feed: Feed) -> int:
    """Fetch one feed and store new articles. Returns the number of new articles."""
    resp = client.get(feed.url)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    seen_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    is_first = (
        conn.execute("SELECT COUNT(*) FROM articles WHERE source = ?", (feed.name,)).fetchone()[0]
        == 0
    )
    now = datetime.fromisoformat(seen_at)
    new = 0
    for e in parsed.entries:
        key = e.get("id") or e.get("link") or e.get("title")
        title = e.get("title")
        if not key or not title:
            continue
        published = _iso(e.get("published_parsed") or e.get("updated_parsed"))
        # Some feeds resurface old posts as new entries; those are not fresh news either.
        stale = published is not None and now - datetime.fromisoformat(published) > STALE_AFTER
        cur = conn.execute(
            "INSERT OR IGNORE INTO articles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                hashlib.sha1(f"{feed.name}|{key}".encode()).hexdigest(),
                feed.name,
                title,
                (e.get("summary") or "")[:2000],
                e.get("link"),
                published,
                seen_at,
                int(is_first or stale),
            ),
        )
        new += cur.rowcount
    conn.commit()
    return new


def run(cfg: Config, once: bool = False) -> None:
    conn = open_db(cfg)
    with httpx.Client(
        timeout=20.0, follow_redirects=True, headers={"User-Agent": "btcml-news/0.1"}
    ) as client:
        while True:
            for feed in cfg.feeds:
                try:
                    n = poll_feed(conn, client, feed)
                    log.info("%s: %d new", feed.name, n)
                except Exception as exc:  # one broken feed must not stop the others
                    log.warning("%s: failed (%s)", feed.name, exc)
            if once:
                return
            time.sleep(cfg.poll_seconds)
