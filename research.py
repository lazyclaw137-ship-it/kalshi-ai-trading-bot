"""OPTIONAL research step, off by default. Only public market text may ever leave this module."""


def public_payload(quote):
    return {"title": quote.get("title"), "rules_primary": quote.get("rules_primary"),
            "yes_bid": quote.get("yes_bid"), "yes_ask": quote.get("yes_ask")}  # never keys/balances/positions


def research(quote, enabled=False, llm_key=None):
    if not enabled:
        return None
    payload = public_payload(quote)
    if not llm_key:   # no key: no network call; deterministic stub, clearly labeled
        return {"summary": "stub, not a forecast", "probability": None, "input": payload}
    raise NotImplementedError("wire your LLM provider here; send only `payload`, log its output")
