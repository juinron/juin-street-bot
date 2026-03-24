"""Configuration — loads secrets from .env and defines all tunable constants."""

import os
from dotenv import load_dotenv

load_dotenv()

# API credentials
API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")
BASE_URL = "https://mock-api.roostoo.com"

# Traded pairs (Roostoo uses COIN/USD format)
ASSETS = ["BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD", "LINK/USD"]

# Bollinger Band parameters
BB_PERIOD = 20
BB_STD_DEV = 2.0

# RSI parameters
RSI_PERIOD = 14
RSI_OVERSOLD = 40
RSI_OVERBOUGHT = 60

# Portfolio allocation
TARGET_ALLOCATION_PCT = 0.16  # 16% per asset (6 assets + 4% cash buffer)
CASH_BUFFER_PCT = 0.04        # keep 4% in USD
REBALANCE_DRIFT_PCT = 0.03    # rebalance if >3% off target

# Risk management
STOP_LOSS_PCT = 0.04               # -4% from entry
CIRCUIT_BREAKER_PAUSE_PCT = 0.10   # pause buys if portfolio drops 10% from start
CIRCUIT_BREAKER_RESUME_PCT = 0.08  # resume buys when within 8% of start
DAILY_LOSS_LIMIT_PCT = 0.05        # no buys if down 5% vs yesterday close
MAX_DRAWDOWN_PCT = 0.15            # halt all trading if 15% below peak

# Scheduling
SIGNAL_LOOP_MINUTES = 30     # run signal loop every 30 minutes
DAILY_REBALANCE_HOUR = 9      # rebalance at 09:00 UTC
STALE_ORDER_HOURS = 2         # cancel unfilled orders older than 2 hours

# Order pricing offsets (maker order placement)
BUY_LIMIT_OFFSET = 1.001      # buy limit = price * 1.001 (slightly above current price to increase fill probability)
SELL_LIMIT_OFFSET = 1.001     # sell limit = price * 1.001

# API retry settings
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5

# Binance public API for historical candle data (no auth required)
BINANCE_BASE_URL = "https://data-api.binance.vision"
CANDLE_INTERVAL = "30m"  # matches SIGNAL_LOOP_MINUTES
CANDLE_BOOTSTRAP_COUNT = 200  # candles to fetch on startup (BB_PERIOD=20 + buffer)

# Map Roostoo pairs → Binance symbols (Roostoo uses /USD, Binance uses USDT)
BINANCE_SYMBOL_MAP = {
    "BTC/USD": "BTCUSDT",
    "ETH/USD": "ETHUSDT",
    "SOL/USD": "SOLUSDT",
    "BNB/USD": "BNBUSDT",
    "XRP/USD": "XRPUSDT",
    "LINK/USD": "LINKUSDT",
}

# File paths
TRADES_LOG_FILE = "trades_log.csv"
PORTFOLIO_LOG_FILE = "portfolio_snapshots.csv"
PRICE_HISTORY_FILE = "price_history.csv"
STATE_FILE = "state.json"
