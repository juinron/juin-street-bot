"""Logging — CSV trade/portfolio loggers and console logging setup."""

import csv
import os
import logging
from datetime import datetime, timezone

import config


def setup_console_logging():
    """Configure root logger with timestamped console output."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


class TradeLogger:
    """Appends trade events to trades_log.csv."""

    COLUMNS = [
        "timestamp", "asset", "action", "order_type", "quantity",
        "price", "order_id", "status", "reason", "portfolio_value_at_time",
    ]

    def __init__(self, filepath: str = None):
        self.filepath = filepath or config.TRADES_LOG_FILE
        self._ensure_header()

    def _ensure_header(self):
        if not os.path.exists(self.filepath):
            with open(self.filepath, "w", newline="") as f:
                csv.writer(f).writerow(self.COLUMNS)

    def log(
        self, asset: str, action: str, order_type: str = "",
        quantity: float = 0, price: float = 0, order_id: str = "",
        status: str = "", reason: str = "", portfolio_value: float = 0,
    ):
        row = [
            datetime.now(timezone.utc).isoformat(),
            asset, action, order_type, quantity, price,
            order_id, status, reason, round(portfolio_value, 2),
        ]
        with open(self.filepath, "a", newline="") as f:
            csv.writer(f).writerow(row)
        logging.getLogger(__name__).info(
            f"{action} {asset} | qty={quantity} price={price} | {reason}"
        )


class PortfolioLogger:
    """Appends portfolio snapshots to portfolio_snapshots.csv."""

    def __init__(self, filepath: str = None):
        self.filepath = filepath or config.PORTFOLIO_LOG_FILE
        # Build columns dynamically from config
        self.asset_columns = [f"{pair.split('/')[0].lower()}_value" 
                              for pair in config.ASSETS]
        self.COLUMNS = (
            ["timestamp", "total_value_usd"]
            + self.asset_columns
            + ["usd_cash", "daily_return_pct", "drawdown_from_peak_pct"]
        )
        self._ensure_header()

    def _ensure_header(self):
        if not os.path.exists(self.filepath):
            with open(self.filepath, "w", newline="") as f:
                csv.writer(f).writerow(self.COLUMNS)

    def log(
        self, total_value: float, asset_values: dict,
        usd_cash: float, daily_return_pct: float, drawdown_pct: float,
    ):
        asset_rows = [round(asset_values.get(pair, 0), 2) 
                      for pair in config.ASSETS]
        row = (
            [datetime.now(timezone.utc).isoformat(), round(total_value, 2)]
            + asset_rows
            + [round(usd_cash, 2), round(daily_return_pct, 4), round(drawdown_pct, 4)]
        )
        with open(self.filepath, "a", newline="") as f:
            csv.writer(f).writerow(row)
        logging.getLogger(__name__).info(
            f"Portfolio snapshot: ${total_value:.2f} | "
            f"daily={daily_return_pct:+.2%} | drawdown={drawdown_pct:+.2%}"
        )
