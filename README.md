# Newshound

The Python package is called `residual`.

Watches Polymarket and Kalshi for sharp price moves, then has a Claude agent with web search find out
why each one happened. A move nothing public explains is a lead. Moves that stay unexplained are
searched again later, and if reporting appears afterwards, Residual records how far ahead the market
was.

## How it works

1. **Watch.** Every 2 minutes it pulls prices for about 28,000 Polymarket and 27,000 Kalshi markets
   and stores them in SQLite.
2. **Flag.** A market is flagged when its price moves 10+ points within 30 minutes. Skipped: sports,
   weather, crypto, markets that track stock or commodity prices, markets that resolve within
   12 hours, thin markets, and moves with under $1,000 traded.
3. **Signals.** For each flagged move it records the volume compared with normal, how much of the
   buying came from the single largest buyer, related markets in the same event that moved too, and
   (Polymarket only) any nearly unused wallets among the biggest buyers.
4. **Investigate.** Two passes:
   - A quick pass on a cheap model with 3 searches clears moves that have an obvious cause.
   - If that finds nothing, a deep pass with 12 searches looks at officials, social media, filings,
     dockets and the market's resolution source.
5. **Verdict, decided in code rather than by the agent.** A move counts as *explained* only when the
   agent names a cause that meets all three of these conditions:
   - Its URL actually appeared in that run's search results, which rules out invented links.
   - It was published before the move started (with a 15-minute allowance for the market to react).
   - It is about the underlying development, not just articles reporting the odds.

   A cause with no publication time makes the move *unclear*. Anything else is *unexplained*.
6. **Re-check.** Unexplained and unclear moves are searched again at 1, 6 and 24 hours. The first
   report published after the move becomes the confirmation, and the gap is the lead time.
7. **Briefs.** Each investigated move gets a Markdown brief in `briefs/`. `report.html` lists
   every flagged move, with track-record figures at the top.

## Scheduled mode (no server, no API key)

This is how it runs by default. A Claude scheduled task runs once an hour, and you can also start it
whenever you like. Each run does the following, as set out in [`AGENT.md`](AGENT.md):

1. Clones this repo, plus the `data` branch that holds the database, saved prices and briefs.
2. Runs `scan` to compare prices with the previous run (and with Polymarket's own one-hour change),
   then `queue` to pick the moves to look at.
3. Claude investigates each queued move with its own web search, writes the findings to a JSON file,
   and runs `record`. The same verdict rules apply in code.
4. Publishes `report.html` to the Newshound dashboard on claude.ai and force-pushes the `data`
   branch, which is kept to a single commit so the repo doesn't grow.
5. Sends a push notification when a move has no public explanation.

Because it compares prices hour to hour, scheduled mode catches moves over the past hour rather
than within minutes, and lead times are only accurate to about an hour. To change how runs behave,
edit `AGENT.md`; the scheduled task reads it fresh each time.

## Continuous mode: setup

Requires Python 3.11+.

```
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
```

## Running it

```
python -m residual run                     # watch, flag and investigate on a loop
python -m residual run --no-investigate    # watch and flag only (no API key needed)
python -m residual once                    # one cycle, then exit
python -m residual investigate 12          # investigate one move by id
python -m residual report                  # write report.html
```

Run it somewhere that stays on, such as a small server or a `tmux` session, because the re-checks
need it to still be running hours later. Settings live in `residual/config.py`. To change any of
them, copy `config.example.toml`, edit it, and pass it with `--config`.

## Cost control

- `max_investigations_per_hour` (default 20) caps Claude calls. When the cap is hit, the most
  suspicious moves go first: big move, fresh wallets, volume spike, one dominant buyer.
- Most moves end after the quick pass.
- Busy news days, such as election nights, flag many moves that have obvious causes. Raise
  `move_threshold`, or add terms to `excluded_categories`, if the budget runs out.

## What has been tested

- **Market watching and move detection:** run against the live Polymarket and Kalshi APIs. It flagged
  real moves and pulled trade and wallet signals.
- **The investigating agent:** tested only with scripted Claude API responses (`pytest tests`),
  because no API key was available. Those tests cover a fast explanation, an invented URL being
  rejected, a later report confirming a lead, re-checks, and the paused-turn case. Watch the first
  live runs and read a few briefs before trusting the verdicts.

## Known limits

- Publication times come from the agent reading the page, so they can be wrong. Briefs show them so
  a person can check.
- "Fresh wallet" means few lifetime trades. One person using many fresh wallets will not look like
  one buyer.
- Kalshi accounts are private, so Kalshi moves have no wallet signals.
- Kalshi links point to the series page, not the exact market.
- The default models are `claude-haiku-5-5` for the quick pass and `claude-sonnet-5-5` for the deep
  one. Switch the deep pass to `claude-opus-5-5` for harder cases.
