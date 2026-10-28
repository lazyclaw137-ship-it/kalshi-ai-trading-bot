"""Kalshi demo trading bot loop. kill switch -> data -> (research) -> strategy -> risk -> broker -> log."""
import argparse, fcntl, json, os, sys, time, datetime, tomllib
import client as kc, data, research, risk, state, strategy
from broker import LiveBroker, MockBroker


def load_config(path="config.toml"):
    return tomllib.load(open(path, "rb"))


def make_logger(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    def log(ev):
        ev = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), **ev}
        line = json.dumps(ev)
        open(path, "a").write(line + "\n")
        print(line, flush=True)
    return log


def base_url(cfg):
    env = cfg["env"]
    return os.environ.get(f"KALSHI_{env.upper()}_BASE_URL", cfg["base_url"][env])


def make_client(cfg, signed=False):
    if signed:
        return kc.Client(base_url(cfg), os.environ["KALSHI_KEY_ID"], os.environ["KALSHI_KEY_FILE"])
    return kc.Client(base_url(cfg))


def make_broker(cfg, log):
    if os.environ.get("BROKER_MOCK") == "1" or not (os.environ.get("KALSHI_KEY_ID") and os.environ.get("KALSHI_KEY_FILE")):
        return MockBroker(log)
    return LiveBroker(make_client(cfg, signed=True))


def live_gate(env, live_flag, typed):
    if env != "prod":
        return True
    return bool(live_flag) and typed == "I accept the risk"


