"""Portfolio — position tracking, allocation calculations, and state persistence."""

import json
import os
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Tuple

import config

log = logging.getLogger(__name__)


class PortfolioManager:
    """Tracks positions, entry prices, and calculates portfolio allocations."""

    def __init__(self):
        self.entry_prices = {}
        self.starting_value = 0
        self.peak_value = 0
        self.yesterday_close = 0
        self._pair_rules = {}
        self._pair_rules_ts = 0.0
        self.position_quantities = {}
        self.tranche_allocations = {}
        self._load_state()

    def get_pair_rules(self, client, refresh_after_seconds: float = 3600) -> dict:
        now = time.time()
        if self._pair_rules and (now - self._pair_rules_ts) < refresh_after_seconds:
            return self._pair_rules

        info = client.get_exchange_info()
        pair_rules = {}
        if info and info.get("TradePairs"):
            for pair, details in info["TradePairs"].items():
                pair_rules[pair] = {
                    "price_precision": int(details.get("PricePrecision", 4)),
                    "amount_precision": int(details.get("AmountPrecision", 6)),
                    "mini_order": float(details.get("MiniOrder", 0) or 0),
                }

        self._pair_rules = pair_rules
        self._pair_rules_ts = now
        return self._pair_rules

    @staticmethod
    def floor_to_precision(value: float, precision: int) -> float:
        if precision is None:
            return float(value)
        if precision < 0:
            return float(value)
        d = Decimal(str(value))
        quant = Decimal("1").scaleb(-precision)
        return float(d.quantize(quant, rounding=ROUND_DOWN))

    def _load_state(self):
        if not os.path.exists(config.STATE_FILE):
            return
        try:
            with open(config.STATE_FILE, "r") as f:
                state = json.load(f)
            self.entry_prices = state.get("entry_prices", {})
            self.starting_value = state.get("starting_value", 0)
            self.peak_value = state.get("peak_value", 0)
            self.yesterday_close = state.get("yesterday_close", 0)
            self.position_quantities = state.get("position_quantities", {})
            self.tranche_allocations = state.get("tranche_allocations", {})
            log.info(f"State restored: peak={self.peak_value:.2f}, "
                     f"positions={list(self.entry_prices.keys())}")
        except Exception as e:
            log.warning(f"Failed to load state: {e}")

    def save_state(self):
        state = {
            "entry_prices": self.entry_prices,
            "starting_value": self.starting_value,
            "peak_value": self.peak_value,
            "yesterday_close": self.yesterday_close,
            "position_quantities": self.position_quantities,
            "tranche_allocations": self.tranche_allocations,
        }
        with open(config.STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

    def fetch_portfolio(self, client) -> dict:
        balance_data = client.get_balance()
        if not balance_data or not balance_data.get("Success"):
            log.error(f"Failed to fetch balance: {balance_data}")
            return {}

        wallet = balance_data.get("SpotWallet", balance_data.get("Wallet", {}))
        usd_free = wallet.get("USD", {}).get("Free", 0)
        usd_locked = wallet.get("USD", {}).get("Lock", 0)
        usd_cash = usd_free + usd_locked
        log.debug(f"Wallet data: {wallet}")

        ticker_data = client.get_ticker()
        if not ticker_data or not ticker_data.get("Success"):
            log.error(f"Failed to fetch ticker for portfolio valuation: {ticker_data}")
            return {}

        log.debug(f"Ticker data keys: {list(ticker_data.get('Data', {}).keys())}")

        prices = {}
        max_bids = {}
        min_asks = {}
        asset_values = {}
        balances = {}
        held_assets = set()
        total_value = usd_cash

        for pair in config.ASSETS:
            coin = pair.split("/")[0]
            coin_balance = wallet.get(coin, {}).get("Free", 0)
            coin_locked = wallet.get(coin, {}).get("Lock", 0)
            total_coin = coin_balance + coin_locked
            balances[coin] = total_coin

            ticker = ticker_data.get("Data", {}).get(pair, {})
            last_price = ticker.get("LastPrice", 0)
            max_bid = ticker.get("MaxBid", last_price)
            min_ask = ticker.get("MinAsk", last_price)

            prices[pair] = last_price
            max_bids[pair] = max_bid
            min_asks[pair] = min_ask

            if not ticker:
                log.warning(f"{pair}: not found in ticker data — pair may not be listed")

            value = total_coin * last_price
            asset_values[pair] = value
            total_value += value

            if total_coin > 0 and value >= config.DUST_THRESHOLD_USD:
                held_assets.add(coin)
            elif total_coin > 0:
                log.debug(
                    f"{pair}: dust position ({total_coin:.8f} {coin}, ${value:.2f}) below "
                    f"DUST_THRESHOLD_USD={config.DUST_THRESHOLD_USD}, not marking held"
                )

        if self.starting_value == 0:
            self.starting_value = total_value
            log.info(f"Starting portfolio value: ${total_value:.2f}")

        if total_value > self.peak_value:
            self.peak_value = total_value

        log.info(
            f"Portfolio: ${total_value:.2f} | USD={usd_cash:.2f} | "
            f"held={list(held_assets)} | prices={prices}"
        )

        return {
            "total_value": total_value,
            "usd_cash": usd_cash,
            "balances": balances,
            "prices": prices,
            "max_bid": max_bids,
            "min_ask": min_asks,
            "asset_values": asset_values,
            "held_assets": held_assets,
        }

    def get_dust_candidates(self, portfolio: dict) -> list:
        candidates = []
        balances = portfolio.get("balances", {})
        prices = portfolio.get("prices", {})

        for pair in config.ASSETS:
            coin = pair.split("/")[0]
            qty = balances.get(coin, 0)
            price = prices.get(pair, 0)
            if qty <= 0 or price <= 0:
                continue
            usd_value = qty * price
            if usd_value > 0 and usd_value <= config.DUST_THRESHOLD_USD:
                candidates.append({
                    "pair": pair,
                    "coin": coin,
                    "quantity": qty,
                    "price": price,
                    "usd_value": usd_value,
                })

        return candidates

    def get_allocation_pct(self, pair: str, portfolio: dict) -> float:
        total = portfolio.get("total_value", 0)
        if total <= 0:
            return 0
        return portfolio.get("asset_values", {}).get(pair, 0) / total

    def calculate_tiered_fixed_quantity(
        self,
        pair: str,
        price: float,
        portfolio: dict,
        available_usd: float = None,
    ) -> tuple:
        """Scale-In (DCA) strategy: buy fixed signal size until ceiling is hit.
        
        Each BUY signal intends to buy a fixed percentage (SIGNAL_SIZES[pair]).
        The allocation ceiling (MAX_ASSET_ALLOCATION_PCT) prevents overconcentration.
        
        Returns: (quantity, spend_used)
        """
        total = portfolio.get("total_value", 0)
        usd_cash = portfolio.get("usd_cash", 0)
        if total <= 0 or price <= 0:
            return 0, 0

        target_pct = config.SIGNAL_SIZES.get(pair, 0)
        if target_pct <= 0:
            return 0, 0

        # 1. The bot ALWAYS wants to buy the full signal size
        intended_buy_usd = total * target_pct

        # 2. Check current allocation against the absolute ceiling
        current_value = portfolio.get("asset_values", {}).get(pair, 0)
        current_pct = current_value / total if total > 0 else 0
        
        max_pct = config.MAX_ASSET_ALLOCATION_PCT
        remaining_pct = max(0.0, max_pct - current_pct)

        # If we are already at or above 20%, stop here.
        if remaining_pct <= 0:
            return 0, 0

        # 3. We buy the intended amount, OR whatever room is left before the ceiling
        max_allowed_usd = total * remaining_pct
        buy_usd = min(intended_buy_usd, max_allowed_usd)

        # 4. Enforce minimum trade limits
        if buy_usd < config.MIN_TRADE_USD:
            return 0, 0

        # 5. Check available cash (respecting the 10% cash buffer)
        min_cash = total * config.CASH_BUFFER_PCT
        available = (available_usd if available_usd is not None else (usd_cash - min_cash))
        
        if available <= 0:
            return 0, 0

        spend_used = min(buy_usd, available)
        if spend_used < config.MIN_TRADE_USD:
            return 0, 0

        quantity = spend_used / price
        return quantity, spend_used

    def calculate_buy_quantity(
        self,
        pair: str,
        price: float,
        portfolio: dict,
        available_usd: float = None,
    ) -> Tuple[float, float]:
        return self.calculate_tiered_fixed_quantity(
            pair=pair, price=price, portfolio=portfolio, available_usd=available_usd,
        )

    def record_entry(self, coin: str, new_qty: float, new_price: float, current_qty: float, sigma_level: float = None):
        """Record entry price after a buy is filled, using weighted average price."""
        old_price = self.entry_prices.get(coin, 0)

        if coin not in self.tranche_allocations:
            self.tranche_allocations[coin] = []

        tranche_record = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'quantity': new_qty,
            'price': new_price,
            'sigma_level': sigma_level,
        }
        self.tranche_allocations[coin].append(tranche_record)

        if current_qty > 0 and old_price > 0:
            total_cost = (current_qty * old_price) + (new_qty * new_price)
            self.entry_prices[coin] = total_cost / (current_qty + new_qty)
        else:
            self.entry_prices[coin] = new_price

        self.position_quantities[coin] = current_qty + new_qty

        log.info(
            f"Recorded entry for {coin}: new_qty={new_qty:.8f} new_price={new_price:.2f} "
            f"wgt_avg={self.entry_prices[coin]:.2f} total_qty={self.position_quantities[coin]:.8f}"
        )

        self.save_state()

    def clear_entry(self, coin: str):
        """Remove all position state after selling.
        
        FIX 3: also clears position_quantities and tranche_allocations to prevent
        stale data from corrupting weighted average price on re-entry.
        """
        self.entry_prices.pop(coin, None)
        self.position_quantities.pop(coin, None)
        self.tranche_allocations.pop(coin, None)
        self.save_state()

    def update_daily_close(self, value: float):
        self.yesterday_close = value
        self.save_state()
