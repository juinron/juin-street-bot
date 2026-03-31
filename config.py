"""Configuration — loads secrets from .env and defines all tunable constants."""

import os
from dotenv import load_dotenv

load_dotenv()

# API credentials
API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")
BASE_URL = "https://mock-api.roostoo.com"

# Traded pairs (Roostoo uses COIN/USD format)
ASSETS = ["BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD"]

# Bollinger Band parameters
BB_PERIOD = 20
BB_STD_DEV = 2.0

# RSI parameters
RSI_PERIOD = 14

# ATR-based risk management
ATR_PERIOD = 14
ATR_MULTIPLIER = 3

# RSI Z-score thresholds
RSI_Z_PERIOD = 20
RSI_Z_THRESHOLD = 1.2

# Trend filter — only BUY when price is above this SMA
TREND_SMA_PERIOD = 200
TREND_FILTER_BUFFER = 0.02    # allow buys within 2% below trend SMA

# Portfolio allocation (Tiered Fixed-Fractional Sizing)
CASH_BUFFER_PCT = 0.10        # keep 10% in USD for aggressive buy cycles
MAX_ASSET_ALLOCATION_PCT = 0.20  # never exceed 20% allocation in single asset

# Fixed fractional buy sizing by asset risk tier
SIGNAL_SIZES = {
    "BTC/USD": 0.05,
    "ETH/USD": 0.05,
    "SOL/USD": 0.05,
    "BNB/USD": 0.05,
}

# Risk management
CIRCUIT_BREAKER_PAUSE_PCT = 0.10   # pause buys if portfolio drops 10% from start
CIRCUIT_BREAKER_RESUME_PCT = 0.08  # resume buys when within 8% of start
DAILY_LOSS_LIMIT_PCT = 0.05        # no buys if down 5% vs yesterday close
MAX_DRAWDOWN_PCT = 0.15            # halt all trading if 15% below peak
STOP_LOSS_COOLDOWN_MINUTES = 60   # no buys for 4 hours after stop-loss triggered on an asset
STOP_LOSS_RECOVERY_PCT = 0.01     # after stop-loss, price must recover 1% above stop level before re-entry

# Spread-aware order execution
MAKER_SPREAD_TICKS = 1             # submit buy at (max_bid + 1 tick), sell at (min_ask - 1 tick)

# Scheduling
SIGNAL_LOOP_MINUTES = 15      # run signal loop every 15 minutes
STALE_ORDER_HOURS = 2         # cancel unfilled orders older than 2 hours

# API retry settings
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5

# Binance public API for historical candle data (no auth required)
BINANCE_BASE_URL = "https://data-api.binance.vision"
CANDLE_INTERVAL = "15m"
CANDLE_BOOTSTRAP_COUNT = 350

# Map Roostoo pairs → Binance symbols
BINANCE_SYMBOL_MAP = {
    "BTC/USD": "BTCUSDT",
    "ETH/USD": "ETHUSDT",
    "SOL/USD": "SOLUSDT",
    "BNB/USD": "BNBUSDT",
    "LINK/USD": "LINKUSDT",
    "ADA/USD": "ADAUSDT",
    "AVAX/USD": "AVAXUSDT",
}

# Dust handling / minimum tradable value
MIN_TRADE_USD = 10.0
DUST_THRESHOLD_USD = 10.0
DUST_SELL_ENABLED = True
DUST_SELL_ORDER_TYPE = "MARKET"

# File paths
TRADES_LOG_FILE = "trades_log.csv"
PORTFOLIO_LOG_FILE = "portfolio_snapshots.csv"
PRICE_HISTORY_FILE = "price_history.csv"
STATE_FILE = "state.json"
