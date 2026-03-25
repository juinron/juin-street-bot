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
        self.entry_prices = {}   # {coin: entry_price}
        self.starting_value = 0  # set on first run
        self.peak_value = 0      # track peak for max drawdown
        self.yesterday_close = 0 # daily close for daily loss limit
        self._pair_rules = {}     # {pair: {"price_precision", "amount_precision", "mini_order"}}
        self._pair_rules_ts = 0.0 # last exchangeInfo refresh time
        self.position_quantities = {}  # {coin: total_qty} — track quantity for each position
        self.tranche_allocations = {}  # {coin: [list of tranche dicts]} — record each tranche separately
        self._load_state()

    def get_pair_rules(self, client, refresh_after_seconds: float = 3600) -> dict:
        """Fetch and cache per-pair precision rules from exchangeInfo."""
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
        """Round down to a fixed decimal precision (safe for step sizes)."""
        if precision is None:
            return float(value)
        if precision < 0:
            return float(value)
        d = Decimal(str(value))
        quant = Decimal("1").scaleb(-precision)  # 10 ** (-precision)
        return float(d.quantize(quant, rounding=ROUND_DOWN))

    def _load_state(self):
        """Restore state from state.json if it exists."""
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
        """Persist current state to state.json."""
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
        """Fetch balances and ticker prices, return portfolio summary.
        Returns: {
            'total_value': float,
            'usd_cash': float,
            'balances': {coin: free_amount},
            'prices': {pair: last_price},
            'asset_values': {pair: usd_value},
            'held_assets': set of coins with non-zero balances,
        }
        """
        balance_data = client.get_balance()
        if not balance_data or not balance_data.get("Success"):
            log.error(f"Failed to fetch balance: {balance_data}")
            return {}

        wallet = balance_data.get("SpotWallet", balance_data.get("Wallet", {}))
        usd_free = wallet.get("USD", {}).get("Free", 0)
        usd_locked = wallet.get("USD", {}).get("Lock", 0)
        usd_cash = usd_free + usd_locked # Total USD value
        log.debug(f"Wallet data: {wallet}")

        # Fetch current prices
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
            max_bid = ticker.get("MaxBid", last_price)  # fallback to last_price if not available
            min_ask = ticker.get("MinAsk", last_price)
            
            prices[pair] = last_price
            max_bids[pair] = max_bid
            min_asks[pair] = min_ask

            if not ticker:
                log.warning(f"{pair}: not found in ticker data — pair may not be listed")

            value = total_coin * last_price
            asset_values[pair] = value
            total_value += value

            # Skip “dust” positions as held assets to avoid repeated unnecessary signals.
            if total_coin > 0 and value >= config.DUST_THRESHOLD_USD:
                held_assets.add(coin)
            elif total_coin > 0:
                log.debug(
                    f"{pair}: dust position ({total_coin:.8f} {coin}, ${value:.2f}) below "
                    f"DUST_THRESHOLD_USD={config.DUST_THRESHOLD_USD}, not marking held"
                )

        # Initialize starting value on first run
        if self.starting_value == 0:
            self.starting_value = total_value
            log.info(f"Starting portfolio value: ${total_value:.2f}")

        # Update peak
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
        """Return dust-sized coin entries that can be cleaned up.

        dust candidates are positive balances whose USD value is below DUST_THRESHOLD_USD.
        """
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
        """Current allocation percentage for an asset."""
        total = portfolio.get("total_value", 0)
        if total <= 0:
            return 0
        return portfolio.get("asset_values", {}).get(pair, 0) / total

    def calculate_buy_quantity(
        self,
        pair: str,
        price: float,
        portfolio: dict,
        available_usd: float = None,
    ) -> Tuple[float, float]:
        """
        How much to buy to reach target allocation, respecting cash buffer.

        Returns: (quantity, spend_used_usd)
        """
        total = portfolio["total_value"]
        usd_cash = portfolio["usd_cash"]

        target_usd = total * config.TARGET_ALLOCATION_PCT
        current_value = portfolio["asset_values"].get(pair, 0)
        spend = target_usd - current_value

        if spend <= 0:
            return 0, 0

        # Avoid creating a tiny purchase that later becomes dust.
        if spend < config.MIN_TRADE_USD:
            log.info(
                f"calculate_buy_quantity: spend ${spend:.2f} below MIN_TRADE_USD={config.MIN_TRADE_USD}, skip"
            )
            return 0, 0

        # Respect cash buffer (optionally override with a shared "remaining cash" during a run)
        min_cash = total * config.CASH_BUFFER_PCT
        available = (available_usd if available_usd is not None else (usd_cash - min_cash))
        if available <= 0:
            log.info(f"Cash buffer would be breached, skipping buy for {pair}")
            return 0, 0

        spend_used = min(spend, available)
        quantity = spend_used / price if price > 0 else 0
        return quantity, spend_used

    def calculate_tranche_quantity(
        self,
        pair: str,
        price: float,
        portfolio: dict,
        sigma_level: float,
        available_usd: float = None,
    ) -> Tuple[float, float, float]:
        """
        Calculate quantity for tranche buying based on price deviation from SMA.
        
        Returns: (quantity, spend_used_usd, tranche_allocation_pct)
        
        Logic:
        - If price is at 2-sigma: allocate TRANCHE_LEVELS[2] of target allocation
        - If price is at 2.5-sigma: allocate TRANCHE_LEVELS[2.5] of target allocation
        - etc.
        - Respects MAX_ASSET_ALLOCATION_PCT limit
        """
        total = portfolio["total_value"]
        usd_cash = portfolio["usd_cash"]
        coin = pair.split("/")[0]
        
        if price <= 0:
            return 0, 0, 0
        
        # Determine tranche level (round sigma to nearest discrete level)
        # e.g., if TRANCHE_LEVELS = {2: 0.05, 2.5: 0.05, 3: 0.05}
        # and sigma_level = 2.3, use level 2 (5% allocation)
        tranche_pct = 0
        matched_sigma = None
        for level in sorted(config.TRANCHE_LEVELS.keys()):
            if sigma_level >= level:
                tranche_pct = config.TRANCHE_LEVELS[level]
                matched_sigma = level
        
        if tranche_pct <= 0:
            log.debug(f"{pair}: sigma_level={sigma_level:.2f} doesn't match any tranche level")
            return 0, 0, 0
        
        # Calculate allocation for this tranche
        current_value = portfolio["asset_values"].get(pair, 0)
        current_pct = current_value / total if total > 0 else 0
        
        # Don't exceed max allocation
        if current_pct + tranche_pct > config.MAX_ASSET_ALLOCATION_PCT:
            remaining_alloc = max(0, config.MAX_ASSET_ALLOCATION_PCT - current_pct)
            tranche_pct = min(tranche_pct, remaining_alloc)
        
        if tranche_pct <= 0:
            log.info(f"{pair}: already at max allocation ({current_pct:.1%}), no tranche buy")
            return 0, 0, 0
        
        # Calculate spend for this tranche
        tranche_usd = total * tranche_pct
        
        # Respect minimum trade size
        if tranche_usd < config.MIN_TRADE_USD:
            log.debug(f"{pair}: tranche spend ${tranche_usd:.2f} below MIN_TRADE_USD")
            return 0, 0, 0
        
        # Respect cash buffer
        min_cash = total * config.CASH_BUFFER_PCT
        available = (available_usd if available_usd is not None else (usd_cash - min_cash))
        if available <= 0:
            log.info(f"Cash buffer would be breached, skipping tranche for {pair}")
            return 0, 0, 0
        
        spend_used = min(tranche_usd, available)
        quantity = spend_used / price
        
        log.info(
            f"{pair}: tranche buy at sigma={matched_sigma} "
            f"allocating {tranche_pct:.1%} (${spend_used:.2f}) qty={quantity:.8f}"
        )
        
        return quantity, spend_used, tranche_pct

    def calculate_rebalance_trades(self, portfolio: dict) -> list:
        """Compare actual vs target allocations, return needed trades.
        Returns list of dicts: {pair, side, quantity, price, reason}
        """
        trades = []
        total = portfolio["total_value"]
        if total <= 0:
            return trades

        # Track remaining USD across multiple BUY trades in this same rebalance run.
        min_cash = total * config.CASH_BUFFER_PCT
        available_cash = portfolio["usd_cash"] - min_cash

        for pair in config.ASSETS:
            actual_pct = self.get_allocation_pct(pair, portfolio)
            drift = actual_pct - config.TARGET_ALLOCATION_PCT

            if abs(drift) < config.REBALANCE_DRIFT_PCT:
                continue

            price = portfolio["prices"].get(pair, 0)
            if price <= 0:
                continue

            coin = pair.split("/")[0]
            drift_usd = drift * total

            # Do not buy into an asset we don't currently hold.
            # Let the signal_loop handle entries.
            if coin not in portfolio.get("held_assets", set()) and drift < 0:
                continue

            if drift > 0:
                # Over-allocated: sell the excess
                sell_qty = drift_usd / price
                if sell_qty > 0:
                    trades.append({
                        "pair": pair,
                        "side": "SELL",
                        "quantity": sell_qty,
                        "price": price * config.SELL_LIMIT_OFFSET,
                        "reason": f"Rebalance: {actual_pct:.1%} → {config.TARGET_ALLOCATION_PCT:.1%}",
                    })
            else:
                # Under-allocated: buy to top up (respecting cash buffer)
                buy_usd = abs(drift_usd)
                if available_cash <= 0:
                    continue

                spend_used = min(buy_usd, available_cash)
                available_cash -= spend_used

                buy_qty = spend_used / price if price > 0 else 0
                if buy_qty > 0 and spend_used > 0:
                    trades.append({
                        "pair": pair,
                        "side": "BUY",
                        "quantity": buy_qty,
                        "price": price * config.BUY_LIMIT_OFFSET,
                        "reason": f"Rebalance: {actual_pct:.1%} → {config.TARGET_ALLOCATION_PCT:.1%}",
                    })

        return trades

    def record_entry(self, coin: str, new_qty: float, new_price: float, current_qty: float, sigma_level: float = None):
        """Record entry price after a buy is filled, using weighted average price.
        
        Supports tranche buying:
        - Each fill is recorded with its price
        - Weighted average is maintained across all tranches
        - sigma_level tracks which deviation level triggered this tranche
        
        Args:
            coin: Asset symbol (e.g., 'BTC')
            new_qty: Quantity purchased in this fill
            new_price: Price at which this fill executed
            current_qty: Current portfolio quantity (before this fill)
            sigma_level: Statistical deviation level (for tranche tracking)
        """
        old_price = self.entry_prices.get(coin, 0)
        
        # Initialize tranche record if not present
        if coin not in self.tranche_allocations:
            self.tranche_allocations[coin] = []
        
        # Record this tranche
        tranche_record = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'quantity': new_qty,
            'price': new_price,
            'sigma_level': sigma_level,
        }
        self.tranche_allocations[coin].append(tranche_record)
        
        # Weighted average entry price across all tranches
        if current_qty > 0 and old_price > 0:
            total_cost = (current_qty * old_price) + (new_qty * new_price)
            self.entry_prices[coin] = total_cost / (current_qty + new_qty)
        else:
            self.entry_prices[coin] = new_price
        
        # Track position quantity
        self.position_quantities[coin] = current_qty + new_qty
        
        log.info(
            f"Recorded entry for {coin}: new_qty={new_qty:.8f} new_price={new_price:.2f} "
            f"wgt_avg={self.entry_prices[coin]:.2f} total_qty={self.position_quantities[coin]:.8f}"
        )
        
        self.save_state()

    def clear_entry(self, coin: str):
        """Remove entry price after selling."""
        self.entry_prices.pop(coin, None)
        self.save_state()

    def update_daily_close(self, value: float):
        """Set yesterday's close value for daily loss tracking."""
        self.yesterday_close = value
        self.save_state()
