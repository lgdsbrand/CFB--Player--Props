"use client";

import { useSyncExternalStore } from "react";

import type { SlipLeg } from "@/lib/core/slip";
import { addSaved, parseSavedSlips, type LegTrack, type SavedSlip } from "@/lib/core/slip-track";

/**
 * Saved slips (client, 2026-10-12), kept in this browser's localStorage and
 * nowhere else, as the bet slip is (`slip-store.ts`): no account, a cleared
 * browser is an empty list. Same external-store shape, so the Save buttons
 * and the My slips tab read one copy and another tab's change arrives
 * through the `storage` event.
 */

const SAVED_KEY = "legends.savedslips.v1";
const EMPTY: SavedSlip[] = [];

let snapshot: SavedSlip[] | null = null;
const listeners = new Set<() => void>();

function read(): SavedSlip[] {
  try {
    return parseSavedSlips(JSON.parse(window.localStorage.getItem(SAVED_KEY) ?? "[]"));
  } catch {
    return EMPTY;
  }
}

function current(): SavedSlip[] {
  if (snapshot === null) snapshot = read();
  return snapshot;
}

function write(next: SavedSlip[]) {
  snapshot = next;
  try {
    window.localStorage.setItem(SAVED_KEY, JSON.stringify(next));
  } catch {
    // Private mode or blocked storage: kept for this page only.
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key !== SAVED_KEY) return;
    snapshot = read();
    listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

function newId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  }
}

export const savedSlips = {
  save(input: {
    legs: SlipLeg[];
    bookKey: string | null;
    bookName: string | null;
    prices: (number | null)[];
    price: number | null;
  }) {
    write(addSaved(current(), { ...input, id: newId(), savedAt: new Date().toISOString(), settled: {} }));
  },
  remove(id: string) {
    write(current().filter((s) => s.id !== id));
  },
  /** Keep decided legs, so a finished slip is never looked up again. */
  settle(decided: Record<string, Record<string, LegTrack>>) {
    let changed = false;
    const next = current().map((s) => {
      const add = decided[s.id];
      if (!add || Object.keys(add).every((k) => s.settled[k])) return s;
      changed = true;
      return { ...s, settled: { ...s.settled, ...add } };
    });
    if (changed) write(next);
  },
};

export function useSavedSlips(): SavedSlip[] {
  return useSyncExternalStore(subscribe, current, () => EMPTY);
}
