"""Placeholder rule: 1-contract bid at the implied YES ask if fair - ask - taker fee > margin."""


def taker_fee(ask):
    return 0.07 * ask * (1 - ask)


def propose(ticker, quote, fair_map, margin, research_out=None):
    """Returns (order_or_None, rule_text). research_out is advisory only and never changes the order."""
    if quote["status"] != "active":
        return None, f"status {quote['status']}, not active: skip"
    ask = quote["yes_ask"]
    if ask is None:
        return None, "no NO bids, so no YES ask: don't trade"
    fair = fair_map.get(ticker)
    if fair is None:
        return None, "no configured fair price"
    fee = taker_fee(ask)
    edge = fair - ask - fee
    if edge > margin:
        return ({"ticker": ticker, "side": "bid", "count": 1, "price": ask},
                f"fair {fair} - ask {ask} - fee {fee:.4f} = {edge:.4f} > margin {margin}: bid 1 @ {ask:.4f}")
    return None, f"fair {fair} - ask {ask} - fee {fee:.4f} = {edge:.4f} <= margin {margin}"
