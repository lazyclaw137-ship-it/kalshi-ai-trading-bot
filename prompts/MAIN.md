Build a Kalshi trading bot in Python that runs on the Kalshi DEMO environment
by default. Use only endpoints/fields from https://docs.kalshi.com. Follow the
rules in CLAUDE.md/AGENTS.md. If unsure about an endpoint, ask me.

Reuse (or create) a signed client: headers KALSHI-ACCESS-KEY,
KALSHI-ACCESS-TIMESTAMP (ms), KALSHI-ACCESS-SIGNATURE over
timestamp+METHOD+path (path from /trade-api/v2, no query string; RSA-PSS
SHA-256 for RSA keys, direct signature for Ed25519). Key file path and key id
come from env vars; the key file lives outside the repo.

Modules:
- config.toml: env ("demo"|"prod", default demo), tickers to watch (from
  the SAME env: demo and prod list different markets, so a prod ticker
  returns 404 on demo; never read quotes from one env and order on another),
  poll seconds, strategy params, and the base URL per env (demo:
  https://external-api.demo.kalshi.co/trade-api/v2; prod: the URL in the
  docs, overridable by an env var because some networks get a 403 on
  one production host and not the other).
- limits.json: max_contracts_per_order, max_open_exposure_usd,
  max_daily_loss_usd, max_orders_per_minute, order_group_contracts_limit.
- data.py: GET /markets/{ticker}, GET /markets/{ticker}/orderbook (public).
  Use *_dollars price fields. Remember the book has bids only: the response
  is {"orderbook_fp": {"yes_dollars", "no_dollars"}}, each sorted by price
  ascending, so the best bid is the LAST entry. Implied YES ask = 1.00 minus
  the best NO bid; if there are no NO bids, there is no ask, so don't trade.
  A ticker that returns 404: log it once and skip it for the rest of the run.
  Don't fall back to the market's yes_ask_dollars snapshot: in our run it
  showed an ask while the live book had no NO bids.
- research.py (OPTIONAL, off by default): sends ONLY public market text
  (title, rules_primary, current prices) to an LLM and asks for a short
  written summary and a probability estimate with reasoning. Never send keys,
  balances or positions to the LLM. Log every LLM output. If no LLM API key
  is set, don't call the network: use a deterministic stub that is clearly
  labeled "stub, not a forecast", or skip research. (Python's hash() of a
  string changes between runs, so it can't make a deterministic stub.)
- strategy.py: my rule. Start with a placeholder: if the market's status is
  "active", propose a 1-contract limit bid at the implied YES ask only if my
  own configured fair price minus that ask minus Kalshi's taker fee
  (0.07 * ask * (1 - ask) per contract; you bid at the ask, so you cross the
  book) is above a configured margin.
  The research output is advisory input only.
- risk.py: kill switch (KILL file or BOT_KILL=1) + every limit in
  limits.json, checked before every order. Unit tests.
  Daily loss: equity = balance + portfolio_value from GET /portfolio/balance
  (both in cents). Save the equity from the first loop of each UTC day in
  state.json; daily loss = max(0, that - equity now). Don't build it from
  /portfolio/positions: realized_pnl_dollars there is per market, not per
  day, and a market drops out of the default (unsettled) list once it
  settles. Open exposure = sum of market_exposure_dollars from
  GET /portfolio/positions PLUS the cost of my resting orders from
  GET /portfolio/orders?status=resting (page through it with the cursor).
  If any of these calls fails, place nothing this loop. Save order
  timestamps in state.json so max_orders_per_minute survives a restart.
- broker.py:
    create_order_group(contracts_limit) -> POST /portfolio/order_groups/create
    place_order(...) -> POST /portfolio/events/orders with ticker, side
      ("bid" = buy YES, "ask" = sell YES), count (string, e.g. "1.00"),
      price (fixed-point dollars string, e.g. "0.3500"), time_in_force
      (fill_or_kill | good_till_canceled | immediate_or_cancel; GTT is not a
      valid value), self_trade_prevention_type (taker_at_cross | maker), client_order_id (uuid4), and the order
      group id (field: order_group_id). Use post_only where sensible; reduce_only for exits
      (Kalshi rejects reduce_only=true unless time_in_force is
      immediate_or_cancel); cancel_order_on_pause=true.
    cancel_all() -> DELETE /portfolio/events/orders
    positions() -> GET /portfolio/positions
    balance() -> GET /portfolio/balance
    resting_orders() -> GET /portfolio/orders?status=resting (all pages)
- bot.py: loop = kill switch -> data -> (research) -> strategy -> risk ->
  broker -> log to logs/decisions.jsonl. At most one order per ticker,
  and that must survive a restart: at startup and every loop, treat any
  ticker with a resting order as already ordered. A filled order is no
  longer resting, so also save the tickers ordered today in state.json and
  skip any ticker where GET /portfolio/positions shows a position. Create the order group
  once and save its id in state.json; reuse it after a restart. When the
  kill switch trips mid-run, call cancel_all() once, then stop ordering.
  After the switch is cleared, wait 60 seconds before ordering again
  (Kalshi says cancel-all can also cancel orders placed in the following
  minute); save the wait in state.json so a restart can't skip it. A
  second copy of the bot must refuse to start (file lock). If the order
  group is rejected, stop. "prod" requires --live AND typing
  "I accept the risk". Print the environment loudly at startup.
- cancel_all.py: one-shot cancel-all.
- HTTP client: retry GETs only (429/5xx/timeouts), never POSTs; bounded
  exponential backoff, honor Retry-After (cap 30 s), timeout 5 s connect and
  10 s read, a 25 s total retry budget per call, and end the loop early if
  two tickers in a row fail on the network. Never retry a rejected order in a
  loop. Use client_order_id so retries can't double-submit.
- If demo keys are not set (or BROKER_MOCK=1), use a mock broker that logs
  the exact request it would send and sends nothing, so the whole loop runs
  without an account. The mock remembers its own orders as resting (in
  logs/mock_state.json) so the exposure and one-order-per-ticker rules are
  exercised. When a market's status is no longer active, drop its resting
  orders from the mock and log it (the real exchange cancels them), so the
  exposure is released. Its balance is a fixed $100 labeled "mock", so daily loss can
  only really be tested with demo keys. Print that at startup.
- pytest tests for risk, signing, the live gate, daily loss from the saved
  start-of-day equity, exposure that includes resting orders, a restart
  that must not place a second order on a ticker with a resting one, a
  filled (no longer resting) ticker that must not be bought again, the
  orders-per-minute cap across a restart, a 404 ticker logged once, and
  retry behavior (GET only, Retry-After, budget).

Show the plan, build, then give me the commands to run on demo.