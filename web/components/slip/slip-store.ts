"use client";

import { useSyncExternalStore } from "react";

import { addLeg, legKey, parseLegs, type SlipLeg } from "@/lib/core/slip";

/**
 * The bet slip's state: its legs and the reader's state (for the BetMGM and
 * BetRivers links), kept in this browser's localStorage and nowhere else. No
 * account, nothing sent to the server: a cleared browser is an empty slip.
 *
 * An external store rather than React state, so every "Add" button on a page
 * and the drawer read one copy, and a change in another tab arrives through
 * the `storage` event. The server snapshot is always empty, so the first
 * render matches the server's and the stored slip appears right after.
 */

interface SlipSnapshot {
  legs: SlipLeg[];
  state: string | null;
}

const LEGS_KEY = "legends.slip.v1";
const STATE_KEY = "legends.slip.state";
const EMPTY: SlipSnapshot = { legs: [], state: null };
export const OPEN_EVENT = "legends:slip-open";

let snapshot: SlipSnapshot | null = null;
const listeners = new Set<() => void>();

function read(): SlipSnapshot {
  try {
    const legs = parseLegs(JSON.parse(window.localStorage.getItem(LEGS_KEY) ?? "[]"));
    const state = window.localStorage.getItem(STATE_KEY);
    return { legs, state: state && /^[A-Z]{2}$/.test(state) ? state : null };
  } catch {
    return EMPTY;
  }
}

function current(): SlipSnapshot {
  if (snapshot === null) snapshot = read();
  return snapshot;
}

function write(next: SlipSnapshot) {
  snapshot = next;
  try {
    window.localStorage.setItem(LEGS_KEY, JSON.stringify(next.legs));
    if (next.state) window.localStorage.setItem(STATE_KEY, next.state);
    else window.localStorage.removeItem(STATE_KEY);
  } catch {
    // Private mode or blocked storage: the slip still works for this page.
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key !== LEGS_KEY && event.key !== STATE_KEY) return;
    snapshot = read();
    listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

export const slip = {
  add(leg: SlipLeg) {
    write({ ...current(), legs: addLeg(current().legs, leg) });
  },
  remove(key: string) {
    write({ ...current(), legs: current().legs.filter((l) => legKey(l) !== key) });
  },
  clear() {
    write({ ...current(), legs: [] });
  },
  /** Swap the whole slip for these legs: the bet builder's "Use this slip". */
  replace(legs: SlipLeg[]) {
    write({ ...current(), legs: legs.reduce<SlipLeg[]>(addLeg, []) });
  },
  /** Ask the drawer to open; `SlipDock` listens. */
  open() {
    window.dispatchEvent(new Event(OPEN_EVENT));
  },
  setState(state: string | null) {
    write({ ...current(), state });
  },
};

export function useSlip(): SlipSnapshot {
  return useSyncExternalStore(subscribe, current, () => EMPTY);
}
