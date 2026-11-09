# Newshound: instructions for a scheduled run

> This is the playbook for running a cycle in a Claude session. The GitHub Actions workflow
> (`.github/workflows/hourly.yml`) runs the same cycle on the Claude API with `python -m residual cycle`;
> its rules live in `residual/hunter.py`. Keep the two in step when you change the rules.

You are running one cycle of Newshound. Prediction markets (Polymarket and Kalshi) give the hound a
scent: when expectations about something shift sharply, something probably happened. The code finds
those shifts, groups them into stories and scores them. Your job is to chase each scent down with
your own web search and web fetch tools and write up what actually happened.

The markets are the scent, not the story. Traders, odds and prices never appear in a headline, a
summary or anywhere on a story page. Only what the hunt catches is published.

Work through the steps in order. Do not stop to ask questions; nobody is watching this run.

## 1. Get the code, the data and the site

Attach the repo with push access (owner `mjsilverman19`, repo `newshound`), then:

```
git clone https://github.com/mjsilverman19/newshound /tmp/residual
git clone --branch data --depth 1 https://github.com/mjsilverman19/newshound /tmp/rdata \
  || (mkdir -p /tmp/rdata && cd /tmp/rdata && git init -q && git remote add origin https://github.com/mjsilverman19/newshound)
git clone --branch gh-pages --depth 1 https://github.com/mjsilverman19/newshound /tmp/site \
  || (mkdir -p /tmp/site && cd /tmp/site && git init -q && git checkout -q -b gh-pages && git remote add origin https://github.com/mjsilverman19/newshound)
for d in /tmp/rdata /tmp/site; do git -C $d config user.name "github-actions[bot]"; git -C $d config user.email "41898283+github-actions[bot]@users.noreply.github.com"; done
```

- The `data` branch holds the database and saved prices. It is rewritten each run.
- The `gh-pages` branch holds the built site, served at https://mjsilverman19.github.io/newshound/.

The code uses only the Python standard library (3.11+). Nothing to install.

## 2. Scan, queue and name the small stories

```
cd /tmp/residual
python3 -m residual --data /tmp/rdata scan
python3 -m residual --data /tmp/rdata queue --out /tmp/queue.json
python3 -m residual --data /tmp/rdata unlabeled
```

`scan` groups price moves into stories and scores each by how much it shifted expectations. Stories
above the threshold are queued for you to hunt.

