"""Entry point — initializes all components, runs first cycle, starts scheduler."""

import os
import signal
import sys
import time
import logging

import config
from logger import setup_console_logging, TradeLogger, PortfolioLogger
from api_client import RoostooClient
from portfolio import PortfolioManager
from risk_manager import RiskManager
from scheduler import create_scheduler, signal_loop
from strategy import bootstrap_price_history

log = logging.getLogger(__name__)

# Flag to prevent double-shutdown on Ctrl+C
_shutting_down = False


def main():
    global _shutting_down

    setup_console_logging()
    log.info("Juin Street Bot starting up...")

    # Validate config
    from config import API_KEY, API_SECRET
    if not API_KEY or not API_SECRET or "your_" in API_KEY:
        log.error("API_KEY and API_SECRET must be set in .env file")
        sys.exit(1)

    # Initialize components
    client = RoostooClient()
    pm = PortfolioManager()
    rm = RiskManager(pm)
    trade_logger = TradeLogger()
    portfolio_logger = PortfolioLogger()

    # Verify API connectivity
    server_time = client.get_server_time()
    if server_time:
        log.info(f"Connected to Roostoo API (server time: {server_time})")
    else:
        log.error("Failed to connect to Roostoo API — check credentials and network")
        sys.exit(1)

    # Bootstrap candle data from Binance so signals fire immediately
    bootstrap_price_history(client)

    # Run initial cycle immediately
    log.info("Running initial signal loop...")
    signal_loop(client, pm, rm, trade_logger, portfolio_logger)

    # Set up scheduler
    scheduler = create_scheduler(client, pm, rm, trade_logger, portfolio_logger)
    scheduler.start()
    log.info(
        "Scheduler started — signal loop every "
        f"{config.SIGNAL_LOOP_MINUTES}m, rebalance daily at "
        f"{config.DAILY_REBALANCE_HOUR:02d}:00 UTC"
    )

    # Graceful shutdown handler (guarded against double-trigger)
    def shutdown(signum, frame):
        global _shutting_down
        if _shutting_down:
            return
        _shutting_down = True

        log.info("Shutdown signal received — saving state and stopping...")
        pm.save_state()
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    # SIGTERM is not available on Windows
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    # Block main thread
    log.info("Bot is running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        shutdown(None, None)


if __name__ == "__main__":
    main()
