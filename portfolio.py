"""Portfolio — position tracking, allocation calculations, and state persistence."""

import json
import os
import logging

import config

log = logging.getLogger(__name__)


class PortfolioManager:
    """Tracks positions, entry prices, and calculates portfolio allocations."""

    def __init__(self):
        self.entry_prices = {}   # {coin: entry_price}
        self.starting_value = 0  # set on first run
        self.peak_value = 0      # track peak for max drawdown
        self.yesterday_close = 0 # daily close for daily loss limit
        self._load_state()

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

        wallet = balance_data.get("Wallet", {})
        usd_cash = wallet.get("USD", {}).get("Free", 0)
        log.debug(f"Wallet data: {wallet}")

        # Fetch current prices
        ticker_data = client.get_ticker()
        if not ticker_data or not ticker_data.get("Success"):
            log.error(f"Failed to fetch ticker for portfolio valuation: {ticker_data}")
            return {}

        log.debug(f"Ticker data keys: {list(ticker_data.get('Data', {}).keys())}")

        prices = {}
        asset_values = {}
        balances = {}
        held_assets = set()
        total_value = usd_cash

        for pair in config.ASSETS:
            coin = pair.split("/")[0]
            coin_balance = wallet.get(coin, {}).get("Free", 0)
            coin_locked = wallet.get(coin, {}).get("Lock", 0)
            total_coin = coin_balance + coin_locked
            balances[coin] = coin_balance

            ticker = ticker_data.get("Data", {}).get(pair, {})
            last_price = ticker.get("LastPrice", 0)
            prices[pair] = last_price

            if not ticker:
                log.warning(f"{pair}: not found in ticker data — pair may not be listed")

            value = total_coin * last_price
            asset_values[pair] = value
            total_value += value

            if coin_balance > 0:
                held_assets.add(coin)

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
            "asset_values": asset_values,
            "held_assets": held_assets,
        }

    def get_allocation_pct(self, pair: str, portfolio: dict) -> float:
        """Current allocation percentage for an asset."""
        total = portfolio.get("total_value", 0)
        if total <= 0:
            return 0
        return portfolio.get("asset_values", {}).get(pair, 0) / total

    def calculate_buy_quantity(
        self, pair: str, price: float, portfolio: dict
    ) -> float:
        """How much to buy to reach target allocation, respecting cash buffer."""
        total = portfolio["total_value"]
        usd_cash = portfolio["usd_cash"]

        target_usd = total * config.TARGET_ALLOCATION_PCT
        current_value = portfolio["asset_values"].get(pair, 0)
        spend = target_usd - current_value

        if spend <= 0:
            return 0

        # Respect cash buffer
        min_cash = total * config.CASH_BUFFER_PCT
        available = usd_cash - min_cash
        if available <= 0:
            log.info(f"Cash buffer would be breached, skipping buy for {pair}")
            return 0

        spend = min(spend, available)
        quantity = spend / price if price > 0 else 0
        return quantity

    def calculate_rebalance_trades(self, portfolio: dict) -> list:
        """Compare actual vs target allocations, return needed trades.
        Returns list of dicts: {pair, side, quantity, price, reason}
        """
        trades = []
        total = portfolio["total_value"]
        if total <= 0:
            return trades

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
                min_cash = total * config.CASH_BUFFER_PCT
                available = portfolio["usd_cash"] - min_cash
                buy_usd = min(buy_usd, max(available, 0))
                buy_qty = buy_usd / price
                if buy_qty > 0:
                    trades.append({
                        "pair": pair,
                        "side": "BUY",
                        "quantity": buy_qty,
                        "price": price * config.BUY_LIMIT_OFFSET,
                        "reason": f"Rebalance: {actual_pct:.1%} → {config.TARGET_ALLOCATION_PCT:.1%}",
                    })

        return trades

    def record_entry(self, coin: str, price: float):
        """Record entry price after a buy is filled."""
        self.entry_prices[coin] = price
        self.save_state()

    def clear_entry(self, coin: str):
        """Remove entry price after selling."""
        self.entry_prices.pop(coin, None)
        self.save_state()

    def update_daily_close(self, value: float):
        """Set yesterday's close value for daily loss tracking."""
        self.yesterday_close = value
        self.save_state()
