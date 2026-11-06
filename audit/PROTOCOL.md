# Hunting-stage cost and quality test: protocol

Written 2026-11-06, before any replay was run. Don't edit the measures or the decision rule after
results come in; add a dated note instead.

## The test set

`frozen/items.json`: 20 real queue items from Thursday 2026-11-05, frozen at 23:41 UTC (6:41 PM ET)
from a snapshot of the data taken that evening. 16 are day-mode topics (ranks 1 to 20 that today's
filters still accept); 4 are hourly-era stories that were hunted on Thursday. `frozen/thursday.db` is
the snapshot with every hunt result for Thursday blanked, so no scent or list of other stories gives
an answer away. `audit/freeze.py` built both.

## Variants

`variants.json`. A is the live setting. B to E each change one thing; F combines the cheap options; A2
repeats A to measure how much two identical runs differ. Every item runs under every variant, item by
item, so if the budget runs out every variant has hunted the same items.

## Measures, per hunt

1. **Cost**, from the ledger's usage and `prices.json`.
2. **Published**: the code's verdict is reported or timing unclear.
3. **Published on Thursday's evidence**: the same verdict using only cause sources published by
   frozen time + 1 hour. Replays run days later and search can return later reports; this measure
   removes that hindsight.
4. **Matches the reference** (12 items with one, `frozen/reference.json`): the published headline,
   dek and summary contain a term from every group. The references are the verified cause reports
   from Thursday's published stories (and, for Hegseth, the report found that evening).
5. **Wrong story**: published, a reference exists, and it doesn't match.
6. **Unverified citations**: sources the model cited that its own searches never returned.
7. **FT overlap**: the published headline and dek match a subject on ft.com's Thursday front page
   (`frozen/benchmark_ft.json`).

## Decision rule

Noise = the difference between A and A2 in reference matches. A variant is acceptable if, against A:
- reference matches fall by no more than the noise (and by at most 1);
- wrong stories don't rise;
- unverified citations rise by at most 1 per 20 hunts.

Recommend the cheapest acceptable variant by cost per reference match, and say by how much.

## After the recommendation

Write the predicted cost per published story and per run in `PREDICTION.md` before changing the live
settings. After a week live, compare it with the ledger and with Anthropic's cost report
(`python -m residual audit reconcile`).

## Known limits

- 20 items and one repeat of the baseline can show large differences, not small ones.
- Hindsight is removed from measure 3 only; the model may still see later reports while it searches.
- Merges between stories aren't tested: each hunt runs alone, and any merge it asks for is recorded
  but not applied.
