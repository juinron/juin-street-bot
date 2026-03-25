"""Scheduler — APScheduler jobs for signal loop, daily rebalance, and midnight reset."""

import logging
import threading
from apscheduler.schedulers.background import BackgroundScheduler

import config
from api_client import RoostooClient
from portfolio import PortfolioManager
from risk_manager import RiskManager
from strategy import (
    collect_price_snapshot, compute_signal, load_price_history,
    compute_atr, compute_rsi_zscore, compute_rsi, compute_bollinger_bands
)
from logger import TradeLogger, PortfolioLogger

log = logging.getLogger(__name__)

# Prevents signal_loop and daily_rebalance from running simultaneously
_trading_lock = threading.Lock()


def calculate_spread_aware_limit_price(
    side: str,
    mid_price: float,
    max_bid: float,
    min_ask: float,
    tick_size: float = 1,
    price_precision: int = 4,
) -> float:
    """Calculate limit price to quote inside the bid-ask spread (maker execution).
    
    Acts as a maker by:
    - BUY: place limit at max_bid + tick (slightly above bid, below mid)
    - SELL: place limit at min_ask - tick (slightly below ask, above mid)
    
    This avoids paying the spread premium while improving fill probability.
    
    Args:
        side: 'BUY' or 'SELL'
        mid_price: Current market mid-price
        max_bid: Current bid price
        min_ask: Current ask price
        tick_size: Smallest price increment (usually 1 for crypto in USD)
        price_precision: Decimal places to round to
    
    Returns:
        Limit price as float, rounded to price precision
    """
    pm = PortfolioManager()  # temporary, just for floor_to_precision
    
    if side.upper() == "BUY":
        # Buy at bid + tick, but not above mid-price
        limit_price = min(max_bid + tick_size / (10 ** price_precision), mid_price)
    else:  # SELL
        # Sell at ask - tick, but not below mid-price
        limit_price = max(min_ask - tick_size / (10 ** price_precision), mid_price)
    
    # Round to price precision
    limit_price = pm.floor_to_precision(limit_price, price_precision)
    return limit_price


def cancel_stale_orders(client: RoostooClient, trade_logger: TradeLogger, portfolio_value: float):
    """Cancel any pending orders older than STALE_ORDER_HOURS."""
    import time
    result = client.query_order(pending_only=True)
    if not result or not result.get("Success"):
        return

    stale_ms = config.STALE_ORDER_HOURS * 3600 * 1000
    now_ms = int(time.time() * 1000)

    for order in result.get("OrderMatched", []):
        if order.get("Status") != "PENDING":
            continue
        created = order.get("CreateTimestamp", 0)
        if now_ms - created > stale_ms:
            order_id = order["OrderID"]
            cancel_result = client.cancel_order(order_id=order_id)
            if cancel_result and cancel_result.get("Success"):
                trade_logger.log(
                    asset=order.get("Pair", ""),
                    action="CANCEL",
                    order_id=str(order_id),
                    status="CANCELED",
                    reason=f"Stale order (>{config.STALE_ORDER_HOURS}h unfilled)",
                    portfolio_value=portfolio_value,
                )
                log.info(f"Canceled stale order {order_id}")