def single_instance(path="bot.lock"):
    fd = open(path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("another copy of the bot is already running: refusing to start")
    return fd


def held(positions):
    out = set()
    for p in positions:
        n = risk._f(p.get("position_fp", p.get("position")))
        if n != 0 or risk._f(p.get("market_exposure_dollars")) > 0:
            out.add(p.get("ticker"))
    return out


class GroupRejected(Exception):
    pass


class Bot:
    def __init__(self, cfg, limits, public, broker, log, state_path="state.json", now=time.time, sleep=time.sleep):
        self.cfg, self.limits, self.public, self.broker, self.log = cfg, limits, public, broker, log
        self.state_path, self.now, self.sleep = state_path, now, sleep
        self.st = state.load(state_path)
        self.dead = set()
        self.tickers = cfg["tickers"][cfg["env"]]

    def save(self):
        state.save(self.st, self.state_path)

    def ensure_group(self):
        if self.st["order_group_id"]:
            self.log({"event": "reusing order group", "order_group_id": self.st["order_group_id"]})
            return
        try:
            self.st["order_group_id"] = self.broker.create_order_group(self.limits["order_group_contracts_limit"])
        except Exception as e:
            self.log({"event": "order group rejected: stopping", "error": str(e)})
            raise GroupRejected(str(e))
        self.save()
        self.log({"event": "created order group", "order_group_id": self.st["order_group_id"]})

    def loop_once(self):
        st, now = self.st, self.now()
        killed = risk.kill_active()
        if killed:
            if not st["killed"]:
                self.log({"event": "kill switch on: cancel_all once"})
                self.broker.cancel_all()
                st["killed"] = True
                self.save()
            return
        if st["killed"]:   # also true after a restart if KILL was removed while the bot was stopped
            st["killed"], st["resume_after"] = False, now + 60
            self.save()
            self.log({"event": "kill switch cleared: waiting 60s before ordering"})
        try:
            bal, positions, resting = self.broker.balance(), self.broker.positions(), self.broker.resting_orders()
        except Exception as e:
            self.log({"event": "account call failed: no orders this loop", "error": str(e)})
            return
        equity = int(bal.get("balance", 0)) + int(bal.get("portfolio_value", 0))
        if state.roll_day(st, equity):
            self.log({"event": "start of UTC day equity saved", "equity_cents": equity})
        loss = risk.daily_loss_usd(st["start_equity_cents"], equity)
        exposure = risk.open_exposure_usd(positions, resting)
        resting_t = {o.get("ticker") for o in resting}
        pos_t = held(positions)
        fails = 0
        for t in self.tickers:
            if t in self.dead:
                continue
            try:
                q = data.get_quote(self.public, t)
                fails = 0
            except Exception as e:
                if isinstance(e, kc.KalshiError) and e.status == 404:
                    self.dead.add(t)
                    self.log({"ticker": t, "event": "ticker not found (404): skipping it from now on"})
                    continue
                self.log({"ticker": t, "event": "data error", "error": str(e)[:200]})
                fails = fails + 1 if kc.is_network_failure(e) else 0
                if fails >= 2:
                    self.log({"event": "2 tickers in a row failed on the network: ending this loop early"})
                    break
                continue
            seen = {"yes_bid": q["yes_bid"], "yes_ask": q["yes_ask"], "status": q["status"]}
            if q["status"] != "active" and t in resting_t:
                n = self.broker.drop_inactive(t)
                if n:
                    self.log({"ticker": t, "event": f"market closed: {n} resting order(s) now gone", "seen": seen})
                    resting = self.broker.resting_orders()
                    exposure = risk.open_exposure_usd(positions, resting)
                    resting_t = {o.get("ticker") for o in resting}
            if t in resting_t:
                self.log({"ticker": t, "seen": seen, "event": "no order", "rule": "already have a resting order here"})
                continue
            if t in st["ordered_today"]:
                self.log({"ticker": t, "seen": seen, "event": "no order", "rule": "already ordered today"})
                continue
            if t in pos_t:
                self.log({"ticker": t, "seen": seen, "event": "no order", "rule": "already hold a position here"})
                continue
            res = research.research(q, enabled=self.cfg.get("research", {}).get("enabled", False))
            order, rule = strategy.propose(t, q, self.cfg["strategy"]["fair"], self.cfg["strategy"]["margin"], res)
            if order is None:
                self.log({"ticker": t, "seen": seen, "event": "no order", "rule": rule})
                continue
            if now < st["resume_after"]:
                self.log({"ticker": t, "event": "no order", "why": "within 60s of cancel-all"})
                continue
            ok, why = risk.check_order(order, self.limits, exposure, loss, st["order_times"], self.now())
            if not ok:
                self.log({"ticker": t, "seen": seen, "rule": rule, "risk": "blocked: " + why, "event": "no order"})
                continue
            try:
                self.broker.place_order(t, order["price"], order["count"], st["order_group_id"])
            except Exception as e:
                if isinstance(e, kc.KalshiError):
                    st["ordered_today"].append(t)   # rejected: never retry it in a loop
                    self.save()
                self.log({"ticker": t, "event": "order failed (not retried)", "error": str(e)[:200]})
                continue
            now = self.now()
            st["ordered_today"].append(t)
            st["order_times"] = [x for x in st["order_times"] if now - x < 60] + [now]
            self.save()
            exposure += order["count"] * order["price"]
            resting_t.add(t)
            self.log({"ticker": t, "seen": seen, "rule": rule, "risk": "ok",
                      "event": "order sent (mock)" if self.broker.mock else "order sent"})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--loops", type=int, default=0, help="0 = run until stopped")
    args = ap.parse_args(argv)
    lock = single_instance()
    cfg, limits = load_config(), risk.load_limits()
    log = make_logger("logs/decisions.jsonl")
    print("=" * 60, f"\n  ENVIRONMENT: {cfg['env'].upper()}   base: {base_url(cfg)}")
    broker = make_broker(cfg, log)
    if broker.mock:
        print('  BROKER: MOCK. Logs each order it would send, sends nothing.\n  Mock balance is a fixed $100 (mock), so daily loss can only be tested with demo keys.')
    print("=" * 60)
    typed = input('Type "I accept the risk": ') if cfg["env"] == "prod" and args.live else ""
    if not live_gate(cfg["env"], args.live, typed):
        sys.exit("prod requires --live AND typing: I accept the risk")
    bot = Bot(cfg, limits, make_client(cfg), broker, log)
    try:
        bot.ensure_group()
    except GroupRejected:
        sys.exit(1)
    n = 0
    while True:
        bot.loop_once()
        n += 1
        if args.loops and n >= args.loops:
            break
        time.sleep(cfg["poll_seconds"])


if __name__ == "__main__":
    main()
