"""Usage share: a player's targets and carries as a fraction of his offence's.

SPORT-AGNOSTIC CORE (CLAUDE.md §3). Both providers give the numerator and the
denominator in the same table, so nothing here knows which league it is looking
at -- it takes a sport only to scope the pass to one league's rows.

WHAT IT DOES NOT WRITE. `player_game_stats.snap_share` is not computed here. It
is the provider's own `offense_pct`, written by the nflverse snap adapter beside
`snaps`, because team offensive snaps cannot be recovered from this table: the
offensive line takes snaps and has no box-score row, so the largest count we
store is a skill player's and every share derived from it would be too high.
Migration 0071 records the whole reasoning.

THE COMPLETENESS GUARD ON TARGETS IS THE POINT OF THIS MODULE. `targets` is
attributed play by play rather than read off a box score, and college
attribution is incomplete in a way that does NOT cancel out of a ratio -- whole
roster rows are missing, so the players who do appear absorb the share of those
who do not. Measured on cfb 2025 FBS, a player's mean target share in team-games
under the 0.80 ratio runs 1-2 points above his own complete games, in the same
direction for every position. Below the threshold the share is written as NULL.

IDEMPOTENT ON PURPOSE. The UPDATE compares before it writes, so a re-run over an
unchanged season touches no rows and bumps no `updated_at`. That also makes the
returned rowcount mean "rows changed", which is the number worth logging.
"""

from __future__ import annotations

from typing import Any

from worker.db import execute, fetch_all
from worker.logging_setup import get_logger

log = get_logger(__name__)

#: Attributed team targets must reach this fraction of the same team's pass
#: attempts before any target share from that team-game is published.
#:
#: 0.80 is a measurement, not a preference: under it at least a fifth of the
#: team's pass plays produced no attributed target. NFL seasons sit at a 0.96
#: median with a 0.87 fifth percentile and lose one team-game in 570 to this;
#: college sits at 0.91 with a 0.65 fifth percentile and loses about one in
#: seven. Raising it would throw away sound college data, lowering it would
#: publish shares inflated by more than the point or two a rounded percentage
#: can absorb. Migration 0071 carries the full table.
MIN_TARGET_ATTRIBUTION = 0.80

#: Written by this module. `snap_share` is deliberately absent -- see the module
#: docstring. Named once so the audit checks and tests can assert against the
#: same list rather than restating it.
DERIVED_SHARE_COLUMNS = ("target_share", "rush_share")


def build_usage_shares(season: int, sport: str) -> int:
    """Recompute target and rush share for one season of one sport.

    Returns rows CHANGED, not rows considered.

    Reads only the database, so it is safe to re-run at any time and is ordered
    after the box-score ingest rather than inside it: the denominator is a sum
    over every row of a team-game, so it cannot be known until the last one of
    them has landed.
    """
    changed = execute(
        """
        with team_game as (
            select s.game_id,
                   s.team_id,
                   sum(s.targets)       as team_targets,
                   sum(s.rush_attempts) as team_rush_attempts,
                   sum(s.pass_attempts) as team_pass_attempts
              from player_game_stats s
              join games g on g.id = s.game_id
             where s.season = %(season)s
               and g.sport  = %(sport)s
             group by s.game_id, s.team_id
        ),
        computed as (
            select s.id,
                   case
                     when s.targets is null then null
                     when coalesce(tg.team_targets, 0) = 0 then null
                     -- No pass attempts recorded means the attribution cannot
                     -- be checked at all, which is not the same as passing the
                     -- check. Withhold rather than assume.
                     when coalesce(tg.team_pass_attempts, 0) = 0 then null
                     when tg.team_targets::numeric
                          < %(min_attribution)s * tg.team_pass_attempts then null
                     else round(s.targets::numeric / tg.team_targets, 4)
                   end as target_share,
                   case
                     when s.rush_attempts is null then null
                     when coalesce(tg.team_rush_attempts, 0) = 0 then null
                     else round(s.rush_attempts::numeric / tg.team_rush_attempts, 4)
                   end as rush_share
              from player_game_stats s
              join team_game tg
                on tg.game_id = s.game_id
               and tg.team_id = s.team_id
             where s.season = %(season)s
        )
        update player_game_stats s
           set target_share = c.target_share,
               rush_share   = c.rush_share
          from computed c
         where c.id = s.id
           and (s.target_share is distinct from c.target_share
                or s.rush_share is distinct from c.rush_share)
        """,
        {
            "season": season,
            "sport": sport,
            "min_attribution": MIN_TARGET_ATTRIBUTION,
        },
    )
    return changed


def season_summary(season: int, sport: str) -> dict[str, Any]:
    """Coverage for one season, for the job to log.

    `withheld_targets` is the number this module exists to produce, so it is
    reported every run rather than left to be discovered: a sudden jump in it is
    an ingest that has started dropping rows, and a fall to zero on college
    would mean the guard has stopped doing anything.
    """
    rows = fetch_all(
        """
        select count(*)                                            as player_games,
               count(*) filter (where s.targets is not null)        as with_targets,
               count(s.target_share)                                as with_target_share,
               count(*) filter (where s.rush_attempts is not null)  as with_rushes,
               count(s.rush_share)                                  as with_rush_share,
               count(s.snap_share)                                  as with_snap_share,
               round(avg(s.target_share) filter
                     (where s.position_group = 'WR'), 4)            as mean_wr_target_share
          from player_game_stats s
          join games g on g.id = s.game_id
         where s.season = %(season)s and g.sport = %(sport)s
        """,
        {"season": season, "sport": sport},
    )
    summary = dict(rows[0]) if rows else {}
    with_targets = summary.get("with_targets") or 0
    summary["withheld_targets"] = with_targets - (summary.get("with_target_share") or 0)
    return summary


__all__ = [
    "DERIVED_SHARE_COLUMNS",
    "MIN_TARGET_ATTRIBUTION",
    "build_usage_shares",
    "season_summary",
]