def execute_stop_losses(
    client: RoostooClient,
    pm: PortfolioManager,
    rm: RiskManager,
    portfolio: dict,
    trade_logger: TradeLogger,
    pair_rules: dict,
):
    """Check all held positions for dynamic (ATR-based) stop-loss and execute market sells."""
    for pair in config.ASSETS:
        coin = pair.split("/")[0]
        price = portfolio["prices"].get(pair, 0)

        if coin not in portfolio["held_assets"] or price <= 0:
            continue

        # Compute ATR for this asset to use in dynamic stop-loss check
        df = load_price_history(pair)
        atr = None
        
        if len(df) >= config.ATR_PERIOD:
            import pandas as pd
            df_tail = df.tail(200).copy()
            close = df_tail["last_price"].astype(float)
            high = df_tail["max_bid"].astype(float)
            low = df_tail["min_ask"].astype(float)
            
            # Fallback if bid/ask unavailable
            if high.abs().sum() < 1e-10:
                high = close
            if low.abs().sum() < 1e-10:
                low = close
            
            atr_series = compute_atr(high, low, close)
            atr = atr_series.iloc[-1]
            
            if pd.isna(atr):
                atr = None

        # Check stop-loss with ATR (dynamic) or fallback to legacy 4%
        if rm.check_stop_loss(coin, price, atr):
            quantity = portfolio["balances"].get(coin, 0)
            if quantity <= 0:
                continue
            amount_precision = pair_rules.get(pair, {}).get("amount_precision", 6)
            quantity = pm.floor_to_precision(quantity, int(amount_precision))
            if quantity <= 0:
                continue

            mini_order = float(pair_rules.get(pair, {}).get("mini_order", 0) or 0)
            if mini_order and (quantity * price) < mini_order:
                continue

            result = client.place_order(
                pair,
                "SELL",
                quantity,
                order_type="MARKET",
            )
            if result and result.get("Success"):
                detail = result.get("OrderDetail", {})
                trade_logger.log(
                    asset=pair, action="SELL", order_type="MARKET",
                    quantity=quantity, price=detail.get("FilledAverPrice", price),
                    order_id=str(detail.get("OrderID", "")),
                    status=detail.get("Status", ""),
                    reason="Stop-loss triggered (ATR-based)" if atr else "Stop-loss triggered (legacy 4%)",
                    portfolio_value=portfolio["total_value"],
                )
                pm.clear_entry(coin)
            else:
                trade_logger.log(
                    asset=pair, action="ERROR", reason="Stop-loss market sell failed",
                    portfolio_value=portfolio["total_value"],
                )


def liquidate_dust_positions(
    client: RoostooClient,
    pm: PortfolioManager,
    portfolio: dict,
    trade_logger: TradeLogger,
    pair_rules: dict,
):
    """Convert dust (< DUST_THRESHOLD_USD) holdings to USD to prevent accumulation."""
    if not config.DUST_SELL_ENABLED:
        return

    dust_candidates = pm.get_dust_candidates(portfolio)
    for candidate in dust_candidates:
        pair = candidate["pair"]
        coin = candidate["coin"]
        quantity = candidate["quantity"]
        price = candidate["price"]

        rules = pair_rules.get(pair, {})
        amount_precision = rules.get("amount_precision", 6)
        quantity = pm.floor_to_precision(quantity, int(amount_precision))

        if quantity <= 0:
            continue

        mini_order = float(rules.get("mini_order", 0) or 0)
        if mini_order and (quantity * price) < mini_order:
            log.debug(f"{pair}: dust ${candidate['usd_value']:.2f} below mini_order, skipping")
            continue

        log.info(f"{pair}: liquidating dust {quantity:.8f} ({candidate['usd_value']:.2f} USD)")
        result = client.place_order(
            pair,
            "SELL",
            quantity,
            order_type=config.DUST_SELL_ORDER_TYPE,
        )

        if result and result.get("Success"):
            detail = result.get("OrderDetail", {})
            trade_logger.log(
                asset=pair,
                action="SELL",
                order_type=config.DUST_SELL_ORDER_TYPE,
                quantity=quantity,
                price=detail.get("FilledAverPrice", price),
                order_id=str(detail.get("OrderID", "")),
                status=detail.get("Status", ""),
                reason="Dust cleanup",
                portfolio_value=portfolio.get("total_value", 0),
            )
            pm.clear_entry(coin)
        else:
            trade_logger.log(
                asset=pair,
                action="ERROR",
                reason="Dust cleanup sell failed",
                portfolio_value=portfolio.get("total_value", 0),
            )


def signal_loop(
    client: RoostooClient, pm: PortfolioManager, rm: RiskManager,
    trade_logger: TradeLogger, portfolio_logger: PortfolioLogger,
):
    """Main signal loop: cancel stale orders → collect prices → evaluate signals → trade."""
    if not _trading_lock.acquire(blocking=False):
        log.warning("Signal loop skipped — another trading job is running")
        return
    try:
        _signal_loop_inner(client, pm, rm, trade_logger, portfolio_logger)
    except Exception as e:
        log.error(f"Signal loop failed with unexpected error: {e}", exc_info=True)
    finally:
        _trading_lock.release()


