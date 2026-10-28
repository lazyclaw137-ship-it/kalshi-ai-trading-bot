import json, os, time, pytest, requests
import bot, client as kc, risk, state, strategy
from broker import MockBroker
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa, padding
from cryptography.hazmat.primitives import hashes

LIM = {"max_contracts_per_order": 1, "max_open_exposure_usd": 5, "max_daily_loss_usd": 2,
       "max_orders_per_minute": 3, "order_group_contracts_limit": 3}


class FakePublic:
    """Stands in for the public client: serves market + orderbook for ASK-priced active markets."""
    def __init__(self, asks, status="active"):
        self.asks, self.status, self.calls = asks, status, 0

    def get(self, path, params=None):
        self.calls += 1
        t = path.split("/")[2]
        if t not in self.asks:
            raise kc.KalshiError(404, "not found")
        if path.endswith("/orderbook"):
            return {"orderbook_fp": {"yes_dollars": [], "no_dollars": [["%.4f" % (1 - self.asks[t]), "10.00"]]}}
        return {"market": {"status": self.status, "title": t}}


def mk(tmp, asks, fair=0.9, tickers=None, limits=None, status="active", now=time.time, broker=None):
    os.chdir(tmp)
    logs = []
    cfg = {"env": "demo", "tickers": {"demo": tickers or list(asks)},
           "strategy": {"margin": 0.03, "fair": {t: fair for t in (tickers or asks)}}}
    broker = broker or MockBroker(logs.append, str(tmp / "mock.json"))
    b = bot.Bot(cfg, limits or LIM, FakePublic(asks, status), broker, logs.append, str(tmp / "state.json"), now=now)
    b.ensure_group()
    return b, logs, broker


def orders_sent(logs):
    return [l for l in logs if l.get("event", "").startswith("order sent")]


def test_order_then_second_loop_has_no_second_order(tmp_path):
    b, logs, _ = mk(tmp_path, {"A": 0.5})
    b.loop_once(); b.loop_once()
    assert len(orders_sent(logs)) == 1


def test_restart_does_not_double_order_and_reuses_group(tmp_path):
    b, logs, broker = mk(tmp_path, {"A": 0.5})
    b.loop_once()
    gid = b.st["order_group_id"]
    b2, logs2, _ = mk(tmp_path, {"A": 0.5}, broker=MockBroker(logs2 := [].append, str(tmp_path / "mock.json")))
    assert b2.st["order_group_id"] == gid
    b2.loop_once()
    assert not orders_sent(logs2)


def test_filled_ticker_is_not_bought_again(tmp_path):
    b, logs, broker = mk(tmp_path, {"A": 0.5})
    b.loop_once()
    json.dump({"orders": []}, open(tmp_path / "mock.json", "w"))   # simulate a fill: no longer resting
    b.loop_once()
    assert len(orders_sent(logs)) == 1
    assert any(l.get("rule") == "already ordered today" for l in logs)


def test_filled_ticker_with_position_is_skipped_after_state_loss(tmp_path):
    b, logs, broker = mk(tmp_path, {"A": 0.5})
    broker.positions = lambda: [{"ticker": "A", "position_fp": "1.00", "market_exposure_dollars": "0.50"}]
    b.loop_once()
    assert not orders_sent(logs) and any(l.get("rule") == "already hold a position here" for l in logs)


def test_order_rate_cap_survives_restart(tmp_path):
    lim = dict(LIM, max_orders_per_minute=1)
    b, logs, _ = mk(tmp_path, {"A": 0.5, "B": 0.5}, limits=lim)
    b.loop_once()
    assert len(orders_sent(logs)) == 1
    b2, logs2, _ = mk(tmp_path, {"A": 0.5, "B": 0.5}, limits=lim, broker=MockBroker(lambda e: None, str(tmp_path / "mock.json")))
    b2.loop_once()
    assert not orders_sent(logs2)
    assert any("orders in the last minute" in str(l.get("risk")) for l in logs2)


def test_404_ticker_logged_once_and_skipped(tmp_path):
    b, logs, _ = mk(tmp_path, {"A": 0.5}, tickers=["A", "NOPE"])
    for _ in range(3):
        b.loop_once()
    assert sum("404" in l.get("event", "") for l in logs) == 1


def test_resting_orders_count_as_exposure(tmp_path):
    lim = dict(LIM, max_open_exposure_usd=0.8)
    b, logs, _ = mk(tmp_path, {"A": 0.5, "B": 0.5}, limits=lim)
    b.loop_once()
    assert len(orders_sent(logs)) == 1   # 0.5 + 0.5 > 0.8 blocks the second
    assert any("exposure" in str(l.get("risk")) for l in logs)


