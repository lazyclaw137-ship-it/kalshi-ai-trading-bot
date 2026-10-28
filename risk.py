"""Kill switch + hard limits. Pure functions so they are easy to test."""
import json, os

REQUIRED = ["max_contracts_per_order", "max_open_exposure_usd", "max_daily_loss_usd",
            "max_orders_per_minute", "order_group_contracts_limit"]


def load_limits(path="limits.json"):
    lim = json.load(open(path))
    for k in REQUIRED:   # fail closed: a missing or non-positive limit stops the bot
        if k not in lim or not isinstance(lim[k], (int, float)) or lim[k] <= 0:
            raise ValueError(f"limits.json: {k} missing or not positive")
    return lim


def kill_active(path="KILL"):
    return os.path.exists(path) or os.environ.get("BOT_KILL") == "1"


def daily_loss_usd(start_equity_cents, equity_cents):
    return max(0.0, (start_equity_cents - equity_cents) / 100.0)


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def resting_cost(order):
    price = _f(order.get("yes_price_dollars", order.get("price")))
    count = _f(order.get("remaining_count_fp", order.get("remaining_count", order.get("count"))))
    return price * count


def open_exposure_usd(positions, resting):
    pos = sum(_f(p.get("market_exposure_dollars")) for p in positions)
    return pos + sum(resting_cost(o) for o in resting)


def check_order(order, limits, exposure, daily_loss, order_times, now):
    """Returns (ok, reason). Every limit is checked before every order."""
    if order["count"] > limits["max_contracts_per_order"]:
        return False, f"count {order['count']} > max_contracts_per_order {limits['max_contracts_per_order']}"
    cost = order["count"] * order["price"]
    if exposure + cost > limits["max_open_exposure_usd"]:
        return False, f"exposure {exposure:.2f} + {cost:.2f} > {limits['max_open_exposure_usd']}"
    if daily_loss >= limits["max_daily_loss_usd"]:
        return False, f"daily loss {daily_loss:.2f} >= {limits['max_daily_loss_usd']}"
    recent = [t for t in order_times if now - t < 60]
    if len(recent) >= limits["max_orders_per_minute"]:
        return False, f"{len(recent)} orders in the last minute >= {limits['max_orders_per_minute']}"
    return True, "ok"