def _signal_loop_inner(
    client: RoostooClient, pm: PortfolioManager, rm: RiskManager,
    trade_logger: TradeLogger, portfolio_logger: PortfolioLogger,
):
    """Inner signal loop logic, called with trading lock held."""
    log.info("=" * 60)
    log.info("Signal loop starting")

    # Fetch portfolio
    portfolio = pm.fetch_portfolio(client)
    if not portfolio:
        log.error("Could not fetch portfolio, skipping cycle")
        return

    total_value = portfolio["total_value"]

    # Check max drawdown halt
    if rm.check_max_drawdown(total_value):
        log.critical("MAX DRAWDOWN — all trading halted")
        _log_snapshot(pm, rm, portfolio, portfolio_logger)
        return

    # Cancel stale orders
    cancel_stale_orders(client, trade_logger, total_value)

    # Collect price snapshot for strategy history
    collect_price_snapshot(client)

    # Fetch precision rules once per run (cached inside PortfolioManager)
    pair_rules = pm.get_pair_rules(client)

    # Liquidate dust positions before main signal trading, to prevent accumulation
    liquidate_dust_positions(client, pm, portfolio, trade_logger, pair_rules)

    # Execute stop-losses first
    execute_stop_losses(client, pm, rm, portfolio, trade_logger, pair_rules)

    # Re-fetch portfolio after any stop-loss sells
    portfolio = pm.fetch_portfolio(client)
    if not portfolio:
        return
    total_value = portfolio["total_value"]

    # Evaluate signals for each asset
    available_usd = max(0.0, portfolio["usd_cash"] - (total_value * config.CASH_BUFFER_PCT))
    for pair in config.ASSETS:
        coin = pair.split("/")[0]
        signal, metadata = compute_signal(pair, portfolio["held_assets"])
        price = portfolio["prices"].get(pair, 0)
        max_bid = portfolio.get("max_bid", {}).get(pair, price)
        min_ask = portfolio.get("min_ask", {}).get(pair, price)

        if price <= 0:
            continue

        # Fallback to current price if bid/ask not available
        if max_bid <= 0:
            max_bid = price
        if min_ask <= 0:
            min_ask = price

        # Ensure we do not act on tiny dust positions that are flagged HOLD via strategy
        if signal == "SELL" and coin not in portfolio.get("held_assets", set()):
            log.info(f"{pair}: skipping SELL — coin not considered held (dust threshold)")
            continue

        if signal == "BUY" and rm.can_buy(total_value, coin):
            rules = pair_rules.get(pair, {})
            price_precision = rules.get("price_precision", 4)
            amount_precision = rules.get("amount_precision", 6)
            
            # Determine if this is a tranche buy or regular buy
            sigma_level = metadata.get('sigma_level')
            
            if sigma_level is not None and sigma_level > 0:
                # Tranche buying: scale in at different price deviations
                buy_qty, spend_used, tranche_pct = pm.calculate_tranche_quantity(
                    pair, price, portfolio, sigma_level, available_usd=available_usd
                )
                buy_reason = f"Tranche buy at {sigma_level:.1f}σ deviation"
            else:
                # Regular buy to reach target allocation
                buy_qty, spend_used = pm.calculate_buy_quantity(
                    pair, price, portfolio, available_usd=available_usd
                )
                buy_reason = "Target allocation buy (RSI Z-score oversold)"
            
            if buy_qty <= 0 or spend_used <= 0:
                continue

            # Spread-aware execution: quote inside the bid-ask spread
            limit_price = calculate_spread_aware_limit_price(
                side="BUY",
                mid_price=price,
                max_bid=max_bid,
                min_ask=min_ask,
                tick_size=config.MAKER_SPREAD_TICKS,
                price_precision=price_precision,
            )
            
            quantity = pm.floor_to_precision(buy_qty, int(amount_precision))

            if limit_price <= 0 or quantity <= 0:
                continue

            # Ensure we don't violate the exchange minimum order value (if provided).
            mini_order = float(rules.get("mini_order", 0) or 0)
            if mini_order and (quantity * limit_price) < mini_order:
                continue

            result = client.place_order(
                pair,
                "BUY",
                quantity,
                price=limit_price,
                order_type="LIMIT",
            )

            if result and result.get("Success"):
                detail = result.get("OrderDetail", {})
                trade_logger.log(
                    asset=pair, action="BUY", order_type="LIMIT",
                    quantity=quantity, price=limit_price,
                    order_id=str(detail.get("OrderID", "")),
                    status=detail.get("Status", ""),
                    reason=buy_reason,
                    portfolio_value=total_value,
                )
                # Record entry if filled immediately
                if detail.get("Status") == "FILLED":
                    filled_price = detail.get("FilledAverPrice", limit_price)
                    current_qty = portfolio["balances"].get(coin, 0)
                    pm.record_entry(coin, quantity, filled_price, current_qty, sigma_level=sigma_level)
                    # Note: we already reserved cash with the limit_price estimate.

                # Reserve cash for the order so later BUYs in this loop can't overdraft.
                available_usd = max(0.0, available_usd - (quantity * limit_price))
            else:
                trade_logger.log(
                    asset=pair, action="ERROR", reason="Buy order failed",
                    portfolio_value=total_value,
                )

        elif signal == "SELL" and rm.can_sell():
            quantity = portfolio["balances"].get(coin, 0)
            if quantity <= 0:
                continue

            rules = pair_rules.get(pair, {})
            price_precision = rules.get("price_precision", 4)
            amount_precision = rules.get("amount_precision", 6)
            
            # Spread-aware execution: quote inside the bid-ask spread
            limit_price = calculate_spread_aware_limit_price(
                side="SELL",
                mid_price=price,
                max_bid=max_bid,
                min_ask=min_ask,
                tick_size=config.MAKER_SPREAD_TICKS,
                price_precision=price_precision,
            )
            
            quantity = pm.floor_to_precision(quantity, int(amount_precision))

            if limit_price <= 0 or quantity <= 0:
                continue

            mini_order = float(rules.get("mini_order", 0) or 0)
            if mini_order and (quantity * limit_price) < mini_order:
                continue

            result = client.place_order(
                pair,
                "SELL",
                quantity,
                price=limit_price,
                order_type="LIMIT",
            )

            if result and result.get("Success"):
                detail = result.get("OrderDetail", {})
                trade_logger.log(
                    asset=pair, action="SELL", order_type="LIMIT",
                    quantity=quantity, price=limit_price,
                    order_id=str(detail.get("OrderID", "")),
                    status=detail.get("Status", ""),
                    reason="RSI Z-score overbought (dynamic threshold)",
                    portfolio_value=total_value,
                )
                if detail.get("Status") == "FILLED":
                    pm.clear_entry(coin)
            else:
                trade_logger.log(
                    asset=pair, action="ERROR", reason="Sell order failed",
                    portfolio_value=total_value,
                )

    # Log portfolio snapshot
    _log_snapshot(pm, rm, portfolio, portfolio_logger)
    pm.save_state()
    log.info("Signal loop complete")
    log.info("=" * 60)


