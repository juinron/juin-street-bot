"""Strategy — Bollinger Bands + RSI signal generation with price history collection."""

import os
import csv
import logging
from datetime import datetime, timezone

import pandas as pd
import numpy as np

import config

log = logging.getLogger(__name__)


def collect_price_snapshot(client) -> dict:
    """Fetch current ticker for all assets and append to price_history.csv.
    Returns dict of {pair: last_price} for convenience.
    """
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
                prices[pair] = last_price
                writer.writerow([
                    now, pair, last_price,
                    data.get("MaxBid", 0), data.get("MinAsk", 0),
                ])

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
    """Calculate upper band, lower band, and SMA for the given price series."""
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


def compute_signal(pair: str, held_assets: set) -> str:
    """Evaluate Bollinger Band + RSI signal for a single pair.
    Returns 'BUY', 'SELL', or 'HOLD'.
    """
    df = load_price_history(pair)

    # Need at least BB_PERIOD data points to calculate bands
    if len(df) < config.BB_PERIOD:
        log.info(f"{pair}: insufficient data ({len(df)}/{config.BB_PERIOD}), holding")
        return "HOLD"

    # Use last 50 data points (as per spec)
    df = df.tail(50)
    close = df["last_price"].astype(float)

    upper, lower, sma = compute_bollinger_bands(close)
    rsi = compute_rsi(close)

    current_price = close.iloc[-1]
    current_upper = upper.iloc[-1]
    current_lower = lower.iloc[-1]
    current_rsi = rsi.iloc[-1]

    # Skip if indicators are NaN (insufficient data for calculation)
    if pd.isna(current_upper) or pd.isna(current_rsi):
        log.info(f"{pair}: indicators not ready, holding")
        return "HOLD"

    log.info(
        f"{pair}: price={current_price:.2f} upper={current_upper:.2f} "
        f"lower={current_lower:.2f} RSI={current_rsi:.1f}"
    )

    # Extract coin symbol from pair (e.g., "BTC" from "BTC/USD")
    coin = pair.split("/")[0]

    # BUY: price below lower band + RSI oversold + no existing position
    if current_price < current_lower and current_rsi < config.RSI_OVERSOLD:
        if coin not in held_assets:
            log.info(f"{pair}: BUY signal — price below lower BB, RSI={current_rsi:.1f}")
            return "BUY"
        log.info(f"{pair}: BUY signal conditions met but already holding, hold")

    # SELL: price above upper band + RSI overbought + holding position
    if current_price > current_upper and current_rsi > config.RSI_OVERBOUGHT:
        if coin in held_assets:
            log.info(f"{pair}: SELL signal — price above upper BB, RSI={current_rsi:.1f}")
            return "SELL"
        log.info(f"{pair}: SELL signal conditions met but not holding, hold")

    return "HOLD"
