# Newshound

A front page that ranks the day's news by how much money moved on it.

Every hour, Newshound compares about 55,000 Polymarket and Kalshi prices with an hour earlier.
Markets that move 5 points or more with real money behind them become **stories**, grouped by the
event they are about. Each story gets an **importance index**:

> for each market, points moved × dollars traded during the move × √(trading pace vs normal),
> summed over the story's markets; the index is 10 × log10(1 + that sum), capped at 100.

Stories scoring 30 or more are hunted. Claude starts from the scent the markets give (what moved,
in which direction, when, with how much money, and how the market resolves), finds what happened
and writes it up. The code checks every source against the searches that found it and against the
time of the move, and labels the story *reported*, *timing unclear* or *nothing reported yet*.
Stories with nothing reported are searched again after 1, 6 and 24 hours, which measures how far
ahead of the news the markets were.

The front page, [Newshound](https://claude.ai/artifact/4crkditFpW38tzKemS9aEW), follows the
Book Critic Data style. Each story's image is its own price chart.

The Python package is called `residual`.

## Scheduled mode (no server, no API key)

This is how it runs by default. A Claude scheduled task runs once an hour, and you can also start it
whenever you like. Each run does the following, as set out in [`AGENT.md`](AGENT.md):

1. Clones this repo, plus the `data` branch that holds the database, saved prices and briefs.
2. Runs `scan` to compare prices with the previous run (and with Polymarket's own one-hour change),
   then `queue` to pick the moves to look at.
3. Claude hunts each queued story with its own web search, writes it up as JSON and runs
   `record`. The code decides the status from the sources and their times.
4. Publishes `report.html` to the Newshound dashboard on claude.ai and force-pushes the `data`
   branch, which is kept to a single commit so the repo doesn't grow.
5. Ends with a summary of the top stories, which arrives as a push notification.

Because it compares prices hour to hour, scheduled mode catches moves over the past hour rather
than within minutes, and lead times are only accurate to about an hour. To change how runs behave,
edit `AGENT.md`; the scheduled task reads it fresh each time.

## Continuous mode (older)

The original loop that polls every 2 minutes and calls the Claude API still works, but it flags
moves rather than building stories. Setup:

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
