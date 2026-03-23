"""Roostoo API client — HMAC-signed requests with automatic retry."""

import time
import hmac
import hashlib
import logging
from typing import Optional, Tuple

import requests
from functools import wraps

import config

log = logging.getLogger(__name__)


def retry(func):
    """Retry decorator: retries up to MAX_RETRIES on failure with delay."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                log.warning(f"Attempt {attempt}/{config.MAX_RETRIES} failed for {func.__name__}: {e}")
                if attempt < config.MAX_RETRIES:
                    time.sleep(config.RETRY_DELAY_SECONDS)
                else:
                    log.error(f"All {config.MAX_RETRIES} attempts failed for {func.__name__}: {e}")
                    return None
    return wrapper


class RoostooClient:
    """Handles all Roostoo API interactions with HMAC-SHA256 signing."""

    def __init__(self, api_key: str = None, api_secret: str = None):
        self.api_key = api_key or config.API_KEY
        self.api_secret = api_secret or config.API_SECRET
        self.base_url = config.BASE_URL

    def _timestamp(self) -> str:
        return str(int(time.time() * 1000))

    def _sign(self, params: dict) -> Tuple[dict, dict, str]:
        """Sign params with HMAC-SHA256. Returns (headers, params, total_params_string)."""
        params["timestamp"] = self._timestamp()
        sorted_keys = sorted(params.keys())
        total_params = "&".join(f"{k}={params[k]}" for k in sorted_keys)

        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            total_params.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        headers = {
            "RST-API-KEY": self.api_key,
            "MSG-SIGNATURE": signature,
        }
        return headers, params, total_params

    # ── Public endpoints (no signing) ──

    @retry
    def get_server_time(self) -> Optional[dict]:
        res = requests.get(f"{self.base_url}/v3/serverTime", timeout=10)
        res.raise_for_status()
        return res.json()

    @retry
    def get_exchange_info(self) -> Optional[dict]:
        res = requests.get(f"{self.base_url}/v3/exchangeInfo", timeout=10)
        res.raise_for_status()
        return res.json()

    @retry
    def get_ticker(self, pair: str = None) -> Optional[dict]:
        """Fetch market ticker. If pair is None, returns all pairs."""
        params = {"timestamp": self._timestamp()}
        if pair:
            params["pair"] = pair
        res = requests.get(f"{self.base_url}/v3/ticker", params=params, timeout=10)
        res.raise_for_status()
        return res.json()

    @retry
    def get_klines(
        self, binance_symbol: str, interval: str, limit: int
    ) -> Optional[list]:
        """Fetch historical klines from Binance public API (no auth required).

        Args:
            binance_symbol: Binance trading pair symbol (e.g. 'BTCUSDT').
            interval: Candle interval (e.g. '2h', '1h', '1d').
            limit: Number of candles to fetch (max 1000).

        Returns:
            List of dicts with keys: open_time, open, high, low, close, volume,
            close_time. Returns None on failure.
        """
        params = {
            "symbol": binance_symbol,
            "interval": interval,
            "limit": limit,
        }
        res = requests.get(
            f"{config.BINANCE_BASE_URL}/api/v3/klines",
            params=params,
            timeout=15,
        )
        res.raise_for_status()
        raw = res.json()

        candles = []
        for entry in raw:
            candles.append({
                "open_time": entry[0],
                "open": float(entry[1]),
                "high": float(entry[2]),
                "low": float(entry[3]),
                "close": float(entry[4]),
                "volume": float(entry[5]),
                "close_time": entry[6],
            })
        return candles

    # ── Signed endpoints ──

    @retry
    def get_balance(self) -> Optional[dict]:
        headers, params, _ = self._sign({})
        res = requests.get(
            f"{self.base_url}/v3/balance", headers=headers, params=params, timeout=10
        )
        res.raise_for_status()
        return res.json()

    @retry
    def get_pending_count(self) -> Optional[dict]:
        headers, params, _ = self._sign({})
        res = requests.get(
            f"{self.base_url}/v3/pending_count", headers=headers, params=params, timeout=10
        )
        res.raise_for_status()
        return res.json()

    @retry
    def place_order(
        self, pair: str, side: str, quantity: float,
        price: float = None, order_type: str = None
    ) -> Optional[dict]:
        """Place a LIMIT or MARKET order."""
        if order_type is None:
            order_type = "LIMIT" if price is not None else "MARKET"

        payload = {
            "pair": pair,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": str(quantity),
        }
        if order_type.upper() == "LIMIT" and price is not None:
            payload["price"] = str(price)

        headers, _, total_params = self._sign(payload)
        headers["Content-Type"] = "application/x-www-form-urlencoded"

        res = requests.post(
            f"{self.base_url}/v3/place_order",
            headers=headers, data=total_params, timeout=10,
        )
        res.raise_for_status()
        return res.json()

    @retry
    def query_order(
        self, order_id: int = None, pair: str = None, pending_only: bool = None
    ) -> Optional[dict]:
        """Query order history or specific orders."""
        payload = {}
        if order_id is not None:
            payload["order_id"] = str(order_id)
        else:
            if pair:
                payload["pair"] = pair
            if pending_only is not None:
                payload["pending_only"] = "TRUE" if pending_only else "FALSE"

        headers, _, total_params = self._sign(payload)
        headers["Content-Type"] = "application/x-www-form-urlencoded"

        res = requests.post(
            f"{self.base_url}/v3/query_order",
            headers=headers, data=total_params, timeout=10,
        )
        res.raise_for_status()
        return res.json()

    @retry
    def cancel_order(self, order_id: int = None, pair: str = None) -> Optional[dict]:
        """Cancel specific order, all orders for a pair, or all pending orders."""
        payload = {}
        if order_id is not None:
            payload["order_id"] = str(order_id)
        elif pair:
            payload["pair"] = pair

        headers, _, total_params = self._sign(payload)
        headers["Content-Type"] = "application/x-www-form-urlencoded"

        res = requests.post(
            f"{self.base_url}/v3/cancel_order",
            headers=headers, data=total_params, timeout=10,
        )
        res.raise_for_status()
        return res.json()
