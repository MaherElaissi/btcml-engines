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
