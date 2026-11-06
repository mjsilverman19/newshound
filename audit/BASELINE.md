# Baseline: Thursday 2026-11-05, before the ledger

Source: the per-story lines in the logs of the seven GitHub Actions runs between 22:25 and 00:38 UTC
that hunted (26 hunts, all on `claude-opus-5-5`), priced with `prices.json` (checked 2026-11-06).
These lines recorded uncached input, cache reads, output, searches and fetches. They did **not**
record cache writes or the labelling calls, so everything below understates the real cost.

## What the logs show

| Component | Tokens or count | $ | Share of logged $ |
|---|---|---|---|
| Web searches | 259 | 2.59 | 46% |
| Output | 102,211 | 2.04 | 36% |
| Cache reads | 5,023,016 | 1.00 | 18% |
| Uncached input | 528 | 0.00 | 0% |
| **Logged total** | | **5.64** | |
| Cache writes | not logged | ? | |
| Labelling (Haiku) | not logged | small | |

- Per hunt (logged cost): p50 $0.215, p90 $0.272, max $0.328.
- 14 of 26 hunts published. Logged cost per published story: $0.40.
- Hunts that published nothing: $2.46, 44% of logged spend.
- The Rubio story, hunted 6 times because of a duplicate and a re-hunt bug (both since fixed): $1.34, 24%.

## What this changes

In tokens, re-reading the context is 97% of the volume. In dollars it is the smallest logged item,
because cache reads cost a twentieth of base input. Searches and output cost more.

## The biggest unknown: cache writes

Every new search result and fetched page is written to the cache once, at $5 per million tokens on
Opus. If each hunt adds 30,000 to 100,000 tokens of new material, writes cost $3.90 to $13.00 for these
26 hunts, which is 41% to 70% of the true total. The first run with the ledger will measure this, and the
reconciliation against Anthropic's cost report will confirm it.
