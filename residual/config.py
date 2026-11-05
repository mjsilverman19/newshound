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

    # Polling
    poll_seconds: int = 120
    platforms: list[str] = field(default_factory=lambda: ["polymarket", "kalshi"])
    polymarket_max_events: int = 3000      # events sorted by 24h volume
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
    hunt_min_score: float = 30             # importance needed before the agent goes looking
    rehunt_gain: float = 10                # hunt again if a story's score rises this much

    # Scheduled mode (one scan per run; Claude in the session investigates)
    state_path: str = "prices.json.gz"
    max_new_per_run: int = 6
    max_rechecks_per_run: int = 6

    # Re-checks for moves nobody explained
    recheck_after_hours: list[float] = field(default_factory=lambda: [1, 6, 24])

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
