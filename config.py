"""Configuration — loads secrets from .env and defines all tunable constants."""

import os
from dotenv import load_dotenv

load_dotenv()

# API credentials
API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")
BASE_URL = "https://mock-api.roostoo.com"

# Traded pairs (Roostoo uses COIN/USD format)
ASSETS = ["BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD", "LINK/USD", "ADA/USD", "FET/USD"]

# Bollinger Band parameters
BB_PERIOD = 20
BB_STD_DEV = 2.0

# RSI parameters
RSI_PERIOD = 14
RSI_OVERSOLD = 40
RSI_OVERBOUGHT = 60

# Dynamic ATR-based risk management (replaces static STOP_LOSS_PCT)
ATR_PERIOD = 14             # rolling window for ATR calculation
ATR_MULTIPLIER = 3.5        # stop-loss at entry_price - (k * ATR)

# Volatility-adjusted RSI thresholds (replaces static RSI_OVERSOLD/OVERBOUGHT)
RSI_Z_PERIOD = 20           # rolling window for RSI mean/std calculation
RSI_Z_THRESHOLD = 2.0       # trigger signal when |Z_RSI| > threshold (2 sigma deviation)

# Portfolio allocation (Tiered Fixed-Fractional Sizing)
CASH_BUFFER_PCT = 0.10        # keep 10% in USD for aggressive buy cycles
REBALANCE_DRIFT_PCT = 0.03    # rebalance if >3% off allocation
MAX_ASSET_ALLOCATION_PCT = 0.20  # never exceed 20% allocation in single asset

# Fixed fractional buy sizing by asset risk tier
SIGNAL_SIZES = {
    "BTC/USD": 0.1,
    "ETH/USD": 0.1,
    "SOL/USD": 0.05,
    "BNB/USD": 0.05,
    "LINK/USD": 0.05,
    "ADA/USD": 0.05,
    "XRP/USD": 0.03,
    "FET/USD": 0.03,
}

# Risk management
CIRCUIT_BREAKER_PAUSE_PCT = 0.10   # pause buys if portfolio drops 10% from start
CIRCUIT_BREAKER_RESUME_PCT = 0.08  # resume buys when within 8% of start
DAILY_LOSS_LIMIT_PCT = 0.05        # no buys if down 5% vs yesterday close
MAX_DRAWDOWN_PCT = 0.15            # halt all trading if 15% below peak
STOP_LOSS_COOLDOWN_MINUTES = 30    # no buys for 30 minutes after stop-loss triggered on an asset

# Spread-aware order execution (replaces static offsets)
# Quotes limit orders slightly inside the bid-ask spread to act as maker
MAKER_SPREAD_TICKS = 1             # submit buy at (max_bid + 1 tick), sell at (min_ask - 1 tick)

# Scheduling
SIGNAL_LOOP_MINUTES = 5      # run signal loop every 5 minutes (high-frequency mode)
DAILY_REBALANCE_HOUR = 9      # rebalance at 09:00 UTC
STALE_ORDER_HOURS = 2         # cancel unfilled orders older than 2 hours

# Order pricing offsets (DEPRECATED — now using spread-aware execution)
# Kept for backward compatibility; overridden by MAKER_SPREAD_TICKS in execution
BUY_LIMIT_OFFSET = 1.001      
SELL_LIMIT_OFFSET = 1.001     

# API retry settings
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5

# Binance public API for historical candle data (no auth required)
BINANCE_BASE_URL = "https://data-api.binance.vision"
CANDLE_INTERVAL = "5m"  # matches SIGNAL_LOOP_MINUTES (5-minute candles for high-frequency)
CANDLE_BOOTSTRAP_COUNT = 200  # candles to fetch on startup (ATR_PERIOD=14, RSI_PERIOD=14, BB_PERIOD=20 + buffer)

# Map Roostoo pairs → Binance symbols (Roostoo uses /USD, Binance uses USDT)
BINANCE_SYMBOL_MAP = {
    "BTC/USD": "BTCUSDT",
    "ETH/USD": "ETHUSDT",
    "SOL/USD": "SOLUSDT",
    "BNB/USD": "BNBUSDT",
    "XRP/USD": "XRPUSDT",
    "LINK/USD": "LINKUSDT",
    "ADA/USD": "ADAUSDT",
    "FET/USD": "FETUSDT",
}

# Dust handling / minimum tradable value
# Treats tiny residual assets (e.g. 0.01 ETH) as non-held for signal filtering.
MIN_TRADE_USD = 10.0
DUST_THRESHOLD_USD = 10.0
# Optionally liquidate dust positions (to prevent accumulation over time)
DUST_SELL_ENABLED = True
DUST_SELL_ORDER_TYPE = "MARKET"  # use MARKET for simplest, or LIMIT if desired

# File paths
TRADES_LOG_FILE = "trades_log.csv"
PORTFOLIO_LOG_FILE = "portfolio_snapshots.csv"
PRICE_HISTORY_FILE = "price_history.csv"
STATE_FILE = "state.json"
