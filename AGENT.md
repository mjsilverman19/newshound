# Newshound: instructions for a scheduled run

You are running one cycle of Newshound. It ranks the day's news by how much money moved on it in
Polymarket and Kalshi. The code finds the moves, groups them into stories and scores them. Your job is
to hunt down what happened behind each story, using your own web search and web fetch tools.

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

The scan groups price moves into **stories** and gives each one an importance score. The score
measures how much money repriced on it. Stories above the threshold are queued for you to hunt.

## 3. Hunt each story

You are building a front page that ranks the day's news by how much money moved on it. The markets
have already decided what matters. Your job is to find out what happened and write it up.

Read `/tmp/queue.json`. Each item has:
- `story_id`
- `kind`: `hunt` or `recheck`
- `scent`: what the markets are saying, the money behind it, the resolution rules, and anything
  already reported on this story
- `move_start_utc`
- `other_stories_today`

Handle the items one at a time, highest score first.

### Reading the scent

Before you search, work out from the scent what kind of development would produce this move. For
example, "the odds Rubio leaves fell 26 points" means something made him look more likely to stay:
a denial, a statement of support, or a report being knocked down. Search for that.

If several markets moved together, find the single development behind all of them. If two queued
stories, or a queued story and one in `other_stories_today`, are really the same news, merge them
(see `merge_with` below).

### Rules

- Report only sources that really appeared in your search results or that you fetched. Never invent
  or guess a URL.
- Record each source's publication time as precisely as the page shows it. Fetch the page when a
  search result doesn't show the time. Use ISO 8601 with a time zone
  (`2026-11-04T13:05:00-05:00`), a bare date if the page shows only a date, or `null` if you can't
  tell. Never guess a time.
- For posts on X, the post ID encodes when it was posted, which is often more reliable than the
  page: `python3 -c "print(__import__('datetime').datetime.utcfromtimestamp(((ID >> 22) + 1288834974657) / 1000))"`
  gives the time in UTC.
- Look beyond news articles: posts on X, official statements and press releases, government and
  agency sites, court dockets, filings, schedules, sports injury reports, and the market's
  resolution source.
- Never stretch weak or general background into a cause. If you find nothing specific, say so.
  "Nothing reported yet" is a real and useful result.

### For `hunt` items

Start with 2 or 3 searches on the most likely development. If you find it, confirm it, get the
publication time of the earliest report, and stop. Otherwise dig deeper with up to about 10 more
searches. Note any report published after the move started too.

### For `recheck` items

Nothing explained this story when it was first hunted. Run up to 5 searches for anything published
since `move_start_utc` that reports the development behind it.

### Write it up and record it

Write `/tmp/story-<story_id>.json`:

```json
{
  "headline": "Plain, factual headline about what happened, in sentence case, under 90 characters",
  "dek": "One sentence on why it matters or what changed.",
  "summary": "Two to four sentences on what happened, with when and who.",
  "why_moved": "One or two sentences connecting the news to the markets' move.",
  "sources": [
    {"url": "...", "title": "...", "outlet": "...", "published_at": "2026-11-04T13:05:00-05:00",
     "relation": "cause | consequence | market_coverage | background",
     "summary": "One sentence: what it says."}
  ],
  "check_next": ["Up to three concrete open questions."],
  "merge_with": [],
  "searched": ["every URL your searches returned, or that you fetched, for this story"],
  "queries": ["each search query you ran"]
}
```

What each `relation` means:
- `cause`: reports the development that drove the move.
- `consequence`: reports that same development, but was published after the move started.
- `market_coverage`: only reports the odds or the trading.
- `background`: context, not a trigger.

Some rules for the write-up:
- **When you found nothing:** the headline describes what the markets did, factually (for example
  "Traders sharply cut the odds Rubio leaves the administration"), and the summary says plainly
  that nothing published so far explains it. Never speculate about causes in the headline.
- **`merge_with`:** the ids of other stories that are the same news. Their markets move into this
  one.
- **`searched`:** the code checks every source against this list, so list those URLs honestly and
  completely.
- **Style:** plain language, no hype, no em-dashes.

Then record it:

```
python3 -m residual --data /tmp/rdata record <story_id> /tmp/story-<story_id>.json --kind <kind>
```

The code decides the status (reported, timing unclear, or nothing reported yet) from the sources and
their times. Don't override it.

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

End with a short summary, written for a phone notification:
- the top three stories today by importance, each as a headline with its score;
- any story hunted this run that came out "nothing reported yet", with the market question and the
  price change;
- the dashboard link https://claude.ai/artifact/4crkditFpW38tzKemS9aEW.

If there were no stories, say so in one line.