def daily_rebalance(
    client: RoostooClient, pm: PortfolioManager, rm: RiskManager,
    trade_logger: TradeLogger, portfolio_logger: PortfolioLogger,
):
    """Daily rebalance at 09:00 UTC — trim/top-up positions to target allocation."""
    if not _trading_lock.acquire(blocking=False):
        log.warning("Daily rebalance skipped — another trading job is running")
        return
    try:
        _daily_rebalance_inner(client, pm, rm, trade_logger, portfolio_logger)
    except Exception as e:
        log.error(f"Daily rebalance failed with unexpected error: {e}", exc_info=True)
    finally:
        _trading_lock.release()


def _daily_rebalance_inner(
    client: RoostooClient, pm: PortfolioManager, rm: RiskManager,
    trade_logger: TradeLogger, portfolio_logger: PortfolioLogger,
):
    """Inner rebalance logic, called with trading lock held."""
    log.info("Daily rebalance starting")

    portfolio = pm.fetch_portfolio(client)
    if not portfolio:
        log.error("Could not fetch portfolio for rebalance")
        return

    if rm.check_max_drawdown(portfolio["total_value"]):
        log.critical("MAX DRAWDOWN — rebalance skipped")
        return

    trades = pm.calculate_rebalance_trades(portfolio)
    pair_rules = pm.get_pair_rules(client)

    for trade in trades:
        pair = trade["pair"]
        side = trade["side"]
        rules = pair_rules.get(pair, {})
        price_precision = rules.get("price_precision", 4)
        amount_precision = rules.get("amount_precision", 6)
        quantity = pm.floor_to_precision(trade["quantity"], int(amount_precision))
        price = pm.floor_to_precision(trade["price"], int(price_precision))

        if quantity <= 0:
            continue

        mini_order = float(rules.get("mini_order", 0) or 0)
        if price <= 0 or (mini_order and (quantity * price) < mini_order):
            continue

        # Respect risk gates for buys
        if side == "BUY" and not rm.can_buy(portfolio["total_value"]):
            log.info(f"Rebalance BUY for {pair} blocked by risk gate")
            continue

        result = client.place_order(
            pair,
            side,
            quantity,
            price=price,
            order_type="LIMIT",
        )
        if result and result.get("Success"):
            detail = result.get("OrderDetail", {})
            trade_logger.log(
                asset=pair, action=side, order_type="LIMIT",
                quantity=quantity, price=price,
                order_id=str(detail.get("OrderID", "")),
                status=detail.get("Status", ""),
                reason=trade["reason"],
                portfolio_value=portfolio["total_value"],
            )
            coin = pair.split("/")[0]
            if side == "BUY" and detail.get("Status") == "FILLED":
                current_qty = portfolio["balances"].get(coin, 0)
                pm.record_entry(coin, quantity, detail.get("FilledAverPrice", price), current_qty)
            elif side == "SELL" and detail.get("Status") == "FILLED":
                pm.clear_entry(coin)
        else:
            trade_logger.log(
                asset=pair, action="ERROR",
                reason=f"Rebalance {side} order failed",
                portfolio_value=portfolio["total_value"],
            )

    _log_snapshot(pm, rm, portfolio, portfolio_logger)
    pm.save_state()
    log.info("Daily rebalance complete")


