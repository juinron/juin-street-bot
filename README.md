# Juin Street Bot

An autonomous Python trading bot for the [Roostoo](https://roostoo.com) mock exchange. Runs a **mean-reversion strategy** on 9 crypto pairs using Bollinger Bands, RSI Z-score, and a long-term trend filter.

## Strategy

The bot trades **BTC, ETH, SOL, BNB, XRP, LINK, FET, TAO, ADA** against USD on a **15-minute loop**:

1. **Bootstrap** historical candle data from Binance on startup
2. **Collect** live ticker prices each cycle and append to local price history
3. **Resample** raw ticks to 15-minute candles for indicator calculation
4. **Evaluate** BUY/SELL/HOLD for each pair using:
   - **RSI Z-score**: signals when RSI deviates > 1.5σ from its 20-period rolling mean
   - **Bollinger Bands** (20-period SMA ± 2 std devs): confirms price is below mid for buys
   - **Trend filter** (200-period SMA): blocks buys when price is below the long-term trend
   - **ATR stop-loss**: exits a position if price falls > 6× ATR below entry
5. **Size** orders using tiered fixed-fractional allocation (5–10% per signal, 20% max per asset)
6. **Execute** limit orders quoted inside the bid-ask spread (maker execution)

### Signal Logic

| Signal | Conditions |
|---|---|
| **BUY** | RSI Z-score < −1.5 AND price < BB mid AND price > 200-SMA (trend filter) |
| **TAKE PROFIT** | RSI Z-score > +1.5 AND price > BB mid AND price > entry price |
| **STOP-LOSS** | price < entry − (6 × ATR) |

### Risk Management

| Guard | Trigger | Action |
|---|---|---|
| **ATR stop-loss** | Price falls > 6× 15m ATR below entry | Immediate market sell |
| **Stop-loss cooldown** | After any stop-loss exit | Block re-entry for 4 hours AND until price recovers 1% above stop level |
| **Circuit breaker** | Portfolio down 10% from starting value | Pause all buys |
| **Daily loss limit** | Down 5% vs yesterday's close | No new buys until midnight UTC |
| **Max drawdown halt** | 15% below portfolio peak | Halt all trading; manual restart required |

## Files

| File | Description |
|---|---|
| `trades_log.csv` | Every trade, cancellation, and error with timestamps |
| `portfolio_snapshots.csv` | Portfolio value snapshot every 15 minutes |
| `price_history.csv` | Raw ticker prices collected each loop |
| `state.json` | Persisted state (entry prices, peak value) for crash recovery |

## Configuration

All tunable parameters are in [`config.py`](config.py):

| Parameter | Value | Description |
|---|---|---|
| `BB_PERIOD` | 20 | Bollinger Band lookback window |
| `BB_STD_DEV` | 2.0 | Band width in standard deviations |
| `RSI_PERIOD` | 14 | RSI calculation period |
| `RSI_Z_PERIOD` | 20 | Rolling window for RSI Z-score |
| `RSI_Z_THRESHOLD` | 1.5 | Signal threshold (σ from mean RSI) |
| `TREND_SMA_PERIOD` | 200 | Long-term trend filter SMA period |
| `ATR_PERIOD` | 14 | ATR lookback window |
| `ATR_MULTIPLIER` | 6 | Stop-loss distance in ATR units |
| `CASH_BUFFER_PCT` | 10% | Minimum USD cash reserve |
| `MAX_ASSET_ALLOCATION_PCT` | 20% | Max allocation per asset |
| `STOP_LOSS_COOLDOWN_MINUTES` | 240 | Re-entry lockout after a stop-loss |
| `STOP_LOSS_RECOVERY_PCT` | 1% | Price must recover this much above stop before re-entry |
| `SIGNAL_LOOP_MINUTES` | 15 | Minutes between signal evaluations |

## Architecture

```
main.py           → Entry point, initializes & starts scheduler
├── config.py     → Environment variables & constants
├── api_client.py → Roostoo API calls with HMAC signing & retries
├── strategy.py   → BB + RSI Z-score + trend filter signal engine
├── portfolio.py  → Position tracking & allocation calculations
├── risk_manager.py → Stop-loss, circuit breaker, drawdown guards
├── scheduler.py  → APScheduler job definitions & orchestration
└── logger.py     → CSV + console logging
```
