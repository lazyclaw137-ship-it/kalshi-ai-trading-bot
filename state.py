"""state.json: saved atomically. Holds day baseline, ordered_today, order times, wait-after-cancel, group id."""
import json, os, tempfile, time, datetime

DEFAULT = {"day": None, "start_equity_cents": None, "ordered_today": [], "order_times": [],
           "resume_after": 0.0, "order_group_id": None, "killed": False}


def today_utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def load(path="state.json"):
    st = dict(DEFAULT)
    if os.path.exists(path):
        try:
            st.update(json.load(open(path)))
        except ValueError:
            pass  # corrupt file: start from defaults rather than crash
    return st


def save(st, path="state.json"):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".state")
    with os.fdopen(fd, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, path)


def roll_day(st, equity_cents, today=None):
    """First loop of a UTC day: save equity baseline, clear today's ordered list."""
    today = today or today_utc()
    if st.get("day") != today or st.get("start_equity_cents") is None:
        st["day"], st["start_equity_cents"], st["ordered_today"] = today, equity_cents, []
        return True
    return False
