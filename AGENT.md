# Residual: instructions for a scheduled run

You are running one cycle of Residual, which watches Polymarket and Kalshi for sharp price moves and
works out whether anything public explains them. The code finds the moves and decides verdicts. Your
job is the investigating, using your own web search and web fetch tools.

Work through the steps in order. Do not stop to ask questions; nobody is watching this run.

## 1. Get the code and the data

1. Attach the repo with push access: owner `mjsilverman19`, repo `newshound`. Then:

   ```
   git clone https://github.com/mjsilverman19/newshound /tmp/residual
   git clone --branch data --depth 1 https://github.com/mjsilverman19/newshound /tmp/rdata \
     || (mkdir -p /tmp/rdata && cd /tmp/rdata && git init -q && git remote add origin https://github.com/mjsilverman19/newshound)
   cd /tmp/rdata && git config user.name "Residual" && git config user.email "residual@users.noreply.github.com"
   ```

   The `data` branch holds the database, saved prices, briefs and report. It is rewritten each run.

2. The code uses only the Python standard library (3.11+). Nothing to install.

## 2. Scan and build the queue

```
cd /tmp/residual
python3 -m residual --data /tmp/rdata scan
python3 -m residual --data /tmp/rdata queue --out /tmp/queue.json
```

The first run on a fresh data branch only records prices for Kalshi. Kalshi moves appear from the
second run on.

## 3. Investigate each queue item

Read `/tmp/queue.json`. Each item has a `move_id`, a `kind` (`investigate` or `recheck`), and
`brief_for_agent`, a description of the market and the move. Handle the items one at a time.

### Rules

A sharp move with no public explanation can mean someone knows something that has not been reported
yet. So "I found no explanation" is a correct, useful result. Never stretch weak or general background
into a cause.

- Report only sources that really appeared in your search results or that you fetched. Never invent
  or guess a URL.
- Record each source's publication time as precisely as the page shows it. Fetch the page when a
  search result doesn't show the time. Use ISO 8601 with a time zone
  (`2026-11-04T13:05:00-05:00`), a bare date if the page shows only a date, or `null` if you
  can't tell. Never guess a time.
- A **cause** must be specific and must plausibly move this exact market, given its resolution rules.
- Articles that only report the market's odds or trading are `market_coverage`, never a cause.
- For posts on X, the post ID encodes when it was posted, which is often more reliable than the
  page: `python3 -c "print(__import__('datetime').datetime.utcfromtimestamp(((ID >> 22) + 1288834974657) / 1000))"`
  gives the time in UTC.
- Look beyond news articles when it helps. That includes posts on X, official statements and press
  releases, government and agency sites, court dockets, regulatory filings, schedules and calendars,
  and the market's resolution source.

### For `investigate` items

1. Start fast. Run 2 or 3 searches on the obvious angle: recent news about the market's subject. If
   you find a clear cause published before the move started, stop there.
2. Otherwise dig deeper with up to about 10 more searches. Try the people and organizations named,
   official sources, social media, local and non-English outlets where relevant, and anything the
   resolution rules depend on.
3. Also note any source published after the move started that reports the same development.

### For `recheck` items

Earlier searches found nothing that explains the move. Run up to 5 searches for anything published
since `move_start_utc` that reports a development explaining it, with publication times.

### Write the findings and record them

Write `/tmp/findings-<move_id>.json`:

```json
{
  "sources": [
    {"url": "...", "title": "...", "outlet": "...", "published_at": "2026-11-04T13:05:00-05:00",
     "relation": "cause | consequence | market_coverage | background",
     "summary": "One sentence: what it says."}
  ],
  "assessment": "Two or three sentences. Say plainly if you found no explanation.",
  "check_next": ["Up to four concrete things a reporter should check next."],
  "searched": ["every URL your searches returned, or that you fetched, during this item"],
  "queries": ["each search query you ran"]
}
```

What each `relation` means:
- `cause`: reports a development that plausibly drove the move.
- `consequence`: reports that same development, but was published after the move started.
- `market_coverage`: only reports the odds or the trading.
- `background`: context, not a trigger.

Leave `sources` empty if nothing relevant turned up. The code checks every source against
`searched`, so list those URLs honestly and completely.

Then record the item:

```
python3 -m residual --data /tmp/rdata record <move_id> /tmp/findings-<move_id>.json --kind <kind>
```

The code decides the verdict and prints it. Don't override it.

## 4. Publish the dashboard

```
python3 -m residual --data /tmp/rdata report --out /tmp/rdata/report.html
```

Republish it to the dashboard artifact, slug `1d4d1bdf-0054-4528-b006-ba066a0e4b43`
(https://claude.ai/artifact/4crkditFpW38tzKemS9aEW):
1. Publishing only accepts files inside your working folder, so copy the report there first:
   `cp /tmp/rdata/report.html "$(pwd)/newshound-dashboard.html"`. Run this from your starting
   working folder, not from `/tmp`.
2. Call `get_artifact` on that slug to get its current version and its `read` value.
3. Call `publish_files` with that slug, `base_version` set to that version, mode `patch`, and one
   file: `{"as": "index.html", "path": "<that working folder>/newshound-dashboard.html"}`. If
   `read` is anything other than `owner`, also pass `shared_live` with that value.

If the artifact tools aren't available in this run, skip this step and say so in the summary. The
report is still saved on the data branch.

## 5. Save the data

```
cd /tmp/rdata
git checkout -q --orphan snapshot && git add -A && git commit -qm "data $(date -u +%FT%TZ)" \
  && git push -q -f origin snapshot:data
```

## 6. Summarize

End with a short summary:
- how many moves were flagged and investigated this run;
- for each move whose verdict is `unexplained` or `unclear`: the question, the price change, and the
  market link;
- any re-check that found later reporting, with its lead time.

If any move came out `unexplained` this run, lead the summary with it, and send a push notification
if the tool is available. If nothing came out `unexplained`, keep the summary to one or two lines.
