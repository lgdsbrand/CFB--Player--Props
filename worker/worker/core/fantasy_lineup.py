"""The five starters a side the fantasy game model reads (migration 0093).

SPORT-AGNOSTIC CORE, pure and database-free. The client's sheet takes a QB,
an RB, two WRs and a TE per team, typed by hand, and looks up each one's
weekly fantasy projection. Here nobody types anything (the user's instruction,
2026-10-14), so the lineup is chosen by rule:

  * Every slot — the AVAILABLE players at the position with the highest
          projection: one QB, one RB, the top two WRs, one TE. Projections
          rather than depth-chart slots, because ESPN lists receivers by
          formation spot (X, Z, slot), not by importance, and the sheet's
          WR1/WR2 mean "the two who matter".
  * The QB too. The first version took the depth chart's QB1, and the first
          real week showed why not: on 2026-10-14 Lamar Jackson was
          Questionable and still QB1, and CBS projected him 8.9 against Tyler
          Huntley's 12.3. CBS had already priced in who would probably start;
          the depth chart had not. The depth chart is the fallback, for a team
          CBS lists no available QB for (then on the default points).

AVAILABLE means no designation in `UNAVAILABLE`. Questionable plays: most
questionable players do, and the sheet had no way to say otherwise.

A STARTER WITH NO PROJECTION GETS THE SHEET'S DEFAULT. The sheet's lookups
fall back to a fixed number when a name is not on CBS's list (QB 17, RB 16,
WR 16, TE 12 — the XLOOKUP's fourth argument). The rule is kept, and the row
says so (`points_source = 'default'`), so it can be seen and questioned.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

SLOTS: tuple[str, ...] = ("QB", "RB", "WR1", "WR2", "TE")

#: The sheet's fallback points when a starter is not on the projection list.
DEFAULT_POINTS: dict[str, float] = {"QB": 17.0, "RB": 16.0, "WR1": 16.0, "WR2": 16.0, "TE": 12.0}

#: Designations that keep a player out of the lineup. Sleeper's spellings
#: (player_injuries.status): game designations Out and Doubtful, and the
#: longer-term IR, PUP, Sus (suspended), NA (non-football injury) and DNR.
UNAVAILABLE: frozenset[str] = frozenset({"Out", "Doubtful", "IR", "PUP", "Sus", "NA", "DNR"})


@dataclass(frozen=True)
class Projected:
    """One player's weekly projection, as captured."""

    player_id: int | None
    name: str
    position: str
    points: float


@dataclass(frozen=True)
class DepthQB:
    """A quarterback on the depth chart; depth 1 is the starter."""

    player_id: int | None
    name: str
    depth: int


@dataclass(frozen=True)
class LineupSlot:
    """One starter, and how he was chosen."""

    slot: str
    player_id: int | None
    player_name: str | None
    fantasy_points: float
    points_source: str  # 'cbs' | 'default'
    picked_by: str  # 'depth_chart' | 'projection' | 'none'
    skipped: str | None


#: (player_id, name) -> designation or None.
StatusOf = Callable[[int | None, str], str | None]


def _skip_note(passed: list[tuple[str, str]]) -> str | None:
    return ", ".join(f"{name} ({status})" for name, status in passed) or None


def _projection_for(
    player_id: int | None, name: str, pool: Sequence[Projected], key: Callable[[str], str]
) -> Projected | None:
    """The projection of one player: by id when both sides have one, else by name."""
    if player_id is not None:
        for p in pool:
            if p.player_id == player_id:
                return p
    wanted = key(name)
    by_name = [p for p in pool if key(p.name) == wanted]
    return by_name[0] if len(by_name) == 1 else None


def _pick_qb(
    depth: Sequence[DepthQB],
    pool: Sequence[Projected],
    status_of: StatusOf,
    key: Callable[[str], str],
) -> LineupSlot:
    by_projection = _pick_by_projection(["QB"], pool, status_of)[0]
    if by_projection.picked_by != "none":
        return by_projection
    # CBS lists no available QB for the team: the depth chart names one, on the
    # sheet's default points unless CBS happens to list him under another name.
    passed = _parse_note(by_projection.skipped)
    for qb in sorted(depth, key=lambda q: q.depth):
        status = status_of(qb.player_id, qb.name)
        if status in UNAVAILABLE:
            if (qb.name, status) not in passed:
                passed.append((qb.name, status))
            continue
        projection = _projection_for(qb.player_id, qb.name, pool, key)
        player_id = qb.player_id
        if player_id is None and projection is not None:
            player_id = projection.player_id
        return LineupSlot(
            slot="QB",
            player_id=player_id,
            player_name=qb.name,
            fantasy_points=projection.points if projection else DEFAULT_POINTS["QB"],
            points_source="cbs" if projection else "default",
            picked_by="depth_chart",
            skipped=_skip_note(passed),
        )
    return LineupSlot(**{**by_projection.__dict__, "skipped": _skip_note(passed)})


def _parse_note(note: str | None) -> list[tuple[str, str]]:
    """The (name, status) pairs a `skipped` note was built from."""
    out = []
    for part in (note or "").split(", "):
        if part:
            name, _, status = part.rpartition(" (")
            out.append((name, status.removesuffix(")")))
    return out


def _pick_by_projection(
    slots: Sequence[str],
    pool: Sequence[Projected],
    status_of: StatusOf,
    passed_before: Sequence[tuple[str, str]] = (),
) -> list[LineupSlot]:
    """Fill `slots` (all one position) with the best available projections, in order."""
    ranked = sorted(pool, key=lambda p: (-p.points, p.name))
    out: list[LineupSlot] = []
    passed: list[tuple[str, str]] = list(passed_before)
    for p in ranked:
        if len(out) == len(slots):
            break
        status = status_of(p.player_id, p.name)
        if status in UNAVAILABLE:
            passed.append((p.name, status))
            continue
        out.append(LineupSlot(
            slot=slots[len(out)],
            player_id=p.player_id,
            player_name=p.name,
            fantasy_points=p.points,
            points_source="cbs",
            picked_by="projection",
            skipped=_skip_note(passed),
        ))
        passed = []
    for slot in slots[len(out):]:
        out.append(LineupSlot(
            slot=slot, player_id=None, player_name=None,
            fantasy_points=DEFAULT_POINTS[slot], points_source="default",
            picked_by="none", skipped=_skip_note(passed),
        ))
        passed = []
    return out


def pick_lineup(
    projections: Sequence[Projected],
    depth_qbs: Sequence[DepthQB],
    status_of: StatusOf,
    key: Callable[[str], str],
) -> list[LineupSlot]:
    """One team's five starters for one week, in SLOTS order.

    `projections` are that team's projections for the week; `depth_qbs` its
    depth chart's quarterbacks; `status_of` its injury report; `key` the name
    comparison form.
    """
    by_position = {
        pos: [p for p in projections if p.position == pos] for pos in ("QB", "RB", "WR", "TE")
    }
    return [
        _pick_qb(depth_qbs, by_position["QB"], status_of, key),
        *_pick_by_projection(["RB"], by_position["RB"], status_of),
        *_pick_by_projection(["WR1", "WR2"], by_position["WR"], status_of),
        *_pick_by_projection(["TE"], by_position["TE"], status_of),
    ]


__all__ = [
    "DEFAULT_POINTS",
    "SLOTS",
    "UNAVAILABLE",
    "DepthQB",
    "LineupSlot",
    "Projected",
    "pick_lineup",
]
