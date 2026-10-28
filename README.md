# Kalshi AI trading bot starter (demo + mock broker)

Starter code for the BotCookery recipe **[kalshi-ai-trading-bot](https://botcookery.com/recipes/kalshi-ai-trading-bot/)**. The recipe page has the step-by-step guide, the prompt, the findings and the safety notes; this repo is the code that came out of it.

## What it is

A small Python bot for Kalshi's **demo** environment: it reads live demo market data, applies a placeholder fair-value rule, checks hard risk limits, and sends orders through a **mock broker** that logs what it would send and sends nothing. It keeps state on disk so a restart doesn't re-buy or reset its order-rate count, refuses to run twice, uses an order group, and has a cancel-all command. `research.py` is a stub; no LLM is called.

The `prompts/` folder holds the exact prompts from the recipe page.

## What was actually tested

- Mode: **mock (live Kalshi demo market data, mock broker)**
- Date: 2026-10-27
- Limits: Clean-folder rebuild from the published prompts only, then live demo market data (44 loops over about 11 minutes), mock broker: no demo keys and no orders sent, so the daily-loss limit was not exercised. Fills and outages were simulated (edited mock state, a local fault-injecting relay). The same assistant that wrote the earlier versions did the rebuild, and the Oct 24 and Oct 27 follow-up prompts were checked against the new build rather than run as an upgrade of an old one.

The recipe page has the full findings, dated sample output and the prompt this code came from. The code was written by an AI coding assistant from that prompt, then run as described above. Market tickers and slugs in the config were live when it was tested and will expire; swap in current ones.

## Setup

Python 3.11 or newer.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pytest -q tests
```

## Run it (no money)

Edit `config.toml`: put open **demo** tickers in `demo = [...]` and your own fair values under `[strategy.fair]`. Then:

```bash
BROKER_MOCK=1 python bot.py --loops 3
```

With no keys set, the bot uses the mock broker anyway. It prints a banner showing `ENVIRONMENT: DEMO` and `BROKER: MOCK`, then one JSON line per market per loop with what it saw, the rule, the risk check and what it did.

`python cancel_all.py` cancels resting orders (mock unless demo keys are set).

## Safety notes

- Demo is the default. `--live` exists but was **not** tested; don't use it until you've run demo with your own keys and read every line of the code.
- Keys come from env vars (`KALSHI_KEY_ID`, `KALSHI_KEY_FILE` pointing at a PEM **outside** this folder). Never commit them; `.gitignore` blocks `.env`, `*.pem` and `*.key`.
- Limits live in `limits.json` (max contracts per order, open exposure, daily loss, orders per minute). A missing or broken limits file stops the bot.
- `touch KILL` stops all new orders.
- Not tested: daily-loss stop with real demo balances; the lock, group-reject stop, fail-closed limits and pagination are unit-tested only.

## Not financial advice

This is example code for learning how these APIs work. It is not financial advice, not a strategy, and not a recommendation to trade anything. The placeholder rules and prices in the config are there to exercise the code, not forecasts. You are responsible for anything you run, especially with real keys or real money.

## License

MIT. See [LICENSE](LICENSE).
