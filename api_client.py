"""Roostoo API client — HMAC-signed requests with automatic retry."""

import time
import hmac
import hashlib
import logging
from typing import Optional

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
        self.session = requests.Session()

    def _timestamp(self) -> str:
        """Return current UTC time in milliseconds as a string (required by Roostoo API)."""
        return str(int(time.time() * 1000))

    def _sign(self, params: dict) -> tuple[dict, dict, str]:
        """Attach a timestamp, sort params, and sign with HMAC-SHA256.

        Args:
            params: Request payload dict (without timestamp).

        Returns:
            Tuple of (headers, signed_params, total_params_string) where
            headers carry the API key and signature, signed_params includes
            the injected timestamp, and total_params_string is the
            canonicalized query string that was signed.
        """
        params = {**params, "timestamp": self._timestamp()}
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
        """Fetch the Roostoo server timestamp. Used to verify API connectivity on startup."""
        res = self.session.get(f"{self.base_url}/v3/serverTime", timeout=10)
        res.raise_for_status()
        return res.json()

    @retry
    def get_exchange_info(self) -> Optional[dict]:
        """Fetch trading pair rules (price/amount precision, minimum order size).

        Returns:
            Dict with a 'TradePairs' key mapping pair symbols to their rules,
            or None on failure.
        """
        res = self.session.get(f"{self.base_url}/v3/exchangeInfo", timeout=10)
        res.raise_for_status()
        return res.json()

    @retry
    def get_ticker(self, pair: str = None) -> Optional[dict]:
        """Fetch market ticker. If pair is None, returns all pairs."""
        params = {"timestamp": self._timestamp()}
        if pair:
            params["pair"] = pair
        res = self.session.get(f"{self.base_url}/v3/ticker", params=params, timeout=10)
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
        res = self.session.get(
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
        """Fetch account balances for all assets (signed request).

        Returns:
            Dict with a 'SpotWallet' key containing per-asset Free/Lock balances,
            or None on failure.
        """
        headers, params, _ = self._sign({})
        res = self.session.get(
            f"{self.base_url}/v3/balance", headers=headers, params=params, timeout=10
        )
        res.raise_for_status()
        return res.json()

    @retry
    def get_pending_count(self) -> Optional[dict]:
        """Fetch the number of currently pending (unfilled) orders (signed request)."""
        headers, params, _ = self._sign({})
        res = self.session.get(
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

        res = self.session.post(
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

        res = self.session.post(
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

        res = self.session.post(
            f"{self.base_url}/v3/cancel_order",
            headers=headers, data=total_params, timeout=10,
        )
        res.raise_for_status()
        return res.json()
