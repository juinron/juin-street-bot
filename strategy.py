"""Strategy — Bollinger Bands + RSI signal generation with price history collection."""

import os
import csv
import logging
from datetime import datetime, timezone

import pandas as pd
import numpy as np

import config

log = logging.getLogger(__name__)


def bootstrap_price_history(client) -> None:
    """Fetch historical candles from Binance and seed price_history.csv."""
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
        writer.writerow(["timestamp", "pair", "last_price", "max_bid", "min_ask"])
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
            writer.writerow(["timestamp", "pair", "last_price", "max_bid", "min_ask"])

        for pair in config.ASSETS:
            data = ticker_data.get("Data", {}).get(pair)
            if data:
                last_price = data.get("LastPrice", 0)
                max_bid = data.get("MaxBid", last_price)
                min_ask = data.get("MinAsk", last_price)
                prices[pair] = last_price
                writer.writerow([now, pair, last_price, max_bid, min_ask])

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


def compute_bollinger_bands(prices: pd.Series) -> tuple:
    """Calculate upper band, lower band, and SMA."""
    sma = prices.rolling(window=config.BB_PERIOD).mean()
    std = prices.rolling(window=config.BB_PERIOD).std()
    upper = sma + config.BB_STD_DEV * std
    lower = sma - config.BB_STD_DEV * std
    return upper, lower, sma


def compute_rsi(prices: pd.Series, period: int = None) -> pd.Series:
    """Calculate RSI using exponential moving average method."""
    period = period or config.RSI_PERIOD
    delta = prices.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()

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
    """Calculate a long-period SMA used as a trend filter.
    
    FIX 4: Added trend filter to prevent buying into downtrends.
    Period defaults to config.TREND_SMA_PERIOD (50 by default).
    """
    period = period or config.TREND_SMA_PERIOD
    return prices.rolling(window=period).mean()


def compute_signal(pair: str, held_assets: set) -> tuple:
    """Evaluate dynamic signal using Bollinger Bands, RSI Z-Score, ATR, and trend filter.

    Returns: (signal, metadata_dict)
    signal: 'BUY', 'SELL', or 'HOLD'
    metadata_dict: {
        'rsi_zscore': float,
        'atr': float,
        'sigma_level': float or None,
        'bb_upper': float,
        'bb_lower': float,
        'bb_sma': float,
        'trend_sma': float,       # FIX 4: added
        'trend_filter_pass': bool, # FIX 4: added — True if price is above trend SMA
    }

    BUY conditions (ALL must be true):
        1. RSI Z-score < -RSI_Z_THRESHOLD  (oversold)
        2. Price < BB SMA                  (below mean)
        3. Price > trend SMA               (FIX 4: uptrend filter — avoids catching falling knives)

    SELL conditions (ALL must be true):
        1. RSI Z-score > RSI_Z_THRESHOLD   (overbought)
        2. Price > BB SMA                  (above mean)
        3. Coin is held
    """
    df = load_price_history(pair)

    # Need enough data for all indicators including trend SMA
    min_period = max(
        config.BB_PERIOD, config.RSI_PERIOD,
        config.RSI_Z_PERIOD, config.ATR_PERIOD,
        config.TREND_SMA_PERIOD,
    )
    if len(df) < min_period:
        log.info(f"{pair}: insufficient data ({len(df)}/{min_period}), holding")
        return "HOLD", {}

    df = df.tail(max(200, config.TREND_SMA_PERIOD + 50)).copy()
    close = df["last_price"].astype(float)
    high = df["max_bid"].astype(float)
    low = df["min_ask"].astype(float)

    if high.abs().sum() < 1e-10:
        high = close
    if low.abs().sum() < 1e-10:
        low = close

    upper, lower, sma = compute_bollinger_bands(close)
    rsi = compute_rsi(close)
    atr = compute_atr(high, low, close)
    rsi_zscore = compute_rsi_zscore(rsi)
    trend_sma = compute_trend_sma(close)  # FIX 4

    current_price = close.iloc[-1]
    current_upper = upper.iloc[-1]
    current_lower = lower.iloc[-1]
    current_sma = sma.iloc[-1]
    current_rsi = rsi.iloc[-1]
    current_rsi_z = rsi_zscore.iloc[-1]
    current_atr = atr.iloc[-1]
    current_trend_sma = trend_sma.iloc[-1]  # FIX 4

    if pd.isna(current_upper) or pd.isna(current_rsi_z) or pd.isna(current_atr) or pd.isna(current_trend_sma):
        log.info(f"{pair}: indicators not ready, holding")
        return "HOLD", {}

    # FIX 4: trend filter — True means price is in an uptrend
    trend_filter_pass = current_price > current_trend_sma

    metadata = {
        'rsi_zscore': float(current_rsi_z),
        'atr': float(current_atr),
        'bb_upper': float(current_upper),
        'bb_lower': float(current_lower),
        'bb_sma': float(current_sma),
        'sigma_level': None,
        'trend_sma': float(current_trend_sma),         # FIX 4
        'trend_filter_pass': bool(trend_filter_pass),  # FIX 4
    }

    log.info(
        f"{pair}: price={current_price:.4f} SMA={current_sma:.4f} "
        f"TrendSMA={current_trend_sma:.4f} trend_pass={trend_filter_pass} "
        f"RSI={current_rsi:.1f} RSI_Z={current_rsi_z:.2f} ATR={current_atr:.4f}"
    )

    coin = pair.split("/")[0]
    is_held = coin in held_assets

    # ── BUY Logic ──
    # Requires: oversold Z-RSI + price below BB SMA + price above trend SMA (FIX 4)
    if (
        current_rsi_z < -config.RSI_Z_THRESHOLD
        and current_price < current_sma
        and trend_filter_pass  # FIX 4: only buy in uptrends
    ):
        if current_sma > 0:
            std_dev = (current_sma - current_lower) / config.BB_STD_DEV if config.BB_STD_DEV > 0 else 1
            if std_dev > 0:
                sigma_level = (current_sma - current_price) / std_dev
                metadata['sigma_level'] = float(sigma_level)

        log.info(
            f"{pair}: BUY signal — Z_RSI={current_rsi_z:.2f} (< -{config.RSI_Z_THRESHOLD}) "
            f"price={current_price:.4f} SMA={current_sma:.4f} TrendSMA={current_trend_sma:.4f}"
        )
        return "BUY", metadata

    # Log when trend filter blocked a BUY signal (useful for tuning)
    if (
        current_rsi_z < -config.RSI_Z_THRESHOLD
        and current_price < current_sma
        and not trend_filter_pass
    ):
        log.info(
            f"{pair}: BUY signal SUPPRESSED by trend filter — "
            f"price={current_price:.4f} below TrendSMA={current_trend_sma:.4f}"
        )

    # ── SELL Logic ──
    if current_rsi_z > config.RSI_Z_THRESHOLD and current_price > current_sma:
        if not is_held:
            log.info(f"{pair}: SELL signal suppressed — position below dust threshold")
            return "HOLD", metadata

        log.info(
            f"{pair}: SELL signal — Z_RSI={current_rsi_z:.2f} (> {config.RSI_Z_THRESHOLD}) "
            f"price={current_price:.4f} SMA={current_sma:.4f}"
        )
        return "SELL", metadata

    return "HOLD", metadata
