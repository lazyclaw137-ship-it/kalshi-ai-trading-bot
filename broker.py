"""Live broker (Kalshi REST) and mock broker (logs the exact request, sends nothing)."""
import json, os, uuid, time


class LiveBroker:
    mock = False

    def __init__(self, client):
        self.c = client

    def create_order_group(self, contracts_limit):
        return self.c.request("POST", "/portfolio/order_groups/create",
                              json={"contracts_limit": contracts_limit})["order_group_id"]

    def place_order(self, ticker, price, count, group_id, side="bid", reduce_only=False):
        body = {"ticker": ticker, "side": side, "count": f"{count:.2f}", "price": f"{price:.4f}",
                "time_in_force": "immediate_or_cancel" if reduce_only else "good_till_canceled",
                "self_trade_prevention_type": "taker_at_cross", "client_order_id": str(uuid.uuid4()),
                "order_group_id": group_id, "cancel_order_on_pause": True}
        if reduce_only:
            body["reduce_only"] = True
        return self.c.request("POST", "/portfolio/events/orders", json=body)   # never auto-retried

    def cancel_all(self):
        return self.c.request("DELETE", "/portfolio/events/orders")

    def positions(self):
        return self.c.get("/portfolio/positions").get("market_positions", [])

    def balance(self):
        return self.c.get("/portfolio/balance")

    def resting_orders(self):
        out, cursor = [], None
        while True:
            params = {"status": "resting", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            r = self.c.get("/portfolio/orders", params=params)
            out += r.get("orders", [])
            cursor = r.get("cursor")
            if not cursor:
                return out

    def drop_inactive(self, ticker):
        return 0   # the real exchange cancels resting orders when a market closes


class MockBroker:
    mock = True

    def __init__(self, log, path="logs/mock_state.json"):
        self.log, self.path = log, path
        self.orders = json.load(open(path))["orders"] if os.path.exists(path) else []

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        json.dump({"orders": self.orders}, open(self.path, "w"), indent=1)

    def _req(self, method, path, body=None):
        self.log({"event": "mock request (not sent)", "method": method, "path": path, "body": body})

    def _reload(self):   # the file is the truth: delete an order from it to simulate a fill
        if os.path.exists(self.path):
            self.orders = json.load(open(self.path))["orders"]

    def create_order_group(self, contracts_limit):
        self._req("POST", "/portfolio/order_groups/create", {"contracts_limit": contracts_limit})
        return "mock-group-" + uuid.uuid4().hex[:8]

    def place_order(self, ticker, price, count, group_id, side="bid", reduce_only=False):
        body = {"ticker": ticker, "side": side, "count": f"{count:.2f}", "price": f"{price:.4f}",
                "time_in_force": "good_till_canceled", "self_trade_prevention_type": "taker_at_cross",
                "client_order_id": str(uuid.uuid4()), "order_group_id": group_id, "cancel_order_on_pause": True}
        self._req("POST", "/portfolio/events/orders", body)
        self._reload()
        self.orders.append({"order_id": "mock-" + uuid.uuid4().hex[:8], "ticker": ticker, "status": "resting",
                            "yes_price_dollars": body["price"], "remaining_count_fp": body["count"]})
        self._save()
        return {"order_id": self.orders[-1]["order_id"]}

    def cancel_all(self):
        self._req("DELETE", "/portfolio/events/orders")
        self._reload()
        for o in self.orders:
            if o["status"] == "resting":
                o["status"] = "canceled"
        self._save()

    def positions(self):
        return []

    def balance(self):
        return {"balance": 10000, "portfolio_value": 0, "mock": True}   # fixed $100, labeled mock

    def resting_orders(self):
        self._reload()
        return [o for o in self.orders if o["status"] == "resting"]

    def drop_inactive(self, ticker):
        self._reload()
        n = 0
        for o in self.orders:
            if o["ticker"] == ticker and o["status"] == "resting":
                o["status"], n = "canceled", n + 1
        if n:
            self._save()
        return n
