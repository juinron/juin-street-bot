"""Strategy module: price history collection + signal generation.

This module stores recent prices to CSV, computes indicators (BB, RSI, ATR, trend SMA),
and decides BUY/SELL/HOLD for each asset by combining:
  - Bollinger Bands mean reversion (price vs SMA)
  - RSI extremes with Z-score filtering
  - Trend direction filter (price > long-term SMA)
  - Optional profit gate on SELL (only exit winners)
"""

import os
import csv
import logging
from datetime import datetime, timezone

import pandas as pd
import numpy as np

import config

log = logging.getLogger(__name__)


def bootstrap_price_history(client) -> None:
    """Fetch historical candles from Binance and seed price_history.csv.

    Skips bootstrap if the file already exists with sufficient data,
    to avoid destroying live price snapshots accumulated between restarts.
    """
    if os.path.exists(config.PRICE_HISTORY_FILE):
        try:
            df = pd.read_csv(config.PRICE_HISTORY_FILE)
            if len(df) >= config.CANDLE_BOOTSTRAP_COUNT:
                log.info(
                    f"Price history already has {len(df)} rows, skipping bootstrap"
                )
                return
        except Exception:
            pass  # corrupt/empty file — proceed with bootstrap

    log.info("Bootstrapping price history from Binance...")

    rows = []
    for pair in config.ASSETS:
        binance_symbol = config.BINANCE_SYMBOL_MAP.get(pair)
        if not binance_symbol:
            log.warning(f"{pair}: no Binance symbol mapping, skipping bootstrap")
            continue

        candles = client.get_klines(
            binance_symbol=binance_symbol,
            interval=config.CANDLE_INTERVAL,
            limit=config.CANDLE_BOOTSTRAP_COUNT,
        )
        if not candles:
            log.warning(f"{pair}: failed to fetch klines from Binance")
            continue

        for candle in candles:
            close_ts = datetime.fromtimestamp(
                candle["close_time"] / 1000, tz=timezone.utc
            ).isoformat()
            rows.append([
                close_ts, pair, candle["close"],
                candle["high"], candle["low"],
            ])

        log.info(
            f"{pair}: bootstrapped {len(candles)} candles from Binance ({binance_symbol})"
        )

    if not rows:
        log.warning("No candles bootstrapped — strategy will wait for live data")
        return

    with open(config.PRICE_HISTORY_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "pair", "last_price", "high", "low"])
        writer.writerows(rows)

    log.info(f"Price history bootstrapped: {len(rows)} total rows written")


def collect_price_snapshot(client) -> dict:
    """Fetch current ticker for all assets and append to price_history.csv."""
    ticker_data = client.get_ticker()
    if not ticker_data or not ticker_data.get("Success"):
        log.warning("Failed to fetch ticker data for price snapshot")
        return {}

    prices = {}
    now = datetime.now(timezone.utc).isoformat()
    file_exists = os.path.exists(config.PRICE_HISTORY_FILE)

    with open(config.PRICE_HISTORY_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "pair", "last_price", "high", "low"])

        for pair in config.ASSETS:
            data = ticker_data.get("Data", {}).get(pair)
            if data:
                last_price = data.get("LastPrice", 0)
                prices[pair] = last_price
                # Live snapshots are point-in-time: no intra-interval H/L available.
                # Store last_price for both so ATR relies on price-change-to-prior-close.
                writer.writerow([now, pair, last_price, last_price, last_price])

    log.info(f"Price snapshot collected: {prices}")
    return prices


def load_price_history(pair: str) -> pd.DataFrame:
    """Load price history for a specific pair from CSV."""
    if not os.path.exists(config.PRICE_HISTORY_FILE):
        return pd.DataFrame()

    df = pd.read_csv(config.PRICE_HISTORY_FILE)
    df = df[df["pair"] == pair].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def load_price_history_resampled(pair: str, interval: str = "15min") -> pd.DataFrame:
    """Load price history and resample to specified interval (e.g., '15min').
    
    Uses last() for last_price, max() for max_bid, min() for min_ask to properly
    aggregate OHLC data (Close, High, Low) over the resampling interval.
    
    Args:
        pair: Trading pair (e.g., 'BTC/USD')
        interval: Resampling interval as pandas frequency string (default '15min')
    
    Returns:
        Resampled DataFrame with 15-minute (or specified) candles
    """
    df = load_price_history(pair)
    if df.empty:
        return df
    
    # Set timestamp as index for resampling
    df = df.set_index("timestamp")
    
    # Resample: last() for close, max() for high, min() for low
    df_resampled = df.resample(interval).agg({
        "last_price": "last",
        "high": "max",
        "low": "min",
        "pair": "first",
    })
    
    # Reset index and drop rows where no data existed in that interval
    df_resampled = df_resampled.dropna(subset=["last_price"]).reset_index()
    
    return df_resampled


def compute_bollinger_bands(prices: pd.Series) -> tuple:
    """Calculate upper band, lower band, SMA, and rolling std."""
    sma = prices.rolling(window=config.BB_PERIOD).mean()
    std = prices.rolling(window=config.BB_PERIOD).std()
    upper = sma + config.BB_STD_DEV * std
    lower = sma - config.BB_STD_DEV * std
    return upper, lower, sma, std


