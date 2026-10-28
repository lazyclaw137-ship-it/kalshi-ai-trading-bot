Update my Kalshi bot:
1. Daily loss: use GET /portfolio/balance. Equity = balance + portfolio_value
   (cents). Save equity at the first loop of each UTC day in state.json;
   daily loss = max(0, saved - now). Stop using realized_pnl from positions.
2. Exposure = positions' market_exposure_dollars + cost of my resting orders
   (GET /portfolio/orders?status=resting). If any account call fails, place
   nothing this loop.
3. One order per ticker must survive a restart: skip tickers with a resting
   order. Save the order group id in state.json and reuse it.
4. Skip markets whose status isn't "active".
5. After the kill switch is cleared, wait 60 s before ordering (cancel-all
   can also cancel orders placed in the following minute).
6. The mock broker keeps its orders as resting in logs/mock_state.json and
   returns a fixed $100 balance labeled mock.
Add tests for each, run them, and show me the diff before changing anything else.