def test_exposure_function():
    assert risk.open_exposure_usd([{"market_exposure_dollars": "1.25"}],
                                  [{"yes_price_dollars": "0.40", "remaining_count_fp": "2.00"}]) == pytest.approx(2.05)


def test_daily_loss_from_saved_start_equity_and_blocks(tmp_path):
    b, logs, broker = mk(tmp_path, {"A": 0.5})
    b.st.update(day=state.today_utc(), start_equity_cents=10300)   # saved at start of today: $103
    broker.balance = lambda: {"balance": 10000, "portfolio_value": 0}   # now $100: lost $3
    b.loop_once()
    assert not orders_sent(logs) and any("daily loss" in str(l.get("risk")) for l in logs)
    assert risk.daily_loss_usd(10300, 10000) == 3.0 and risk.daily_loss_usd(10000, 10500) == 0.0


def test_new_day_resets_baseline_and_ordered_list():
    st = {"day": "2026-10-01", "start_equity_cents": 5, "ordered_today": ["A"]}
    assert state.roll_day(st, 9999, today="2026-10-02")
    assert st["start_equity_cents"] == 9999 and st["ordered_today"] == []


def test_account_call_failure_places_nothing(tmp_path):
    b, logs, broker = mk(tmp_path, {"A": 0.5})
    def boom(): raise RuntimeError("down")
    broker.positions = boom
    b.loop_once()
    assert not orders_sent(logs) and any("account call failed" in l.get("event", "") for l in logs)


def test_kill_switch_cancels_once_and_waits_60s_after_clear(tmp_path):
    t = [1000.0]
    b, logs, broker = mk(tmp_path, {"A": 0.5, "B": 0.5}, now=lambda: t[0])
    b.loop_once()
    open("KILL", "w").close()
    b.loop_once(); b.loop_once()
    assert sum(l.get("event") == "kill switch on: cancel_all once" for l in logs) == 1
    os.remove("KILL")
    json.dump({"orders": []}, open(tmp_path / "mock.json", "w"))
    state.save(dict(b.st, ordered_today=[]), str(tmp_path / "state.json")); b.st["ordered_today"] = []
    t[0] += 10
    b.loop_once()
    assert any("within 60s" in l.get("why", "") for l in logs)
    t[0] += 61
    n = len(orders_sent(logs))
    b.loop_once()
    assert len(orders_sent(logs)) > n


def test_wait_survives_restart_when_kill_removed_while_stopped(tmp_path):
    t = [1000.0]
    b, logs, _ = mk(tmp_path, {"A": 0.5}, now=lambda: t[0])
    b.st["killed"] = True; b.save()
    b2, logs2, _ = mk(tmp_path, {"A": 0.5}, now=lambda: t[0], broker=MockBroker(lambda e: None, str(tmp_path / "mock.json")))
    b2.loop_once()
    assert not orders_sent(logs2) and any("within 60s" in l.get("why", "") for l in logs2)


def test_mock_drops_orders_when_market_no_longer_active(tmp_path):
    b, logs, broker = mk(tmp_path, {"A": 0.5})
    b.loop_once()
    b.public.status = "closed"
    b.loop_once()
    assert any("market closed: 1 resting" in l.get("event", "") for l in logs)
    assert broker.resting_orders() == []


def test_two_network_failures_end_loop_early(tmp_path):
    b, logs, _ = mk(tmp_path, {"A": 0.5, "B": 0.5, "C": 0.5, "D": 0.5})
    def down(path, params=None): raise requests.ConnectionError("x")
    b.public.get = down
    b.loop_once()
    assert sum(l.get("event") == "data error" for l in logs) == 2
    assert any("ending this loop early" in l.get("event", "") for l in logs)


def test_strategy_subtracts_fee_and_ignores_non_active():
    q = {"status": "active", "yes_ask": 0.5}
    assert strategy.propose("A", q, {"A": 0.54}, 0.03)[0] is None          # 0.54-0.5-0.0175 = 0.0225
    assert strategy.propose("A", q, {"A": 0.56}, 0.03)[0] is not None       # 0.0425
    assert strategy.propose("A", dict(q, status="closed"), {"A": 0.9}, 0.03)[0] is None
    assert strategy.propose("A", dict(q, yes_ask=None), {"A": 0.9}, 0.03)[0] is None


def test_limits_fail_closed(tmp_path):
    p = tmp_path / "l.json"; p.write_text(json.dumps(dict(LIM, max_open_exposure_usd=0)))
    with pytest.raises(ValueError): risk.load_limits(str(p))
    p.write_text(json.dumps({k: v for k, v in LIM.items() if k != "max_daily_loss_usd"}))
    with pytest.raises(ValueError): risk.load_limits(str(p))


