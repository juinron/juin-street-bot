"""Risk Manager — stop-loss, circuit breaker, daily loss limit, max drawdown."""

import logging

import config

log = logging.getLogger(__name__)


class RiskManager:
    """Evaluates risk conditions and gates trade execution."""

    def __init__(self, portfolio_manager):
        self.pm = portfolio_manager
        self.circuit_breaker_active = False
        self.daily_loss_active = False
        self.halted = False  # max drawdown halt

    def check_stop_loss(self, coin: str, current_price: float) -> bool:
        """Returns True if stop-loss triggered (price dropped 4%+ from entry)."""
        entry = self.pm.entry_prices.get(coin)
        if entry is None or entry <= 0:
            return False

        loss_pct = (entry - current_price) / entry
        if loss_pct >= config.STOP_LOSS_PCT:
            log.warning(
                f"STOP-LOSS triggered for {coin}: "
                f"entry={entry:.2f} current={current_price:.2f} loss={loss_pct:.2%}"
            )
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

    def check_max_drawdown(self, current_value: float) -> bool:
        """Returns True if all trading should halt (>15% below peak)."""
        if self.pm.peak_value <= 0:
            return False

        drawdown = (self.pm.peak_value - current_value) / self.pm.peak_value

        if drawdown >= config.MAX_DRAWDOWN_PCT:
            if not self.halted:
                log.critical(
                    f"MAX DRAWDOWN HALT: {drawdown:.2%} below peak "
                    f"(peak=${self.pm.peak_value:.2f}, current=${current_value:.2f}). "
                    f"All trading halted. Manual restart required."
                )
            self.halted = True
            return True

        return False

    def can_buy(self, current_value: float) -> bool:
        """Check all risk gates — returns True if buying is allowed."""
        if self.halted:
            log.info("Trading halted due to max drawdown — no buys")
            return False
        if self.check_circuit_breaker(current_value):
            log.info("Circuit breaker active — no buys")
            return False
        if self.check_daily_loss(current_value):
            log.info("Daily loss limit active — no buys")
            return False
        return True

    def can_sell(self) -> bool:
        """Sells are always allowed (to cut losses) unless max drawdown halted."""
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