def compute_rsi(prices: pd.Series, period: int = None) -> pd.Series:
    """Calculate RSI using exponential moving average method."""
    period = period or config.RSI_PERIOD
    delta = prices.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = None) -> pd.Series:
    """Calculate Average True Range (ATR) using exponential moving average."""
    period = period or config.ATR_PERIOD

    hl = high - low
    hc = (high - close.shift(1)).abs()
    lc = (low - close.shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / period, min_periods=period).mean()
    return atr


def compute_rsi_zscore(rsi: pd.Series, period: int = None) -> pd.Series:
    """Calculate rolling Z-score of RSI to detect overbought/oversold extremes."""
    period = period or config.RSI_Z_PERIOD

    mean_rsi = rsi.rolling(window=period, min_periods=period).mean()
    std_rsi = rsi.rolling(window=period, min_periods=period).std()

    z_score = (rsi - mean_rsi) / std_rsi.replace(0, np.nan)
    return z_score


def compute_trend_sma(prices: pd.Series, period: int = None) -> pd.Series:
    """Calculate a long-period SMA used as a trend filter."""
    period = period or config.TREND_SMA_PERIOD
    return prices.rolling(window=period).mean()


def compute_signal(pair: str, held_assets: set, entry_price: float = None) -> tuple:
    """Evaluate a signal for a pair using 15-minute resampled history.

    Refactored to ensure indicator alignment and reduce signal noise.
    """
    # 1. Load and resample EVERYTHING to 15m immediately
    # This aligns the price action for RSI, SMA, and ATR automatically
    df = load_price_history_resampled(pair, interval="15min")

    # 2. Safety check: Need enough data for the longest window (Trend SMA)
    min_period = max(
        config.BB_PERIOD, config.RSI_PERIOD,
        config.RSI_Z_PERIOD, config.ATR_PERIOD,
        config.TREND_SMA_PERIOD,
    )
    
    if len(df) < min_period:
        log.info(f"{pair}: insufficient 15m data ({len(df)}/{min_period}), holding")
        return "HOLD", {}

    # 3. Extract OHLC equivalents from the resampled dataframe
    close = df["last_price"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    # 4. Compute all indicators on the same 15m time-series
    upper, lower, sma, std = compute_bollinger_bands(close)
    rsi = compute_rsi(close)
    rsi_zscore = compute_rsi_zscore(rsi)
    trend_sma = compute_trend_sma(close)
    atr = compute_atr(high, low, close)

    # 5. Get the most recent values for logic evaluation
    current_price = close.iloc[-1]
    current_upper = upper.iloc[-1]
    current_lower = lower.iloc[-1]
    current_sma = sma.iloc[-1]
    current_std = std.iloc[-1]
    current_rsi = rsi.iloc[-1]
    current_rsi_z = rsi_zscore.iloc[-1]
    current_atr = atr.iloc[-1]
    current_trend_sma = trend_sma.iloc[-1]

    # Stop if indicators aren't ready (prevents errors during initial bootstrap)
    if pd.isna(current_upper) or pd.isna(current_rsi_z) or pd.isna(current_atr) or pd.isna(current_trend_sma):
        log.info(f"{pair}: indicators not ready (NaN found), holding")
        return "HOLD", {}

    # 6. Trend Filter Logic
    trend_filter_threshold = current_trend_sma * (1 - config.TREND_FILTER_BUFFER)
    trend_filter_pass = current_price > trend_filter_threshold

    metadata = {
        'rsi_zscore': float(current_rsi_z),
        'atr': float(current_atr),
        'bb_upper': float(current_upper),
        'bb_lower': float(current_lower),
        'bb_sma': float(current_sma),
        'sigma_level': None,
        'trend_sma': float(current_trend_sma),
        'trend_filter_pass': bool(trend_filter_pass),
    }

    log.info(
        f"{pair}: [15m] price={current_price:.4f} SMA={current_sma:.4f} "
        f"TrendSMA={current_trend_sma:.4f} trend_pass={trend_filter_pass} "
        f"RSI={current_rsi:.1f} RSI_Z={current_rsi_z:.2f} ATR={current_atr:.4f}"
    )

    # ── BUY Logic ──
    # Condition: Oversold Z-RSI + Price < Mid-BB + Strong Uptrend
    if (
        current_rsi_z < -config.RSI_Z_THRESHOLD
        and current_price < current_sma
        and trend_filter_pass
    ):
        # Calculate how many standard deviations the price is from the mean
        if current_std > 0:
            sigma_level = (current_sma - current_price) / current_std
            metadata['sigma_level'] = float(sigma_level)

        log.info(f"{pair}: BUY signal — Z_RSI={current_rsi_z:.2f} (< -{config.RSI_Z_THRESHOLD})")
        return "BUY", metadata

    # ── SELL Logic (take-profit only; stop-losses are handled by execute_stop_losses) ──
    coin = pair.split("/")[0]
    if coin in held_assets:
        if current_rsi_z > config.RSI_Z_THRESHOLD and current_price > current_sma:
            if entry_price and current_price > entry_price:
                log.info(f"{pair}: TAKE PROFIT signal — Z_RSI={current_rsi_z:.2f}")
                return "SELL", metadata
            else:
                log.info(f"{pair}: SELL signal blocked by profit gate.")

    return "HOLD", metadata