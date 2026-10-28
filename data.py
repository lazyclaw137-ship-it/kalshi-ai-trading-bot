"""Public market data. The book has bids only: best bid is the LAST entry; YES ask = 1 - best NO bid."""


def best(levels):
    return float(levels[-1][0]) if levels else None


def get_quote(client, ticker):
    m = client.get(f"/markets/{ticker}")["market"]
    q = {"status": m.get("status"), "title": m.get("title"), "rules_primary": m.get("rules_primary"),
         "yes_bid": None, "yes_ask": None}
    if q["status"] != "active":
        return q
    ob = client.get(f"/markets/{ticker}/orderbook")["orderbook_fp"]
    q["yes_bid"] = best(ob.get("yes_dollars") or [])
    no_bid = best(ob.get("no_dollars") or [])
    q["yes_ask"] = None if no_bid is None else round(1.0 - no_bid, 4)   # no NO bids = no ask
    return q
