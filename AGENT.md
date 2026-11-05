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

Give stories about the same subject the same topic. The front page shows them in one quiet line
("Also watching Nigeria's 2027 race and Vance's 2028 standing").

## 3. Hunt each story

Read `/tmp/queue.json`. Each item has `story_id`, `kind` (`hunt` or `recheck`), `scent` (what the
markets are saying, the money behind it, the resolution rules, and anything already found),
`move_start_utc` and `other_stories_today`. Handle the items one at a time, highest score first.

### Reading the scent

Before you search, work out from the scent what kind of development would produce this shift. For
example, "the odds Rubio leaves fell 26 points" means something made him look more likely to stay:
a denial, a statement of support, or a report being knocked down. Search for that.

If several markets moved together, find the single development behind all of them. If two queued
stories, or a queued story and one in `other_stories_today`, are really the same news, merge them
(see `merge_with` below).

### Day mode (items whose `mode` is `day`, the default)

The scent is a whole topic: every market about it that moved over the last 24 hours, how much money
normally trades in them, a signal strength (`strong`, `moderate` or `weak`), and when during the day
the lead market moved. The question is what happened today on this topic and how much it matters, not
what happened in one particular minute.

- Find the development behind the shift as a whole. If this topic and one in `other_stories_today`
  have the same cause (a market rout that moved both an IPO's timing and rate expectations, say),
  merge them and write the story about the shared cause.
- A cause must be from the day of the shift. The code treats anything published more than about a day
  and a half before the 24-hour window as background, whatever you call it.
- If the shift has no single trigger (a slow drift, or the knock-on of bigger news), say so in the
  summary and write about the bigger news if you can source it. Otherwise record an open lead.
- For a `weak` signal, run two or three searches. If nothing specific turns up, record an open lead
  and move on.
- `why_it_matters` should say how much this changes things, in plain terms.

### Sourcing

- Report only sources that really appeared in your search results or that you fetched. Never invent
  or guess a URL.
- Prefer primary sources and established outlets: the official statement, filing, docket, agency
  page or post by the person involved, and wire services and major news organizations. Find the
  original report, not a write-up of it.
- Aggregators, re-posters and partisan blogs (for example Political Wire, ZeroHedge, Townhall, Raw
  Story, NewsBeep, BizToc) are background at most. The code will not let them explain a story even
  if you mark them `cause`, so look for the established or primary source behind them.
- Record each source's publication time as precisely as the page shows it. Fetch the page when a
  search result doesn't show the time. Use ISO 8601 with a time zone
  (`2026-11-04T13:05:00-05:00`), a bare date if the page shows only a date, or `null` if you can't
  tell. Never guess a time or a time zone.
- For posts on X, the post ID encodes when it was posted:
  `python3 -c "print(__import__('datetime').datetime.utcfromtimestamp(((ID >> 22) + 1288834974657) / 1000))"`
  gives the time in UTC.
- Look beyond news articles: posts on X, official statements and press releases, government and
  agency sites, court dockets, filings, schedules, sports injury reports, and the market's
  resolution source.
- Never stretch weak or general background into a cause. If you find nothing specific, say so.
  An open lead is a real and useful result.

### For `hunt` items

Start with 2 or 3 searches on the most likely development. If you find it, confirm it, get the
publication time of the earliest report, and stop. Otherwise dig deeper with up to about 10 more
searches. Note any report published after the move started too.

### For `recheck` items

Nothing explained this story when it was first hunted. Run up to 5 searches for anything published
since `move_start_utc` that reports the development behind it. If you find reporting, write the
story up in full (headline, dek, summary, why it matters, dateline, image) so the lead is promoted to
a story. If not, record the new searches and keep the question.

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
     "relation": "cause | consequence | background | market_coverage",
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

`relation` is what the code uses to decide the status:
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

The code decides the status (reported, timing unclear, or nothing reported yet) from the sources and
their times. Don't override it. A story is published only when it is reported, timing unclear, or a
lead that a recheck later found reporting for. Everything else stays on the trail.

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
