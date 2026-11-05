# Newshound

The day's news, found by following where expectations shifted.

Prediction markets give the hound a scent. Every hour, Newshound compares about 55,000 Polymarket
and Kalshi prices with an hour earlier. Markets that move 5 points or more with real money behind
them are grouped into **stories** by what they are about, and each story is scored by how much it
shifted expectations:

> for each market, points moved × dollars traded during the move × √(trading pace vs normal),
> summed over the story's markets; the score is 10 × log10(1 + that sum), capped at 100.

Stories scoring 25 or more are chased. Claude starts from the scent (what moved, which way, when,
and how the market resolves), finds what happened and writes it up. The code checks every source
against the searches that found it and against the time of the shift, and won't let aggregators or
partisan blogs explain a story. Only what the hunt catches is published. Leads with nothing reported
go "On the trail" as open questions and are searched again after 1, 6 and 24 hours.

The site is at https://mjsilverman19.github.io/newshound/ (built into the `gh-pages` branch). Story
pages carry a headline, dateline, the source's lead image with credit, the body, why it matters and
sources. Odds, dollars and points appear only on the Method page.

The Python package is called `residual`.

## API mode (GitHub Actions, Claude API key)

This is how it runs by default. `.github/workflows/hourly.yml` runs once an hour (and whenever you start
it from the Actions tab) and calls the Claude API directly, so it doesn't use your Claude account.

Setup: create an API key at https://platform.claude.com, add it to the repo as the Actions secret
`ANTHROPIC_API_KEY` (Settings > Secrets and variables > Actions), and turn off the old scheduled task in
your Claude account so the two don't both run.

Each run checks out the `data` and `gh-pages` branches, runs `python -m residual --data rdata cycle`
(scan, queue, name the small stories, hunt each queued story with web search and web fetch, record), builds
the site, pushes `gh-pages` and force-pushes `data`. The run summary appears on the workflow run's page.
The sourcing and writing rules are the ones in `AGENT.md`, built into `residual/hunter.py`. One check is
stricter than in a Claude session: sources are checked against the URLs the API's search and fetch tools
actually returned, not against the list the model reports.

Models and limits are in `residual/config.py`: `hunt_model` (default `claude-opus-5-5` at `medium` effort),
`label_model` (`claude-haiku-5-5`), searches and fetches per story, and `max_new_per_run` /
`max_rechecks_per_run` (6 each) to cap spend. To try a cheaper hunter, set `hunt_model =
"claude-sonnet-5-5"` in a config file and pass it with `--config`.

## Scheduled mode (Claude session, no API key)

The earlier way to run it. A Claude scheduled task runs once an hour, and you can also start it
whenever you like. Each run does the following, as set out in [`AGENT.md`](AGENT.md):

1. Clones this repo, the `data` branch (database and saved prices) and the `gh-pages` branch (site).
2. Runs `scan` to compare prices with the previous run (and with Polymarket's own one-hour change),
   `queue` to pick the stories to chase, and `label` to name the small ones in a few words.
3. Claude hunts each queued story with its own web search, finds a lead image with `image`, writes
   it up as JSON and runs `record`. The code decides the status from the sources and their times.
4. Runs `site` to build the static site and pushes it to `gh-pages`, then force-pushes the `data`
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
python -m residual site --out site          # build the static site into ./site
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
