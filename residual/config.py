"""Settings. Defaults live here; override any of them in a TOML file passed with --config."""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    # Storage
    db_path: str = "residual.db"
    briefs_dir: str = "briefs"

    # How stories are found. "day": each run ranks topics by how much expectations changed over the last
    # 24 hours (residual/day.py). "hourly": the original detector of sharp moves between hourly runs.
    mode: str = "day"

    # Polling
    poll_seconds: int = 120
    platforms: list[str] = field(default_factory=lambda: ["polymarket", "kalshi"])
    polymarket_max_events: int = 6000      # events sorted by 24h volume
    kalshi_max_pages: int = 40             # 200 events per page

    # Detection
    move_threshold: float = 0.05           # 5 points on a 0-1 price
    window_minutes: int = 30
    cooldown_hours: float = 6.0            # one flag per market per cooldown, unless it moves again
    min_liquidity_usd: float = 5_000       # Polymarket order-book liquidity
    min_open_interest: float = 1_000       # Kalshi contracts
    max_spread: float = 0.10               # ignore mid prices from very wide books
    skip_if_ends_within_hours: float = 12  # live games, same-day weather, results being counted
    min_window_volume_usd: float = 1_000   # skip moves with almost no money behind them
    excluded_categories: list[str] = field(default_factory=lambda: [
        # live events and markets that just track a public price feed
        "climate and weather", "weather", "crypto", "financials",
        "esports", "games", "up or down", "updown", "hit price", "equities", "stocks",
        "commodities", "recurring", "multi strikes",
    ])
    excluded_keywords: list[str] = field(default_factory=lambda: [
        " vs. ", " vs ", "up or down", "game 1", "game 2", "game 3", "map 1", "map 2",
        "temperature", "temp in", "price of bitcoin", "bitcoin above", "ethereum above",
    ])
    # Macro markets let back in from the excluded finance categories: rates, recessions, index and oil
    # levels shift on real news. Only long-dated ones count, so same-day price feeds stay out.
    macro_categories: list[str] = field(default_factory=lambda: [
        "financials", "equities", "stocks", "commodities",
    ])
    macro_keywords: list[str] = field(default_factory=lambda: [
        " fed ", "federal reserve", "fomc", "interest rate", "rate cut", "rate hike", "recession",
        "inflation", " cpi ", " gdp ", "unemployment", "jobless", "s&p", "nasdaq", " dow ",
        " oil ", "brent", " wti ", "bank of england", " ecb ", "bank of japan", "tariff", "treasury", "yield",
        "default", "shutdown",
    ])
    macro_min_days_to_end: float = 7

    # Day mode (see residual/day.py)
    day_min_change: float = 0.03           # a market must have moved at least 3 points in 24 hours
    day_min_usd: float = 500               # and traded at least this much in those 24 hours
    day_min_hours_to_end: float = 24       # skip markets resolving within a day (live games, counts)
    day_soft_tags: list[str] = field(default_factory=lambda: [
        "sports", "sport", "games", "movies", "oscars", "culture", "music", "gaming", "box office", "awards",
        "celebrities", "esports", "tennis", "soccer", "nfl", "nba", "mlb", "nhl", "golf", "cricket", "f1",
        "entertainment",
    ])
    day_settling_tags: list[str] = field(default_factory=lambda: ["election night"])
    day_excluded_keywords: list[str] = field(default_factory=lambda: [
        "exact score", "halftime", " win on 20",
        "margin of victory",               # races already decided, being counted
    ])
    # Tags too broad to tie two events together (a tag also counts as broad when it is on more than
    # day_tag_rarity of the day's moving events)
    day_generic_tags: list[str] = field(default_factory=lambda: [
        "politics", "geopolitics", "world", "trump", "united states", "elections", "global elections",
        "main election", "world elections", "us election", "business", "finance", "economy", "tech", "ai",
        "culture", "middle east", "military", "earn 3.25%", "hide from new", "derivatives", "post election",
        "big tech", "trump presidency", "peace deal", "international election props", "polling", "primaries",
        "climate & science", "science", "qualification", "season stats", "ipos", "ipo", "midterms",
        "2028 election", "commodities", "negotiation topics", "debut", "ai releases", "parent for derivative",
    ])
    day_tag_rarity: float = 0.06
    day_name_max_events: int = 4           # a name ties events together only if at most this many share it
    day_max_events: int = 400              # the biggest moving events considered for grouping
    day_story_min_score: float = 30        # topics below this are not tracked as stories
    day_max_topics: int = 20               # tracked per run
    day_hunt_min_score: float = 33         # a topic needs this to be hunted
    day_strong_score: float = 37           # "strong" signal: this score and this much normal trading
    day_strong_usd: float = 40_000
    day_timing_topics: int = 10            # read intraday price history for this many leading topics
    day_source_lookback_hours: float = 36  # a cause must be published within this long before the window

    # Wallet signals (Polymarket only)
    fresh_wallet_max_trades: int = 10
    wallets_to_profile: int = 5

    # Investigation
    anthropic_model_quick: str = "claude-haiku-5-5"
    anthropic_model_deep: str = "claude-sonnet-5-5"
    web_search_tool: str = "web_search_20250305"
    quick_max_searches: int = 3
    deep_max_searches: int = 12
    max_investigations_per_hour: int = 20
    reaction_slack_minutes: int = 15       # a source this soon after the move starts can still be the cause

    # Stories
    hunt_min_score: float = 25             # importance needed before the agent goes looking
    rehunt_gain: float = 10                # hunt again if a story's score rises this much
    # A story needs this much traded across its markets to lead the front page or rank above heavier
    # stories; thinner ones are still published, listed after the rest.
    lead_min_money_usd: float = 10_000
    # Words that never tie two stories together when grouping by the names in them (too common)
    group_stopwords: list[str] = field(default_factory=lambda: [
        "will", "who", "what", "which", "when", "before", "after", "next", "trump", "biden", "harris",
        "president", "presidential", "prime", "minister", "secretary", "state", "states", "united",
        "house", "senate", "congress", "cabinet", "administration", "white", "government", "party",
        "election", "elections", "republican", "republicans", "democrat", "democrats", "democratic",
        "nominee", "nomination", "odds", "january", "february", "march", "april", "june", "july",
        "august", "september", "october", "november", "december", "us", "usa", "uk", "eu", "et", "gop",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
        "winner", "win", "place", "department", "representatives", "seat", "seats", "most", "first",
        "second", "third", "mayor", "governor", "court", "supreme", "federal", "national", "general",
        "legislative", "parliament", "leader", "out", "end", "year", "the", "and", "new", "today", "is",
        "does", "did", "has", "have", "can", "how", "why", "any", "there", "this", "that", "yes", "no",
    ])

    # Scheduled mode (one scan per run; Claude in the session investigates)
    state_path: str = "prices.json.gz"
    max_new_per_run: int = 6
    max_rechecks_per_run: int = 6

    # Scheduled mode on the Claude API (`cycle`, run by .github/workflows/hourly.yml)
    hunt_model: str = "claude-opus-5-5"
    hunt_effort: str = "medium"            # low | medium | high | xhigh | max
    hunt_fallbacks: bool = True            # re-run a declined request on Anthropic's recommended fallback model
    label_model: str = "claude-haiku-5-5"
    web_search_tool_api: str = "web_search_20260209"
    web_fetch_tool_api: str = "web_fetch_20260209"
    hunt_max_searches: int = 13            # 2-3 first searches, then up to about 10 more
    recheck_max_searches: int = 5
    weak_max_searches: int = 3             # day mode: searches for a topic with a weak signal
    moderate_max_searches: int | None = None  # day mode: searches for a moderate signal (None: no extra cap)
    hunt_fetch_max_tokens: int | None = None  # cap on each fetched page (the fetch tool's max_content_tokens)
    ledger_keep_transcript_days: int = 14
    hunt_max_fetches: int = 10
    hunt_max_turns: int = 20               # API round trips per story

    # Re-checks for moves nobody explained
    recheck_after_hours: list[float] = field(default_factory=lambda: [1, 6, 24])

    # Sourcing: sites that may appear as context but never count as the report that explains a story
    # (aggregators, re-posters and partisan blogs). Matched on the end of the host name.
    background_only_domains: list[str] = field(default_factory=lambda: [
        # aggregators and re-posters
        "newsbeep.com", "biztoc.com", "newswav.com", "headtopics.com", "pressbee.net", "pricer.lt",
        "newscord.org", "ground.news", "newsbreak.com", "inkl.com", "flipboard.com", "politicalwire.com",
        "osint613.com", "zerohedge.com", "thelibertydaily.com", "newsnow.co.uk", "memeorandum.com",
        "allsides.com", "theconservativetreehouse.com",
        # partisan blogs
        "townhall.com", "rawstory.com", "dailykos.com", "breitbart.com", "thegatewaypundit.com",
        "redstate.com", "alternet.org", "occupydemocrats.com", "twitchy.com", "hotair.com",
        "pjmedia.com", "commondreams.org", "palmerreport.com", "westernjournal.com", "bizpacreview.com",
    ])

    # Image hosts that refuse to be shown on other sites (the browser blocks them)
    unembeddable_image_hosts: list[str] = field(default_factory=lambda: ["yimg.com", "zenfs.com"])

    # The public site
    site_url: str = "https://mjsilverman19.github.io/newshound/"
    site_title: str = "Newshound"
    # Importance score -> how the page names the shift in expectations (highest band first)
    shift_bands: list[list] = field(default_factory=lambda: [
        [50, "Extraordinary shift"], [38, "Major shift"], [34, "Large shift"], [30, "Notable shift"], [25, "Moderate shift"],
        [0, "Small shift"],
    ])

    @classmethod
    def load(cls, path: str | None) -> "Config":
        cfg = cls()
        if path:
            data = tomllib.loads(Path(path).read_text())
            known = {f.name for f in dataclasses.fields(cls)}
            unknown = set(data) - known
            if unknown:
                raise SystemExit(f"Unknown config keys: {', '.join(sorted(unknown))}")
            for k, v in data.items():
                setattr(cfg, k, v)
        return cfg