def midnight_reset(
    client: RoostooClient, pm: PortfolioManager, rm: RiskManager,
    portfolio_logger: PortfolioLogger,
):
    """Midnight UTC — reset daily loss tracking and snapshot portfolio."""
    try:
        log.info("Midnight reset")
        portfolio = pm.fetch_portfolio(client)
        if portfolio:
            pm.update_daily_close(portfolio["total_value"])
            _log_snapshot(pm, rm, portfolio, portfolio_logger)
        rm.reset_daily()
    except Exception as e:
        log.error(f"Midnight reset failed: {e}", exc_info=True)


def _log_snapshot(
    pm: PortfolioManager, rm: RiskManager,
    portfolio: dict, portfolio_logger: PortfolioLogger,
):
    """Helper to log a portfolio snapshot."""
    total = portfolio["total_value"]
    portfolio_logger.log(
        total_value=total,
        asset_values=portfolio["asset_values"],
        usd_cash=portfolio["usd_cash"],
        daily_return_pct=rm.get_daily_return_pct(total),
        drawdown_pct=rm.get_drawdown_pct(total),
    )


def create_scheduler(
    client: RoostooClient, pm: PortfolioManager, rm: RiskManager,
    trade_logger: TradeLogger, portfolio_logger: PortfolioLogger,
) -> BackgroundScheduler:
    """Create and configure the APScheduler with all jobs."""
    scheduler = BackgroundScheduler(timezone="UTC")

    # Job 1: Signal loop every config.SIGNAL_LOOP_MINUTES minutes
    scheduler.add_job(
        signal_loop,
        "interval",
        minutes=config.SIGNAL_LOOP_MINUTES,
        args=[client, pm, rm, trade_logger, portfolio_logger],
        id="signal_loop",
        name="Signal Loop",
        max_instances=1,
    )

    # Job 2: Daily rebalance at 09:00 UTC
    scheduler.add_job(
        daily_rebalance,
        "cron",
        hour=config.DAILY_REBALANCE_HOUR,
        minute=0,
        args=[client, pm, rm, trade_logger, portfolio_logger],
        id="daily_rebalance",
        name="Daily Rebalance",
        max_instances=1,
    )

    # Job 3: Midnight reset at 00:00 UTC
    scheduler.add_job(
        midnight_reset,
        "cron",
        hour=0,
        minute=0,
        args=[client, pm, rm, portfolio_logger],
        id="midnight_reset",
        name="Midnight Reset",
        max_instances=1,
    )

    return scheduler
