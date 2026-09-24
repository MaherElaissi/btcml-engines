# btcml

Multi-timeframe BTC/USDT prediction engines (1s, 1m, 5m, 10m, 1h) combined by a meta-engine.

See [TODO.md](TODO.md) for the full roadmap and progress.

## Phase 0: data foundation

```
uv sync
uv run pytest                       # unit tests
uv run btcml download --interval 1m # ~24 months of 1m candles
uv run btcml download --interval 1s # ~4 months of 1s candles (large)
uv run btcml fill-recent --interval 1m
uv run btcml check --interval 1m    # data quality report
uv run btcml build-bars             # 5m, 10m, 1h from 1m
uv run btcml news                   # RSS collector (leave running)
```

Data lives in `data/` (git-ignored): raw klines as Parquet, resampled bars, and
`data/news/news.db` (SQLite). Settings are in `config.toml`.

## Dashboard API

```
uv run btcml serve                  # read-only API on http://127.0.0.1:8000
uv run btcml openapi                # regenerate openapi.json after changing the API
```

Interactive docs at http://127.0.0.1:8000/docs. The dashboard (separate repo) generates its typed
client from `openapi.json`. Routes live under `/api/v1`:

| Route | What |
|---|---|
| `GET /health` | liveness (never needs a token) |
| `GET /status` | roadmap progress per section, parsed from TODO.md |
| `GET /data/coverage` | per interval: files, first/last candle, lag behind now |
| `GET /data/{interval}/quality` | the `btcml check` report (cached until the files change) |
| `GET /data/{interval}/candles` | OHLCV for `start`/`end`, downsampled to `max_points`; latest window by default |
| `GET /news/feeds` | article counts and collector heartbeat per feed |
| `GET /news/latest` | newest articles (`limit`, `source`) |
| `GET /news/volume` | live articles per hour (`hours`) |

Allowed browser origins are `[api] cors_origins` in `config.toml`. Set `BTCML_API_TOKEN` to require
`Authorization: Bearer <token>` (or `?token=`); `serve` refuses a non-localhost host without it.
Restart `btcml news` after upgrading so it records the per-feed heartbeat.

### Verifying Phase 0

1. `uv run pytest` passes.
2. `uv run btcml check --interval 1m` (and `1s`): missing candles well under 1%, 0 bad OHLC rows,
   0 negative volume. Binance maintenance windows show up as a few known gaps.
3. `uv run btcml build-bars`, then `btcml check --interval 5m|10m|1h`: 0 missing bars, and
   "bars built from incomplete 1m data" is small (those buckets sit on the 1m gaps).
4. `uv run btcml fill-recent --interval 1m`, then `check` again: `last` should be within a
   couple of minutes of now.
5. `uv run btcml news --once`: each feed logs `N new` (a failing feed logs a warning). Later,
   `sqlite3 data/news/news.db "select source, backfill, count(*) from articles group by 1, 2"`
   should show `backfill=0` rows growing while the collector runs.
