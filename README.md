# Juin Street Bot 🤖📈

An autonomous Python trading bot for the [Roostoo](https://roostoo.com) mock exchange. Uses a **Bollinger Bands mean-reversion strategy** with RSI confirmation, forced daily rebalancing, and comprehensive risk management.

## Strategy

The bot trades **4 crypto pairs** (BTC, ETH, SOL, BNB against USD) on a **2-hour loop**:

1. **Fetch market data** via Roostoo ticker API and build local price history
2. **Calculate Bollinger Bands** (20-period SMA ± 2 std devs) and **RSI** (14-period)
3. **BUY signal**: price drops below lower band + RSI < 40 (oversold) + no existing position
4. **SELL signal**: price rises above upper band + RSI > 60 (overbought) + holding position
5. **Orders**: Limit orders placed just inside the spread (±0.1% from current price)

### Daily Rebalance (09:00 UTC)
Ensures each asset stays near its **22.5% target allocation**. If any position drifts more than ±3%, the bot trims or tops up automatically. This guarantees activity even in flat markets.

### Risk Management
| Guard | Trigger | Action |
|---|---|---|
| **Stop-loss** | Position drops 4% from entry | Immediate market sell |
| **Circuit breaker** | Portfolio down 10% from start | Pause all buys (sells still execute) |
| **Daily loss limit** | Down 5% vs yesterday's close | No new buys until midnight UTC reset |
| **Max drawdown** | 15% below peak portfolio value | Halt all trading, await manual restart |

## Log Files

| File | Description |
|---|---|
| `trades_log.csv` | Every trade, cancellation, and error with timestamps and reasons |
| `portfolio_snapshots.csv` | Portfolio value breakdown every 2 hours + daily |
| `price_history.csv` | Ticker prices collected each loop (feeds strategy calculations) |
| `state.json` | Persisted bot state (entry prices, peak value) for crash recovery |

### `trades_log.csv` columns
`timestamp, asset, action, order_type, quantity, price, order_id, status, reason, portfolio_value_at_time`

### `portfolio_snapshots.csv` columns
`timestamp, total_value_usd, btc_value, eth_value, sol_value, bnb_value, usd_cash, daily_return_pct, drawdown_from_peak_pct`

## Configuration

All tunable parameters are in [`config.py`](config.py) — adjust without touching strategy logic:

| Parameter | Default | Description |
|---|---|---|
| `BB_PERIOD` | 20 | Bollinger Band lookback window |
| `BB_STD_DEV` | 2.0 | Standard deviation multiplier |
| `RSI_PERIOD` | 14 | RSI calculation period |
| `RSI_OVERSOLD` | 40 | RSI threshold for buy signal |
| `RSI_OVERBOUGHT` | 60 | RSI threshold for sell signal |
| `TARGET_ALLOCATION_PCT` | 22.5% | Target allocation per asset |
| `CASH_BUFFER_PCT` | 10% | Minimum USD cash reserve |
| `STOP_LOSS_PCT` | 4% | Per-trade stop-loss threshold |
| `SIGNAL_LOOP_HOURS` | 2 | Hours between signal evaluations |

## Architecture

```
main.py           → Entry point, initializes & starts scheduler
├── config.py     → Environment variables & constants
├── api_client.py → Roostoo API calls with HMAC signing & retries
├── strategy.py   → Bollinger Bands + RSI signal engine
├── portfolio.py  → Position tracking & allocation calculations
├── risk_manager.py → Stop-loss, circuit breaker, drawdown guards
├── scheduler.py  → APScheduler job definitions & orchestration
└── logger.py     → CSV + console logging
```