def test_live_gate():
    assert bot.live_gate("demo", False, "")
    assert not bot.live_gate("prod", False, "I accept the risk")
    assert not bot.live_gate("prod", True, "yes")
    assert bot.live_gate("prod", True, "I accept the risk")


def test_signing_rsa_pss_and_ed25519():
    rk = rsa.generate_private_key(65537, 2048)
    sig = kc.sign(rk, "123", "get", "/trade-api/v2/portfolio/balance?x=1")
    import base64
    rk.public_key().verify(base64.b64decode(sig), b"123GET/trade-api/v2/portfolio/balance",
                           padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
    ek = ed25519.Ed25519PrivateKey.generate()
    sig = kc.sign(ek, "5", "POST", "/trade-api/v2/portfolio/events/orders")
    ek.public_key().verify(base64.b64decode(sig), b"5POST/trade-api/v2/portfolio/events/orders")


class Resp:
    def __init__(self, code, headers=None, body=b'{"ok":1}'):
        self.status_code, self.headers, self.content, self.text = code, headers or {}, body, body.decode()
    def json(self): return json.loads(self.content)


class Seq:
    def __init__(self, items): self.items, self.calls = list(items), 0
    def request(self, *a, **k):
        self.calls += 1
        it = self.items.pop(0)
        if isinstance(it, Exception): raise it
        return it


def test_get_honors_retry_after():
    sleeps = []
    c = kc.Client("https://x", session=Seq([Resp(429, {"Retry-After": "5"}), Resp(503, {"Retry-After": "7"}), Resp(200)]),
                  sleep=sleeps.append)
    assert c.get("/a") == {"ok": 1}
    assert sleeps == [5.0, 7.0]


def test_retry_after_is_capped_at_30_and_a_wait_past_the_budget_gives_up():
    assert kc.backoff_delay(0, "999") == 30.0
    s = Seq([Resp(429, {"Retry-After": "999"}), Resp(200)])
    with pytest.raises(kc.KalshiError): kc.Client("https://x", session=s, sleep=lambda x: None).get("/a")
    assert s.calls == 1   # 30 s wait would blow the 25 s budget: stop instead of hammering


def test_post_is_never_retried():
    s = Seq([Resp(503), Resp(200)])
    c = kc.Client("https://x", session=s, sleep=lambda x: None)
    with pytest.raises(kc.KalshiError): c.request("POST", "/portfolio/events/orders", json={})
    assert s.calls == 1
    s2 = Seq([requests.ReadTimeout("t"), Resp(200)])
    with pytest.raises(requests.Timeout): kc.Client("https://x", session=s2, sleep=lambda x: None).request("DELETE", "/x")
    assert s2.calls == 1


def test_retry_budget_stops_retries():
    t = [0.0]
    class Slow:
        calls = 0
        def request(self, *a, **k):
            Slow.calls += 1; t[0] += 10.0; raise requests.ReadTimeout("slow")
    c = kc.Client("https://x", session=Slow(), sleep=lambda s: None, clock=lambda: t[0])
    with pytest.raises(requests.Timeout): c.get("/a")
    assert Slow.calls <= 3   # 25 s budget: never 5 attempts


def test_404_is_not_retried_and_not_a_network_failure():
    s = Seq([Resp(404, body=b"nf")])
    with pytest.raises(kc.KalshiError) as e: kc.Client("https://x", session=s, sleep=lambda x: None).get("/a")
    assert s.calls == 1 and not kc.is_network_failure(e.value)


def test_second_copy_refuses_to_start(tmp_path):
    os.chdir(tmp_path)
    fd = bot.single_instance(str(tmp_path / "l.lock"))
    with pytest.raises(SystemExit): bot.single_instance(str(tmp_path / "l.lock"))
    fd.close()


def test_group_rejected_stops(tmp_path):
    os.chdir(tmp_path)
    class Bad(MockBroker):
        def create_order_group(self, n): raise kc.KalshiError(400, "no")
    cfg = {"env": "demo", "tickers": {"demo": []}, "strategy": {"margin": .03, "fair": {}}}
    b = bot.Bot(cfg, LIM, None, Bad(lambda e: None, str(tmp_path / "m.json")), lambda e: None, str(tmp_path / "s.json"))
    with pytest.raises(bot.GroupRejected): b.ensure_group()


def test_pagination_collects_all_pages():
    from broker import LiveBroker
    pages = [{"orders": [{"ticker": "A"}], "cursor": "c1"}, {"orders": [{"ticker": "B"}], "cursor": ""}]
    class C:
        def get(self, path, params=None): return pages.pop(0)
    assert [o["ticker"] for o in LiveBroker(C()).resting_orders()] == ["A", "B"]
