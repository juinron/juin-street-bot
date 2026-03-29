"""Configuration — loads secrets from .env and defines all tunable constants."""

import os
from dotenv import load_dotenv

load_dotenv()

# API credentials
API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")
BASE_URL = "https://mock-api.roostoo.com"

# Traded pairs (Roostoo uses COIN/USD format)
ASSETS = ["BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD", "LINK/USD", "FET/USD", "TAO/USD", "ADA/USD"]

# Bollinger Band parameters
BB_PERIOD = 20
BB_STD_DEV = 2.0

# RSI parameters
RSI_PERIOD = 14
RSI_OVERSOLD = 40
RSI_OVERBOUGHT = 60

# Dynamic ATR-based risk management (replaces static STOP_LOSS_PCT)
ATR_PERIOD = 14             # rolling window for ATR calculation
ATR_MULTIPLIER = 6        # stop-loss at entry_price - (k * ATR); widened from 3 to reduce whipsaw on 15m ATR

# Volatility-adjusted RSI thresholds (replaces static RSI_OVERSOLD/OVERBOUGHT)
RSI_Z_PERIOD = 20           # rolling window for RSI mean/std calculation
RSI_Z_THRESHOLD = 1.5       # trigger signal when |Z_RSI| > threshold (1.5 sigma deviation)

# FIX 4: Trend filter — only BUY when price is above this SMA (avoids catching falling knives)
TREND_SMA_PERIOD = 200
TREND_FILTER_BUFFER = 0.02    # allow buys within 1% below trend SMA to avoid small dip rejections

# Portfolio allocation (Tiered Fixed-Fractional Sizing)
CASH_BUFFER_PCT = 0.10        # keep 10% in USD for aggressive buy cycles
MAX_ASSET_ALLOCATION_PCT = 0.20  # never exceed 20% allocation in single asset

# Fixed fractional buy sizing by asset risk tier
SIGNAL_SIZES = {
    "BTC/USD": 0.1,
    "ETH/USD": 0.1,
    "SOL/USD": 0.05,
    "BNB/USD": 0.05,
    "LINK/USD": 0.05,
    "XRP/USD": 0.05,
    "FET/USD": 0.05,
    "TAO/USD": 0.05,
    "ADA/USD": 0.05,
}

# Risk management
CIRCUIT_BREAKER_PAUSE_PCT = 0.10   # pause buys if portfolio drops 10% from start
CIRCUIT_BREAKER_RESUME_PCT = 0.08  # resume buys when within 8% of start
DAILY_LOSS_LIMIT_PCT = 0.05        # no buys if down 5% vs yesterday close
MAX_DRAWDOWN_PCT = 0.15            # halt all trading if 15% below peak
STOP_LOSS_COOLDOWN_MINUTES = 240   # no buys for 4 hours after stop-loss triggered on an asset
STOP_LOSS_RECOVERY_PCT = 0.01     # after stop-loss, price must recover 1% above stop level before re-entry

# Spread-aware order execution
MAKER_SPREAD_TICKS = 1             # submit buy at (max_bid + 1 tick), sell at (min_ask - 1 tick)

# Scheduling
SIGNAL_LOOP_MINUTES = 15      # run signal loop every 15 minutes
STALE_ORDER_HOURS = 2         # cancel unfilled orders older than 2 hours

# Order pricing offsets (DEPRECATED — now using spread-aware execution)
BUY_LIMIT_OFFSET = 1.001
SELL_LIMIT_OFFSET = 1.001

# API retry settings
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5

# Binance public API for historical candle data (no auth required)
BINANCE_BASE_URL = "https://data-api.binance.vision"
CANDLE_INTERVAL = "15m"
ATR_CANDLE_INTERVAL = "15m"  # ATR uses 15-minute buckets to reduce noise sensitivity
CANDLE_BOOTSTRAP_COUNT = 250

# Map Roostoo pairs → Binance symbols
BINANCE_SYMBOL_MAP = {
    "BTC/USD": "BTCUSDT",
    "ETH/USD": "ETHUSDT",
    "SOL/USD": "SOLUSDT",
    "BNB/USD": "BNBUSDT",
    "XRP/USD": "XRPUSDT",
    "LINK/USD": "LINKUSDT",
    "FET/USD": "FETUSDT",
    "TAO/USD": "TAOUSDT",
    "ADA/USD": "ADAUSDT",
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
