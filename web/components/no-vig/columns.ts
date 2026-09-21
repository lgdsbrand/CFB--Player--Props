/**
 * The no-vig table's nine columns and the explanation each one carries.
 *
 * ONE ARRAY, TWO SURFACES. The table prints these as its headers with `help`
 * as a hover; the expandable explainer above it prints the same `help` as a
 * glossary. Both read this file, because a tooltip and a glossary that
 * disagree about what "vs Mkt" means are worse than having only one of them.
 *
 * It lives in its own module rather than in either component so neither has to
 * import from the other — the table is not a consumer of the explainer, and the
 * explainer is not the owner of the table's shape.
 *
 * WRITE `help` AS A SENTENCE A BETTOR WOULD SAY. These strings are the only
 * user-facing definitions on the page, and the client asked for them in those
 * words ("explain what to look for"). No jargon that the sentence does not
 * itself unpack, and nothing that reads as advice: this page describes prices,
 * it does not recommend them.
 */

export type NoVigColumn = {
  /** Header text, exactly as the table prints it. */
  label: string;
  /** One sentence, plain language. Used as the `title` AND the glossary body. */
  help: string;
  /** Right-aligned columns are the numeric ones. */
  align: "left" | "right";
};

/**
 * In table order.
 *
 * THE MEASURED NUMBERS IN THE HOLD ENTRY ARE NOT DECORATION. They come from
 * `holdBand` in `lib/core/no-vig.ts`, which set its boundaries from every
 * two-way quote stored for 2026 week 1: the hold ran 4.75% to 7.94% with a
 * 6.59% mean. If those bands are ever re-fitted, this sentence moves with them.
 */
export const NO_VIG_COLUMNS: readonly NoVigColumn[] = [
  {
    label: "Player",
    help: "Who the prop is on, with his position and this week’s opponent. Click the name to open his game log and splits.",
    align: "left",
  },
  {
    label: "Market",
    help: "Which prop this price is for.",
    align: "left",
  },
  {
    label: "Line",
    help: "The number the book posted. A ×2 marker means the books do not agree on the number itself, and prices are only ever compared against other books at the same one.",
    align: "right",
  },
  {
    label: "Book",
    help: "The sportsbook posting this price.",
    align: "left",
  },
  {
    label: "Posted",
    help: "The price as the book has it, over on top and under below. A star marks the best price on that side among the books at this same line.",
    align: "right",
  },
  {
    label: "Fair",
    help: "The same price with the book’s margin taken out, so it is what the quote would be if the book charged nothing to take the bet.",
    align: "right",
  },
  {
    label: "Hold",
    help: "The book’s cut. Under 5% is keen, 5 to 7% is typical, 7 to 9% is dear, and 9% or more is worse than anything measured on a full week.",
    align: "right",
  },
  {
    label: "Fair %",
    help: "What the fair price works out to as a probability, over then under. The two add up to 100%, which is what having no margin means.",
    align: "right",
  },
  {
    label: "vs Mkt",
    help: "How far this book’s fair over probability sits from the average of the other books at the same line, in probability points. A dash means it is the only book posting that line.",
    align: "right",
  },
];
