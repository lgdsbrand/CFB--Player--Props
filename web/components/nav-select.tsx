"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";

/** One choice in the dropdown, with the address it leads to. */
export type NavOption = { value: string; label: string; href: string };

/**
 * A dropdown whose every option is a URL — a pill group too long for pills.
 *
 * IT FILTERS NOTHING ITSELF. Like the board's `FilterFields`, it only writes
 * the address the server already knows how to read, and every option's href
 * is built on the server by the same function that builds the page's pills.
 * So a choice here and a pill click can never mean different things.
 *
 * Before hydration it is a plain GET form: the other filters ride along as
 * hidden fields and a `<noscript>` button submits it, so the control still
 * works when the script has not arrived.
 *
 * The value is held locally and re-adopted from the URL. A select controlled
 * straight from the prop would snap back to the old choice for the length of
 * the navigation, which reads as the choice being refused.
 */
export function NavSelect({
  label,
  name,
  value,
  options,
  action,
  hidden,
}: {
  label: string;
  name: string;
  /** The current choice; "" is the unfiltered option. */
  value: string;
  options: NavOption[];
  /** Where the no-script form submits. */
  action: string;
  /** Every other parameter on the page, so a no-script submit keeps them. */
  hidden: { name: string; value: string }[];
}) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [chosen, setChosen] = useState(value);
  // Adopt the URL's value when it changes from elsewhere (a pill, the back
  // button). Adjusted during render, React's pattern for state that follows a
  // prop, rather than in an effect that would paint the stale value first.
  const [adopted, setAdopted] = useState(value);
  if (adopted !== value) {
    setAdopted(value);
    setChosen(value);
  }

  return (
    <form method="get" action={action} className="flex items-center gap-2">
      {hidden.map((field) => (
        <input key={field.name} type="hidden" name={field.name} value={field.value} />
      ))}
      <label className="flex items-center gap-2">
        <span className="label-caption">{label}</span>
        <select
          name={name}
          value={chosen}
          aria-busy={isPending}
          onChange={(event) => {
            const option = options.find((o) => o.value === event.target.value);
            if (!option) return;
            setChosen(option.value);
            startTransition(() => router.push(option.href, { scroll: false }));
          }}
          className={
            "bg-panel-inset border-border-subtle text-ink focus:border-accent-cyan/60 rounded-lg border px-2.5 py-1.5 text-sm outline-none" +
            (isPending ? " opacity-60" : "")
          }
        >
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      <noscript>
        <button
          type="submit"
          className="border-border-subtle text-muted rounded-full border px-3 py-1 text-[0.625rem] font-bold uppercase tracking-label"
        >
          Go
        </button>
      </noscript>
    </form>
  );
}
