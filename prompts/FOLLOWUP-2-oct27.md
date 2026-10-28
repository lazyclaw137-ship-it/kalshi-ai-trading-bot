Update my Kalshi bot:
1. A filled order is no longer resting, so save the tickers I ordered today
   in state.json and skip them. Also skip any ticker where GET
   /portfolio/positions shows a position.
2. Save order timestamps in state.json so max_orders_per_minute survives a
   restart. Save the 60 s post-cancel-all wait there too, so clearing KILL
   while the bot is stopped still waits.
3. If a ticker returns 404, log it once and skip it for the rest of the run.
4. Mock broker: when a market's status is no longer active, drop its resting
   orders from logs/mock_state.json and log it, so exposure is released.
5. Subtract Kalshi's taker fee, 0.07 * ask * (1 - ask), from my edge before
   comparing it to the margin.
6. HTTP client: retry GETs only, never POSTs. Honor Retry-After (cap 30 s),
   timeout (5 s connect, 10 s read), a 25 s total retry budget per call, and
   end the loop early if two tickers in a row fail on the network. Page
   through GET /portfolio/orders?status=resting with the cursor.
7. A second copy of the bot must refuse to start (file lock).
Add tests for each, run them, and show me the diff before changing anything else.
