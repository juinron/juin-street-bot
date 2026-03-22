"""Entry point — initializes all components, runs first cycle, starts scheduler."""

import signal
import sys
import logging

from logger import setup_console_logging, TradeLogger, PortfolioLogger
from api_client import RoostooClient
from portfolio import PortfolioManager
from risk_manager import RiskManager
from scheduler import create_scheduler, signal_loop

log = logging.getLogger(__name__)


def main():
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

    # Run initial cycle immediately
    log.info("Running initial signal loop...")
    signal_loop(client, pm, rm, trade_logger, portfolio_logger)

    # Set up scheduler
    scheduler = create_scheduler(client, pm, rm, trade_logger, portfolio_logger)
    scheduler.start()
    log.info("Scheduler started — signal loop every 2h, rebalance daily at 09:00 UTC")

    # Graceful shutdown handler
    def shutdown(signum, frame):
        log.info("Shutdown signal received — saving state and stopping...")
        pm.save_state()
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Block main thread
    log.info("Bot is running. Press Ctrl+C to stop.")
    try:
        while True:
            import time
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        shutdown(None, None)


if __name__ == "__main__":
    main()
