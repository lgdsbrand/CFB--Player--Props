"""Resolve an odds provider's name strings onto our own rows.

The odds adapter deliberately hands back the provider's RAW strings —
`OddsEvent.home_team`, `PropQuote.player_name` — because resolving them is a
fallible step that deserves to fail loudly in one place rather than quietly
inside a transport layer. This module is that place.

WHY THIS IS ITS OWN MODULE, pure and database-free: a name matcher's only
interesting property is its ERROR RATE, and an error rate you cannot measure
offline is one you will discover in production. Everything here takes plain
dicts and returns a result object that records HOW the match was made, so the
ingest job can report a rate rather than an impression.

THE RULE THROUGHOUT: never guess. An ambiguous match is a NON-match. Silently
attaching a book's line to the wrong player would corrupt the edge on a pick
without breaking anything visible, which is the failure mode this project keeps
finding (see the bowl-week collision and the QB rank). Unmatched names are
counted and returned for logging; they are never dropped on the floor.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "SCHOOL_ALIASES",
    "PlayerMatch",
    "PlayerResolver",
    "ResolutionReport",
    "TeamMatch",
    "TeamResolver",
    "normalize",
    "strip_suffix",
]

# Generational suffixes carry no identity and the two sides disagree constantly
# ("Travis Hunter Jr." vs "Travis Hunter"). Stripped from BOTH sides so the
# comparison is symmetric.
_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})

_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalize(raw: str | None) -> str:
    """Fold a name to a comparable form.

    Handles, in order, the four things measured against the live 2026 slate:

      * ACCENTS — the provider writes "San Jose State", we store "San José
        State". NFKD splits the accent into a combining mark, which is then
        dropped.
      * APOSTROPHES AND PERIODS — "Ragin' Cajuns" vs "Ragin Cajuns",
        "St. John's" vs "St Johns".
      * LEADING "THE" — the provider says "Citadel Bulldogs", we store
        "The Citadel".
      * CASE AND SPACING.

    Deliberately NOT handled here: abbreviations ("App State" for "Appalachian
    State"). Those are not a normalization problem — no rule turns one into the
    other — so they are matched structurally or declared in SCHOOL_ALIASES.
    """
    if not raw:
        return ""
    decomposed = unicodedata.normalize("NFKD", raw)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = without_marks.lower()
    # Apostrophes join rather than separate: "ragin'cajuns" must not become two
    # tokens, while a hyphen or slash genuinely does separate.
    lowered = lowered.replace("'", "").replace("’", "").replace("ʻ", "")
    cleaned = _PUNCT.sub(" ", lowered)
    collapsed = _SPACES.sub(" ", cleaned).strip()
    if collapsed.startswith("the "):
        collapsed = collapsed[4:]
    return collapsed


def strip_suffix(tokens: list[str]) -> list[str]:
    """Drop a trailing generational suffix, keeping at least one token."""
    while len(tokens) > 1 and tokens[-1] in _SUFFIXES:
        tokens = tokens[:-1]
    return tokens


# Irreducible school aliases: pairs no normalization rule can bridge, because
# they are different words for the same institution rather than different
# spellings of it.
#
# Kept SMALL and explicit on purpose. Every entry here is a fact about the
# world that will not be re-derived from data, so each one earns its place by
# having been OBSERVED unmatched against a real slate — not added speculatively.
# The structural matcher below handles abbreviation-style differences
# ("Appalachian State" / "App State") without needing an entry.
SCHOOL_ALIASES: dict[str, str] = {
    "umass": "massachusetts",
    "ul monroe": "louisiana monroe",
    "ul lafayette": "louisiana",
    "usc upstate": "south carolina upstate",
}


@dataclass(frozen=True)
class TeamMatch:
    """One resolved team, and the evidence that resolved it.

    `method` exists so the ingest job can report the mix. A run that resolves
    everything by the loosest rule is a different situation from one that
    resolves everything exactly, even though both report 100%.
    """

    team_id: int
    school: str
    provider_name: str
    method: str


@dataclass
class ResolutionReport:
    """What a batch of resolutions actually did. Reported, never inferred."""

    matched: dict[str, TeamMatch] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)
    ambiguous: dict[str, list[str]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.matched) + len(self.unmatched) + len(self.ambiguous)

    @property
    def rate(self) -> float:
        return len(self.matched) / self.total if self.total else 0.0

    def summary(self) -> str:
        return (
            f"{len(self.matched)}/{self.total} resolved ({self.rate:.1%}); "
            f"{len(self.unmatched)} unmatched, {len(self.ambiguous)} ambiguous"
        )


class TeamResolver:
    """Maps a provider's "School Mascot" string onto a teams row.

    The provider concatenates school and mascot into one field with no
    separator, and both halves can differ from ours. The mascot is the more
    stable half — "Mountaineers" is "Mountaineers" everywhere — but it is not
    unique (three schools field Bulldogs), so it is used to NARROW candidates
    and the school half then has to agree.
    """

    def __init__(self, teams: Iterable[dict[str, Any]]) -> None:
        self._by_full: dict[str, list[dict]] = {}
        self._by_school: dict[str, list[dict]] = {}
        self._by_mascot: dict[str, list[dict]] = {}

        for team in teams:
            school = normalize(team.get("school"))
            mascot = normalize(team.get("mascot"))
            if not school:
                continue
            if mascot:
                self._by_full.setdefault(f"{school} {mascot}", []).append(team)
                self._by_mascot.setdefault(mascot, []).append(team)
            self._by_school.setdefault(school, []).append(team)
            alt = normalize(team.get("alt_name"))
            if alt and alt != school:
                self._by_school.setdefault(alt, []).append(team)

    def resolve(self, provider_name: str) -> TeamMatch | list[str] | None:
        """Resolve one provider string.

        Returns a TeamMatch, a list of candidate schools when AMBIGUOUS, or
        None when nothing matched. Ambiguity is reported distinctly from
        absence because they call for different responses: an alias entry
        versus an investigation.
        """
        raw = normalize(provider_name)
        if not raw:
            return None

        # 1. Exact "school mascot".
        hit = self._unique(self._by_full.get(raw))
        if hit is not None:
            return self._match(hit, provider_name, "exact")

        # 2. Mascot-anchored. Find our mascots that end this string, longest
        #    first so "Golden Eagles" wins over "Eagles".
        for mascot in sorted(self._by_mascot, key=len, reverse=True):
            if raw == mascot or not raw.endswith(" " + mascot):
                continue
            school_part = raw[: -len(mascot)].strip()
            candidates = self._by_mascot[mascot]
            scored = [
                (c, _school_affinity(school_part, normalize(c.get("school"))))
                for c in candidates
            ]
            plausible = [(c, s) for c, s in scored if s > 0]
            if not plausible:
                continue
            best = max(s for _, s in plausible)
            winners = [c for c, s in plausible if s == best]
            if len(winners) == 1:
                return self._match(winners[0], provider_name, f"mascot+{best}")
            return sorted(str(w.get("school")) for w in winners)

        # 3. Declared alias, then plain school.
        aliased = SCHOOL_ALIASES.get(raw, raw)
        hit = self._unique(self._by_school.get(aliased))
        if hit is not None:
            return self._match(hit, provider_name, "school")

        return None

    def resolve_all(self, names: Iterable[str]) -> ResolutionReport:
        """Resolve a batch and report the outcome for every input."""
        report = ResolutionReport()
        for name in names:
            outcome = self.resolve(name)
            if isinstance(outcome, TeamMatch):
                report.matched[name] = outcome
            elif isinstance(outcome, list):
                report.ambiguous[name] = outcome
            else:
                report.unmatched.append(name)
        return report

    @staticmethod
    def _unique(rows: list[dict] | None) -> dict | None:
        return rows[0] if rows and len(rows) == 1 else None

    @staticmethod
    def _match(row: dict, provider_name: str, method: str) -> TeamMatch:
        return TeamMatch(
            team_id=int(row["id"]),
            school=str(row["school"]),
            provider_name=provider_name,
            method=method,
        )


def _school_affinity(provider_school: str, our_school: str) -> int:
    """How strongly two school names agree. 0 means "not the same school".

    Graded rather than boolean so mascot ties break on the better school
    agreement — "Mountaineers" narrows to App State and West Virginia, and only
    the school half separates them.

    Scores:
      3  identical, or a declared alias
      2  one is a prefix-word abbreviation of the other ("app" / "appalachian")
      1  they share a distinctive token ("southern miss" / "southern mississippi")
      0  no relationship
    """
    if not provider_school or not our_school:
        return 0
    if provider_school == our_school:
        return 3
    if SCHOOL_ALIASES.get(provider_school) == our_school:
        return 3
    if SCHOOL_ALIASES.get(our_school) == provider_school:
        return 3

    ours = our_school.split()
    theirs = provider_school.split()

    # Token-wise abbreviation: every token of one starts the matching token of
    # the other, in order. "app state" vs "appalachian state".
    if len(ours) == len(theirs):
        pairs = zip(ours, theirs, strict=True)
        if all(a.startswith(b) or b.startswith(a) for a, b in pairs):
            return 2

    # Initialism: "LIU" for "Long Island University". A general rule rather than
    # an alias entry, because it derives the abbreviation from our own stored
    # name instead of asserting a fact about one school. Requires a single
    # token on one side, so it cannot fire between two spelled-out names.
    if len(theirs) == 1 and len(ours) > 1:
        if theirs[0] == "".join(t[0] for t in ours):
            return 2
    if len(ours) == 1 and len(theirs) > 1:
        if ours[0] == "".join(t[0] for t in theirs):
            return 2

    # "State" and "University" appear everywhere and distinguish nothing, so a
    # shared token only counts if it is not one of those.
    generic = {"state", "university", "college", "the", "of", "at"}
    shared = (set(ours) & set(theirs)) - generic
    if shared:
        return 1

    # Whole-string prefix, for "sam houston" / "sam houston state".
    if our_school.startswith(provider_school) or provider_school.startswith(our_school):
        return 1

    return 0


@dataclass(frozen=True)
class PlayerMatch:
    """One resolved player, and how confidently."""

    player_id: int
    name: str
    provider_name: str
    method: str


class PlayerResolver:
    """Maps a provider's player-name string onto a players row.

    THE DESIGN POINT: candidates are scoped to the two rosters playing in the
    game the quote came from. That turns a ~21,000-player problem into a
    ~200-player one, and it is what makes matching safe rather than merely
    plausible — "J. Smith" is hopeless nationally and usually unique inside one
    game. Callers therefore build one resolver PER GAME.

    Ambiguity is never broken by picking. Two players on the same roster who
    both answer to a provider string means we do not know which one the book
    priced, and attaching the line to either is a coin flip that would show up
    as a confident, wrong edge.
    """

    def __init__(self, roster: Iterable[dict[str, Any]]) -> None:
        self._by_full: dict[str, list[dict]] = {}
        self._by_last_initial: dict[tuple[str, str], list[dict]] = {}
        self._by_last: dict[str, list[dict]] = {}

        for player in roster:
            tokens = strip_suffix(normalize(player.get("name")).split())
            if not tokens:
                continue
            self._by_full.setdefault(" ".join(tokens), []).append(player)
            last = tokens[-1]
            self._by_last.setdefault(last, []).append(player)
            if len(tokens) > 1:
                self._by_last_initial.setdefault((last, tokens[0][0]), []).append(
                    player
                )

    def resolve(self, provider_name: str) -> PlayerMatch | list[str] | None:
        """Resolve one provider player string.

        Returns a PlayerMatch, a list of candidate names when AMBIGUOUS, or
        None when nothing matched.
        """
        tokens = strip_suffix(normalize(provider_name).split())
        if not tokens:
            return None

        # 1. Whole name, suffixes already folded off both sides.
        outcome = self._pick(self._by_full.get(" ".join(tokens)), provider_name, "exact")
        if outcome is not None:
            return outcome

        last = tokens[-1]

        # 2. Last name + first initial. Covers "T.J. Moore" vs "Tavion Moore"
        #    and every "Mike"/"Michael" the two sides disagree about.
        if len(tokens) > 1:
            outcome = self._pick(
                self._by_last_initial.get((last, tokens[0][0])),
                provider_name,
                "last+initial",
            )
            if outcome is not None:
                return outcome

        # 3. Last name alone, and ONLY when the roster holds exactly one.
        #    Deliberately last: it is the rule most likely to be wrong, so it
        #    only ever fires where there is nothing to be wrong about.
        outcome = self._pick(self._by_last.get(last), provider_name, "last-only")
        if outcome is not None:
            return outcome

        return None

    def resolve_all(self, names: Iterable[str]) -> ResolutionReport:
        report = ResolutionReport()
        for name in names:
            outcome = self.resolve(name)
            if isinstance(outcome, PlayerMatch):
                report.matched[name] = outcome
            elif isinstance(outcome, list):
                report.ambiguous[name] = outcome
            else:
                report.unmatched.append(name)
        return report

    @staticmethod
    def _pick(
        rows: list[dict] | None, provider_name: str, method: str
    ) -> PlayerMatch | list[str] | None:
        if not rows:
            return None

        if len(rows) > 1:
            # OUR OWN DATA CONTAINS DUPLICATES. Measured on 2025: 147 name
            # groups share a team-season, 135 of them pairing a real player
            # with an empty stub — jersey -1, position OTHER, zero games —
            # created by a later ingest pass for athletes seen in play data but
            # never matched to a roster. "Johntay Cook" and "Johntay Cook II"
            # are one human with two CFBD athlete ids.
            #
            # So prefer a candidate that has actually played. This is evidence,
            # not a guess: a book does not post a prop for a player who has
            # never taken a snap. It only fires when it resolves the question
            # OUTRIGHT — if two candidates have both played, we still do not
            # know which one was priced, and we still refuse.
            #
            # `games` is supplied by the caller rather than looked up, so this
            # module stays database-free and the rule stays testable.
            active = [r for r in rows if (r.get("games") or 0) > 0]
            if len(active) == 1:
                rows, method = active, f"{method}+active"
            else:
                return sorted(str(r.get("name")) for r in rows)

        row = rows[0]
        return PlayerMatch(
            player_id=int(row["id"]),
            name=str(row["name"]),
            provider_name=provider_name,
            method=method,
        )
