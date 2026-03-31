"""Risk Manager — stop-loss, circuit breaker, daily loss limit, max drawdown."""

import logging
import time

import config

log = logging.getLogger(__name__)


class RiskManager:
    """Evaluates risk conditions and gates trade execution."""

    def __init__(self, portfolio_manager):
        self.pm = portfolio_manager
        self.circuit_breaker_active = False
        self.daily_loss_active = False
        self.halted = False  # max drawdown halt
        # Restore stop-loss cooldown state from persisted state (survives restarts)
        self.last_stop_loss_time = dict(self.pm.stop_loss_cooldown_times)
        self.last_stop_loss_price = dict(self.pm.stop_loss_cooldown_prices)

    def check_stop_loss(self, coin: str, current_price: float, atr: float = None) -> bool:
        """Returns True if stop-loss triggered (ATR-based, or legacy 4% fallback)."""
        entry = self.pm.entry_prices.get(coin)
        if entry is None or entry <= 0:
            return False

        if atr is not None and atr > 0:
            stop_loss_price = entry - (config.ATR_MULTIPLIER * atr)
            triggered = current_price <= stop_loss_price
            mode = f"atr={atr:.4f} stop_level={stop_loss_price:.2f}"
        else:
            triggered = (entry - current_price) / entry >= 0.04
            mode = "legacy 4%"

        if triggered:
            loss_pct = (entry - current_price) / entry
            log.warning(
                f"STOP-LOSS triggered for {coin} ({mode}): "
                f"entry={entry:.2f} current={current_price:.2f} loss={loss_pct:.2%}"
            )
            self.last_stop_loss_time[coin] = time.time()
            self.last_stop_loss_price[coin] = current_price
            self._sync_cooldown_to_state()
            return True

        return False

    def check_circuit_breaker(self, current_value: float) -> bool:
        """Returns True if buys should be paused (portfolio down >10% from start).
        Resumes when portfolio recovers above -8% from start.
        """
        if self.pm.starting_value <= 0:
            return False

        drop_pct = (self.pm.starting_value - current_value) / self.pm.starting_value

        if drop_pct >= config.CIRCUIT_BREAKER_PAUSE_PCT:
            if not self.circuit_breaker_active:
                log.warning(
                    f"CIRCUIT BREAKER activated: portfolio down {drop_pct:.2%} from start"
                )
            self.circuit_breaker_active = True
            return True

        if self.circuit_breaker_active and drop_pct < config.CIRCUIT_BREAKER_RESUME_PCT:
            log.info(
                f"CIRCUIT BREAKER deactivated: portfolio recovered to {drop_pct:.2%} from start"
            )
            self.circuit_breaker_active = False

        return self.circuit_breaker_active

    def check_daily_loss(self, current_value: float) -> bool:
        """Returns True if buys should be blocked for the rest of the day."""
        if self.pm.yesterday_close <= 0:
            return False

        daily_drop = (self.pm.yesterday_close - current_value) / self.pm.yesterday_close

        if daily_drop >= config.DAILY_LOSS_LIMIT_PCT:
            if not self.daily_loss_active:
                log.warning(f"DAILY LOSS LIMIT hit: down {daily_drop:.2%} vs yesterday close")
            self.daily_loss_active = True
            return True

        return self.daily_loss_active

    def check_stop_loss_cooldown(self, coin: str, current_price: float = None) -> bool:
        """Returns True if the asset is still in cooldown after a stop-loss.

        Two gates must BOTH pass before re-entry is allowed:
        1. Time gate: STOP_LOSS_COOLDOWN_MINUTES must have elapsed
        2. Price recovery gate: price must have recovered STOP_LOSS_RECOVERY_PCT
           above the stop-loss exit price

        Args:
            coin: Asset identifier (e.g., 'BTC')
            current_price: Current market price (for recovery gate check)

        Returns:
            True if still in cooldown (buy should be blocked)
        """
        if coin not in self.last_stop_loss_time:
            return False

        # Gate 1: Time cooldown
        elapsed_seconds = time.time() - self.last_stop_loss_time[coin]
        cooldown_seconds = config.STOP_LOSS_COOLDOWN_MINUTES * 60

        if elapsed_seconds < cooldown_seconds:
            minutes_remaining = (cooldown_seconds - elapsed_seconds) / 60
            log.info(
                f"{coin}: stop-loss cooldown active ({minutes_remaining:.1f}m remaining), skipping buy"
            )
            return True

        # Gate 2: Price recovery — price must be above stop level + recovery margin
        stop_price = self.last_stop_loss_price.get(coin)
        recovery_pct = config.STOP_LOSS_RECOVERY_PCT
        if stop_price and current_price and current_price < stop_price * (1 + recovery_pct):
            log.info(
                f"{coin}: stop-loss cooldown expired but price {current_price:.4f} "
                f"hasn't recovered above {stop_price * (1 + recovery_pct):.4f} "
                f"(stop={stop_price:.4f} + {recovery_pct:.0%}), skipping buy"
            )
            return True

        # Both gates passed — clear records and allow re-entry
        del self.last_stop_loss_time[coin]
        self.last_stop_loss_price.pop(coin, None)
        self._sync_cooldown_to_state()
        log.info(f"{coin}: stop-loss cooldown expired and price recovered, buys re-enabled")
        return False

    def check_max_drawdown(self, current_value: float) -> bool:
        """Returns True if new buys should halt (>15% below peak).
        Automatically resumes when drawdown recovers below threshold.
        """
        if self.pm.peak_value <= 0:
            return False

        drawdown = (self.pm.peak_value - current_value) / self.pm.peak_value

        if drawdown >= config.MAX_DRAWDOWN_PCT:
            if not self.halted:
                log.critical(
                    f"MAX DRAWDOWN HALT: {drawdown:.2%} below peak "
                    f"(peak=${self.pm.peak_value:.2f}, current=${current_value:.2f}). "
                    f"New buys halted — stop-losses remain active."
                )
            self.halted = True
            return True

        if self.halted:
            log.info(
                f"MAX DRAWDOWN recovered: {drawdown:.2%} below peak "
                f"(< {config.MAX_DRAWDOWN_PCT:.0%} threshold). Buys re-enabled."
            )
            self.halted = False

        return False

    def _sync_cooldown_to_state(self):
        """Persist stop-loss cooldown data through PortfolioManager state."""
        self.pm.stop_loss_cooldown_times = dict(self.last_stop_loss_time)
        self.pm.stop_loss_cooldown_prices = dict(self.last_stop_loss_price)
        self.pm.save_state()

    def can_buy(self, current_value: float, coin: str = None, current_price: float = None) -> bool:
        """Check all risk gates — returns True if buying is allowed.

        Args:
            current_value: Current portfolio value
            coin: Optional asset identifier to check per-asset stop-loss cooldown
            current_price: Current asset price (for stop-loss recovery gate)

        Returns:
            True if buying is permitted for this asset/portfolio
        """
        if self.halted:
            log.info("Trading halted due to max drawdown — no buys")
            return False
        if self.check_circuit_breaker(current_value):
            log.info("Circuit breaker active — no buys")
            return False
        if self.check_daily_loss(current_value):
            log.info("Daily loss limit active — no buys")
            return False
        if coin and self.check_stop_loss_cooldown(coin, current_price):
            # Per-asset cooldown + price recovery gate after stop-loss
            return False
        return True

    def can_sell(self) -> bool:
        """Returns True if a signal-based take-profit SELL is permitted.

        Note: ATR stop-losses in execute_stop_losses() bypass this gate and always
        execute regardless of halt state, so positions can always be cut at the
        stop level. This gate only applies to RSI Z-score take-profit exits.
        """
        return not self.halted

    def get_drawdown_pct(self, current_value: float) -> float:
        """Current drawdown from peak as a decimal (0.05 = 5% below peak)."""
        if self.pm.peak_value <= 0:
            return 0
        return (self.pm.peak_value - current_value) / self.pm.peak_value

    def get_daily_return_pct(self, current_value: float) -> float:
        """Daily return as a decimal relative to yesterday's close."""
        if self.pm.yesterday_close <= 0:
            return 0
        return (current_value - self.pm.yesterday_close) / self.pm.yesterday_close

    def reset_daily(self):
        """Reset daily loss flag — called at midnight UTC."""
        self.daily_loss_active = False
        log.info("Daily loss limit reset")
