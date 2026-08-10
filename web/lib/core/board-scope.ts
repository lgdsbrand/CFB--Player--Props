/**
 * Which offenses the board will actually show.
 *
 * This module exists because two surfaces have to agree on one answer and did
 * not. `lib/data/board.ts` restricts rows to displayed conferences, while the
 * weekly targets panel ranked every FBS matchup on the slate. Since the softest
 * defenses in the country are overwhelmingly G5, the panel spent most of its
 * rows recommending games the board structurally could not contain: measured on
 * 2025 week 8 against production, **17 of 20 target links led to "No players
 * match"**, every dead one a Sun Belt / MAC / C-USA / Mountain West pairing.
 *
 * So the rule lives in one place and both callers read it from here. Anything
 * that narrows the board must narrow the panel by the same predicate, or the
 * panel goes back to advertising players nobody can reach.
 *
 * NOT IN `targets.ts`: that module ranks matchups and states plainly that
 * "nothing here knows what a conference is" — its `includeOffense` hook is the
 * seam this fills. CLAUDE.md §3 names the conference filter as adapter-layer
 * rather than sport-agnostic core, which is the same boundary seen from the
 * other side.
 */

/** The minimum a team must carry to be scoped. Structural, not imported. */
export type OffenseScope = {
  conferenceName: string | null;
  conferenceIsDisplayed: boolean;
};

/**
 * True when this offense's players can appear on the board.
 *
 * `conference` is the reader's explicit filter when they have set one. With no
 * filter the answer is not "everyone" — it is the displayed conferences, which
 * is what the board itself falls back to. That asymmetry was the bug.
 *
 * An unknown team is out of scope rather than in it. A team with no conference
 * row that season (an FCS visitor) cannot be in a displayed conference, so
 * excluding it matches the board rather than guessing.
 */
export function offenseOnBoard(
  team: OffenseScope | undefined,
  conference: string | undefined,
): boolean {
  if (!team) return false;
  if (conference !== undefined) return team.conferenceName === conference;
  return team.conferenceIsDisplayed;
}
