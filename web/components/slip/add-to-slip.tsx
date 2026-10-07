"use client";

import { legKey, legTitle, type SlipLeg } from "@/lib/core/slip";
import { slip, useSlip } from "@/components/slip/slip-store";

/**
 * Puts one bet on the bet slip, or takes it off again.
 *
 * Two looks: the small "+ Slip" pill beside a row, and `asPrice`, where the
 * price a reader is looking at IS the button (the game page's book table).
 */
export function AddToSlip({
  leg,
  children,
  asPrice = false,
}: {
  leg: SlipLeg;
  children?: React.ReactNode;
  asPrice?: boolean;
}) {
  const { legs } = useSlip();
  const key = legKey(leg);
  const inSlip = legs.some((l) => legKey(l) === key);
  const title = `${inSlip ? "Remove from" : "Add to"} bet slip: ${legTitle(leg)}`;
  const toggle = () => (inSlip ? slip.remove(key) : slip.add(leg));

  if (asPrice) {
    return (
      <button
        type="button"
        onClick={toggle}
        title={title}
        aria-pressed={inSlip}
        className={
          "-mx-1 rounded px-1 tabular-nums transition-colors " +
          (inSlip
            ? "bg-accent-cyan/15 text-accent-cyan"
            : "hover:bg-accent-indigo/15 hover:text-accent-cyan")
        }
      >
        {children}
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={toggle}
      title={title}
      aria-label={title}
      aria-pressed={inSlip}
      className={
        "pill shrink-0 border transition-colors " +
        (inSlip
          ? "border-accent-cyan/50 bg-accent-cyan/15 text-accent-cyan"
          : "border-border-subtle text-muted hover:border-border-strong hover:text-ink")
      }
    >
      {inSlip ? "✓ Slip" : "+ Slip"}
    </button>
  );
}
