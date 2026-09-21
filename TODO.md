# Roadmap / TODO

Multi-timeframe BTC/USDT prediction: one engine per timeframe (1s, 1m, 5m, 10m, 1h), stacked
into a meta-engine, with RSS news features. Everything runs locally (CPU, 8 GB RAM), so heavy
work is chunked and Parquet is scanned lazily.

**Design rules (apply to every phase)**
- Predict returns / direction probabilities, not prices. Include a no-trade zone for tiny moves.
- No leakage: a fast engine may only use higher-timeframe candles that have already **closed**.
- Validate with walk-forward splits (train on the past, test on the next block). Never random splits.
- Every model must beat the naive baseline ("next candle = same direction as last") out of sample,
  and results are only meaningful **after fees and slippage** (about 0.1% per trade).
- Keep out-of-fold predictions for every engine; the meta-engine trains only on those.

---

## Phase 0: Data foundation (done)
- [x] Project skeleton (uv, Python 3.12, polars, duckdb), config, CLI
- [x] Binance archive downloader (checksums, monthly/daily fallback, ms/us timestamps)
- [x] REST gap-fill for candles newer than the archive
- [x] Data quality report (`btcml check`)
- [x] 5m / 10m / 1h bars built from 1m (`btcml build-bars`)
- [x] RSS news collector with `first_seen_at` and backfill flag
- [x] Historical data downloaded: 1m (24 months), 1s (4 months), all checks clean
- [x] Unit tests, pushed to GitHub
- [ ] Run the news collector 24/7 (Windows scheduled task, or an always-on machine) so history accumulates
- [ ] Decide how to sync/export `news.db` across machines

## Phase 1: First engine (1m)
- [ ] Add dependencies: lightgbm, scikit-learn
- [ ] Shared feature library (`btcml/features/`): log returns at several lags, rolling volatility,
      RSI, MACD, ATR, volume z-score, taker-buy ratio, trade count, time-of-day / day-of-week
- [ ] Target definition: next-candle log return, direction label with neutral zone
- [ ] Walk-forward validation framework (`btcml/validation/`), reusable by all engines
- [ ] Naive baselines (last-direction, always-up, zero-return) for comparison
- [ ] LightGBM classifier + regressor, out-of-fold predictions saved to Parquet
- [ ] Metrics: directional accuracy, log loss, calibration, information coefficient, per-period stability
- [ ] Feature importance review; drop features that only add noise
- [ ] MLflow local tracking for runs and parameters
- [ ] Gate: beats the naive baseline out of sample across most walk-forward folds, else revisit features

## Phase 2: Backtester
- [ ] Simple event-driven backtester on candle data (`btcml/backtest/`)
- [ ] Fees (maker/taker) and slippage model, configurable
- [ ] Position sizing from predicted probability / confidence threshold
- [ ] Metrics: net return, Sharpe, max drawdown, win rate, turnover, trade count
- [ ] Report comparing each engine and the baselines after costs
- [ ] Sanity checks: shuffled-label test and shifted-feature test must destroy the edge

## Phase 3: Remaining engines
- [ ] 1h engine (LightGBM; add trend / volatility regime features, day-of-week)
- [ ] 10m engine
- [ ] 5m engine
- [ ] 1s engine (rolling windows of 60-300 s, micro-volatility; optionally the order-book depth stream)
- [ ] Sequence models (GRU / TCN in PyTorch, CPU) where they beat LightGBM
- [ ] Regime labels (trending/ranging, high/low volatility) exported by the slower engines
- [ ] Per-engine reports using the shared validation and backtest tools

## Phase 4: Ensemble (meta-engine)
- [ ] Top-down context: 1h -> 10m -> 5m -> 1m -> 1s, using closed higher-timeframe candles only
- [ ] Leakage test: assert no feature timestamp is later than the prediction time
- [ ] Out-of-fold predictions from each engine as inputs to the meta-model
- [ ] Meta-model: start with logistic regression / small LightGBM; add confidence weighting per regime
- [ ] Compare ensemble vs best single engine (accuracy, log loss, backtest after costs)
- [ ] Ablation: which engines actually contribute

## Phase 5: News features
- [ ] Article cleaning (strip HTML, dedupe near-identical headlines)
- [ ] FinBERT sentiment scoring, run locally in batches
- [ ] Keyword / event tags (ETF, hack, regulation, Fed, rate decision, liquidation)
- [ ] Rolling news features (sentiment and article count over 15 min / 1 h / 4 h, spike detection)
- [ ] Use `first_seen_at` for non-backfill articles only; use published time carefully for historical backfill
- [ ] Optional historical backfill (e.g. GDELT) so news has more than a few weeks of data
- [ ] Test whether news improves out-of-sample results for 5m / 10m / 1h engines and the meta-engine

## Phase 6: Live mode (paper trading only)
- [ ] Binance WebSocket ingestion (`btcusdt@kline_1s`, `kline_1m`), reconnect and gap-repair logic
- [ ] Online feature computation identical to training code (one shared implementation)
- [ ] Live prediction loop for every engine and the meta-engine, predictions logged to disk
- [ ] Compare live vs backtest results to detect drift or leakage
- [ ] Scheduled retraining (walk-forward, e.g. weekly) with model versioning
- [ ] Simple dashboard for predictions vs outcomes
- [ ] Decision point: consider real trading only after a long, consistent paper-trading record

## Later / ideas
- [ ] Order-book depth and trade-flow features (largest expected gain for the 1s engine)
- [ ] Funding rate, open interest, liquidation data for the 1h engine
- [ ] Other symbols (ETH/USDT) to test whether the approach generalises
- [ ] Hardware upgrade (GPU) if sequence models become the bottleneck
- [ ] Switch the repo to private once real results / trading logic are added
