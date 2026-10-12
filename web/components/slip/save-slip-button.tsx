"use client";

import Link from "next/link";

import { findSaved } from "@/lib/core/slip-track";
import type { SlipLeg } from "@/lib/core/slip";
import { savedSlips, useSavedSlips } from "@/components/slip/saved-slips-store";

/**
 * "Save slip": keeps these legs, the book and its prices in this browser's
 * list of saved slips, tracked on the My slips tab. Once saved, the button
 * becomes the way there.
 */
export function SaveSlipButton({
  legs,
  bookKey,
  bookName,
  prices,
  price,
  savedHref,
  compact = false,
}: {
  legs: SlipLeg[];
  bookKey: string | null;
  bookName: string | null;
  prices: (number | null)[];
  price: number | null;
  /** The My slips tab, for the league being viewed. */
  savedHref: string;
  compact?: boolean;
}) {
  const saved = useSavedSlips();
  const already = legs.length > 0 && findSaved(saved, legs, bookKey) !== undefined;
  const base = compact
    ? "rounded-lg border px-2 py-1 text-xs font-bold transition-colors"
    : "w-fit rounded-lg border px-3 py-2 text-xs font-extrabold tracking-[0.12em] uppercase transition-colors";

  if (already) {
    return (
      <Link href={savedHref} className={`${base} border-positive/50 text-positive hover:text-ink`}>
        ✓ Saved · My slips →
      </Link>
    );
  }
  return (
    <button
      type="button"
      disabled={legs.length === 0}
      onClick={() => savedSlips.save({ legs, bookKey, bookName, prices, price })}
      title="Keep this slip in this browser and track it on My slips"
      className={`${base} border-border-subtle hover:border-accent-cyan/60 ${compact ? "text-muted hover:text-ink" : "text-ink"} disabled:opacity-40`}
    >
      Save slip
    </button>
  );
}
