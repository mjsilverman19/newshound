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

## How it runs: GitHub Actions on the Claude API

`.github/workflows/hourly.yml` runs once an hour, at 17 minutes past, and whenever you start it from
Actions > Hourly run > Run workflow. It calls the Claude API with the repository secret
`ANTHROPIC_API_KEY` (Settings > Secrets and variables > Actions), so it doesn't use a Claude account.

Each run:

1. Clones the `data` branch (database and saved prices) and the `gh-pages` branch (the site).
2. Runs `python -m residual --data rdata cycle`, which:
   - compares prices with the previous run (and with Polymarket's own one-hour change) and groups the
     sharp moves into stories;
   - names the stories below the hunt threshold in a few words (`claude-haiku-5-5`);
   - hunts each queued story and each lead due for a recheck with web search, web fetch and a lead-image
     tool (`claude-opus-5-5` at `medium` effort), and records what it found. The code decides the status
     from the sources and their times.
3. Builds the site, pushes `gh-pages`, and force-pushes `data` as a single commit so the repo doesn't
   grow. Commits are signed as `github-actions[bot]`.
4. Writes a summary to the run's page: the day's top stories, leads still on the trail, anything that
   failed, and how many tokens, searches and fetches the run used.

The sourcing and writing rules are in `residual/hunter.py` (and, for a Claude session, `AGENT.md`; keep
the two in step). A source only counts if the API's search or fetch tools actually returned its URL.

Because it compares prices hour to hour, it catches moves over the past hour rather than within minutes,
and lead times are only accurate to about an hour.

## Settings and cost

Settings live in `residual/config.py`. To change any of them, copy `config.example.toml`, edit it, and
pass it with `--config` (in the workflow, add it to the `cycle` and `site` commands). The ones that matter
most:

| Setting | Default | What it does |
|---|---|---|
| `hunt_min_score` | 25 | Score a story needs before it is hunted |
| `min_window_volume_usd` | 1000 | Moves with less money traded are skipped |
| `move_threshold` | 0.05 | Smallest price move flagged (5 points) |
| `max_new_per_run` / `max_rechecks_per_run` | 6 / 6 | Caps on hunts per run, the main limit on spend |
| `hunt_model` / `hunt_effort` | `claude-opus-5-5` / `medium` | The hunter. `claude-sonnet-5-5` costs less |
| `hunt_max_searches` / `hunt_max_fetches` | 13 / 10 | Per story (rechecks get 5 searches) |
| `label_model` | `claude-haiku-5-5` | Names the small stories |

Each run's summary reports the tokens and searches it used; the Claude Console shows the bill.

## Commands

Requires Python 3.11+ and `pip install -r requirements.txt`.

```
python -m residual --data DIR cycle [--summary FILE]   # one full run on the Claude API
python -m residual --data DIR site --out DIR --clean   # build the site
python -m residual --data DIR scan                     # just compare prices and flag moves
python -m residual --data DIR queue --out queue.json   # stories to hunt or recheck now
python -m residual --data DIR record 7 findings.json --kind hunt|recheck
python -m residual --data DIR unlabeled                # small stories without a topic
python -m residual --data DIR label 9 "Nigeria's 2027 race"
python -m residual image https://example.com/article   # an article's lead image and credit
```

`cycle` needs `ANTHROPIC_API_KEY`. The other commands don't call Claude.

## Other ways to run it

**In a Claude session.** `AGENT.md` is a playbook for a Claude scheduled task to run the same cycle
with its own web search, using a Claude account instead of an API key. This is how Newshound first ran.
The "Newshound run" scheduled task is paused, not deleted; don't run it alongside the workflow, because
both write to `data`.

**Continuous mode (older).** The original loop polls every 2 minutes and calls the Claude API, but it
flags single moves rather than building stories, and doesn't publish the site.

```
python -m residual run                     # watch, flag and investigate on a loop
python -m residual run --no-investigate    # watch and flag only (no API key needed)
python -m residual once                    # one cycle, then exit
python -m residual investigate 12          # investigate one move by id
```

It needs a machine that stays on, because the rechecks run hours later. `max_investigations_per_hour`
(default 20) caps its Claude calls, and its models are `anthropic_model_quick` and
`anthropic_model_deep`.

## What has been tested

- **Market watching and move detection:** run against the live Polymarket and Kalshi APIs.
- **The hunt on the Claude API:** `pytest tests` covers it with scripted API responses (a source at a
  URL the tools never returned being rejected, the lead-image tool, paused turns, a model that never
  submits, labels). Live hourly runs began on November 5, 2026; read a few stories and their sources
  before trusting the verdicts.

## Known limits

- Publication times come from the model reading the page, so they can be wrong. Story pages list
  sources with their times so a person can check.
- Moves are measured hour to hour, so a story's lead time over the first report is only accurate to
  about an hour.
- Kalshi accounts are private, so Kalshi moves have no wallet signals, and Kalshi links point to the
  series page, not the exact market.
- "Fresh wallet" means few lifetime trades. One person using many fresh wallets will not look like one
  buyer.