`unlabeled` lists today's stories below the threshold that have no topic yet. They are not hunted
and never appear with their market titles. For each one, write a topic of two to five words naming
the real-world subject, not the bet ("Nigeria's 2027 race", "Vance's 2028 standing", "the Fed's
December decision"), and save it:

```
python3 -m residual --data /tmp/rdata label <story_id> "<topic>"
```

Give stories about the same subject the same topic. These labels are internal and never appear publicly.

## 3. Hunt each story

Read `/tmp/queue.json`. Each item has `story_id`, `kind` (`hunt` or `recheck`), `scent` (what the
markets are saying, the money behind it, the resolution rules, and anything already found),
`move_start_utc` and `other_stories_today`. Handle the items one at a time, highest score first.

## Editorial standard
Use market movements to select subjects to investigate. Publish concrete, timely news about the subject
when a trustworthy source supports it. You do NOT need to establish why the market moved, find a single
trigger, confirm the exact market outcome, or wait for a final decision. A reported statement, policy
change, documented political pressure, research disclosure, filing or other substantive development
can be news. Write declarative headlines about what happened, not questions or generic explainers.
A related development uncovered during the investigation can be the story. Do not claim it caused the
move without evidence. Do not mention inability to explain the market in the article.

## Sourcing
- Use only URLs actually returned by your search/fetch tools. Prefer primary sources, original reporting,
  established outlets and credible local reporting. One substantive source can suffice; corroborate
  disputed or consequential claims when possible and attribute allegations and competing accounts.
- Aggregators, partisan blogs and reposts are background only. Follow them to the original source.
- At least one supporting news source must have a known publication date within the last 72 hours.
  Fetch the date if missing; never invent it. For X posts use x_post_time. Older material can provide
  context, but an old event does not become new just because we discovered it. Date events explicitly.
- Check for newer reporting that changes or supersedes the original lead before writing. In particular,
  report completed elections as results, not speculative uncertainty about routine certification.
- Distinguish an announced plan from an event that actually happened. Rumors, generic background,
  unexplained price changes and repetition of already published news do not qualify.

Source relation:
- report: directly supports the article's concrete news development; market causation is unestablished.
- cause: directly supports the development and there is evidence it drove the market shift.
- consequence: reporting on the development found after the move began.
- background: supporting context only; cannot alone qualify an article.
- market_coverage: only odds or trading; cannot qualify and is not displayed.
The source role describes its editorial function for readers, independently of relation.

## Hunts and rechecks
Start with 2 or 3 searches. Follow the strongest actual news you find; don't fixate on the original
question if a different substantive development on the same subject is established. Confirm its facts,
date and latest status. Merge with an existing story if it is the same development, not merely the same
person or topic. On a recheck, search for recent news, including developments after the original move.
Write the complete headline, dek, summary, dateline and why_it_matters when evidence qualifies.
If there is no publishable news, submit only an internal question and research notes. Unpublished leads
are private and must never be dressed up as articles. Respect the search budget, especially weak signals.

### The lead image

When you found the story, get a lead image from one of its sources, preferring the original report:

```
python3 -m residual image <source url>
```

This reads the page's own `og:image` and any credit it declares. Use only an image URL this command
returned. If it returns an error or no image, try another source. Then fetch the article and look
for the photo credit and caption printed with the lead image (for example "Jane Doe/Reuters"); use
the credit only if you saw it. If no source gives a usable image, leave `image` out: the page shows
no image slot rather than a placeholder.

### Write it up

Write `/tmp/story-<story_id>.json`. When the hunt found the development:

```json
{
  "headline": "Plain, factual headline about what happened, in sentence case, under 90 characters",
  "dek": "One sentence on what changed.",
  "dateline": "Where it happened: a city, or a country or region if no city fits",
  "slug": "three-to-six-word-url-slug",
  "summary": "Two to four sentences on what happened, with when and who.",
  "why_it_matters": "One or two sentences on the consequences in the world.",
  "image": {"url": "from the image command", "source_url": "the article it came from",
            "credit": "as printed, or omit", "caption": "as printed, or omit", "alt": "short description"},
  "sources": [
    {"url": "...", "title": "...", "outlet": "...", "published_at": "2026-11-04T13:05:00-05:00",
     "relation": "report | cause | consequence | background | market_coverage",
     "role": "original report | announcement | statement | filing | data | denial | confirmation | follow-up | analysis | context",
     "note": "optional, a few words such as 'quoting the State Department'",
     "summary": "One sentence: what it says."}
  ],
  "check_next": ["Up to three concrete open questions, for the next recheck. Not published."],
  "merge_with": [],
  "searched": ["every URL your searches returned, or that you fetched, for this story"],
  "queries": ["each search query you ran"]
}
```

When the hunt found nothing, leave out `headline`, `dek`, `summary`, `why_it_matters`, `dateline`,
`slug` and `image`, and write instead:

```json
{
  "question": "An open question about the world, e.g. Has anything changed on the Saudi-Yemen border?",
  "trail_note": "Optional: one short sentence of context, e.g. The shift ran against the day's chip selloff.",
  "sources": [], "check_next": [], "searched": [], "queries": []
}
```

`relation` describes how each source supports the article:
- `report`: directly supports concrete news, without asserting market causation.
- `cause`: reports the development that drove the shift.
- `consequence`: reports that same development, but was published after the move started.
- `background`: context, not a trigger.
- `market_coverage`: only reports the odds or the trading. Never shown on the page.

`role` is what readers see next to each source. Pick the one that describes what the source is.

Rules for the write-up:
- Headlines, deks and summaries come only from what the hunt found. Never make traders, odds,
  markets or bets the subject of a headline, dek, summary or why-it-matters, and never mention
  prices, points or dollars traded.
- The question for an open lead is about the world, not the market. It must not speculate about a
  cause, and it must not repeat the market's wording.
- `merge_with`: the ids of other stories that are the same news. Their markets move into this one.
- `searched`: the code checks every source and the image's article against this list, so list those
  URLs honestly and completely.
- `slug` is set the first time a story gets a headline and never changes after that.
- Style: plain language, no hype, no em-dashes.

Then record it:

```
python3 -m residual --data /tmp/rdata record <story_id> /tmp/story-<story_id>.json --kind <kind>
```

The code checks article completeness, source provenance, publisher eligibility and recency.
A market-causation explanation is not required. Unpublished leads stay internal; they are never
shown on the homepage, archives or feed.

## 4. Build and publish the site

```
cd /tmp/residual
python3 -m residual --data /tmp/rdata site --out /tmp/site --clean
cd /tmp/site && git add -A && git commit -qm "site $(date -u +%FT%TZ)" && git push -q origin HEAD:gh-pages
```

This writes the front page, one page per story at `/YYYY/MM/DD/<slug>/`, daily archive pages, the
Method page and `feed.xml`. Story URLs never change once published. If the push fails, say so in
the summary and carry on.

## 5. Save the data

```
cd /tmp/rdata
git checkout -q --orphan snapshot && git add -A && git commit -qm "data $(date -u +%FT%TZ)" \
  && git push -q -f origin snapshot:data
```

If the force-push is refused, keep the data rather than lose it. Put the snapshot on top of the
existing branch and push normally, then say so in the summary:

```
cd /tmp/rdata && git reset -q --soft origin/data && git commit -qm "data $(date -u +%FT%TZ)" \
  && git push -q origin HEAD:data
```

## 6. Summarize

End with a short summary, written for a phone notification:
- the top three stories today by importance, each as its headline with its shift label and score;
- any lead from this run still on the trail, as its open question;
- anything that failed;
- the site link https://mjsilverman19.github.io/newshound/.

If there were no stories, say so in one line.
