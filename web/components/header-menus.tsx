"use client";

import { useEffect, useRef, useState, type RefObject } from "react";

import {
  HIS_SPORTS,
  SOON_SPORTS,
  hisInfoHref,
  hisSiteHref,
  type HisSport,
} from "@/lib/core/client-site";
import type { Sport } from "@/lib/core/sport";

/**
 * The header's two dropdowns, copied from the client's site
 * (DropdownNavigation.jsx and the "More" menu in his App.jsx): the phone
 * hamburger with every sport and his About/Contact, and the desktop "More" with
 * the sports still to come. His classes, verbatim where they apply.
 */

/**
 * Open state that closes on Escape or a press anywhere outside `ref`.
 *
 * NOT A FULL-SCREEN BACKDROP, which is what his site uses. The header has a
 * `backdrop-filter`, and that makes it the containing block for `fixed`
 * descendants: an `inset-0` backdrop inside it covers the header only, so a tap
 * on the page below would not close the menu.
 */
function useClosable(ref: RefObject<HTMLElement | null>): [boolean, (open: boolean) => void] {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    const onPress = (event: PointerEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPress);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPress);
    };
  }, [open, ref]);
  return [open, setOpen];
}

function Badge({ status }: { status: HisSport["status"] }) {
  if (status === "BETA") {
    return (
      <span className="rounded-md border border-cyan-500/30 bg-cyan-950/50 px-1.5 py-0.5 text-[8px] font-black tracking-widest text-cyan-400">
        BETA
      </span>
    );
  }
  if (status === "COMING_SOON") {
    return (
      <span className="rounded-md border border-slate-800 bg-slate-900/50 px-1.5 py-0.5 text-[8px] font-black tracking-widest text-slate-500">
        SOON
      </span>
    );
  }
  return null;
}

/** The phone and tablet menu: his sports, then his About and Contact. */
export function HamburgerMenu({ sport }: { sport: Sport }) {
  const root = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useClosable(root);
  return (
    <div ref={root} className="relative flex items-center lg:hidden">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-label="Toggle navigation menu"
        className="flex cursor-pointer items-center justify-center rounded-xl border border-slate-800 bg-slate-900/40 p-2.5 text-slate-400 backdrop-blur-md transition-all duration-300 hover:border-slate-700 hover:bg-slate-800/80 hover:text-slate-200 focus:outline-none active:scale-95"
      >
        <div className="relative h-5 w-5">
          <span
            className={`absolute left-0 h-0.5 w-5 rounded-full bg-current transition-all duration-300 ${open ? "top-1/2 -translate-y-1/2 rotate-45" : "top-1"}`}
          />
          <span
            className={`absolute left-0 h-0.5 w-5 rounded-full bg-current transition-all duration-300 ${open ? "scale-0 opacity-0" : "top-1/2 -translate-y-1/2"}`}
          />
          <span
            className={`absolute left-0 h-0.5 w-5 rounded-full bg-current transition-all duration-300 ${open ? "top-1/2 -translate-y-1/2 -rotate-45" : "bottom-1"}`}
          />
        </div>
      </button>

      {open ? (
        <>
          <div className="absolute top-16 right-0 z-50 max-h-[80vh] w-72 overflow-y-auto rounded-2xl border border-slate-800 border-t-indigo-500/30 bg-slate-950 p-5 shadow-2xl shadow-black/60">
            <div className="space-y-1">
              <span className="mb-1 block px-2 text-[10px] font-black tracking-[0.2em] text-slate-500 uppercase">
                Sports Predictors
              </span>
              {HIS_SPORTS.map((s) => {
                const selected = s.football === sport;
                const soon = s.status === "COMING_SOON";
                const className =
                  "flex w-full items-center justify-between rounded-xl px-3 py-2.5 text-left text-xs font-bold tracking-wider uppercase transition-all duration-200 " +
                  (selected
                    ? "border border-indigo-500/30 border-t-indigo-400/20 bg-indigo-600/20 font-extrabold text-indigo-400 shadow-[0_0_15px_rgba(99,102,241,0.1)]"
                    : "border border-transparent text-slate-300 hover:bg-slate-900/50 hover:text-slate-100") +
                  (soon ? " opacity-50" : "");
                const body = (
                  <>
                    <span className="flex items-center gap-3">
                      <span className="text-base leading-none">{s.icon}</span>
                      <span>{s.name}</span>
                    </span>
                    <Badge status={s.status} />
                  </>
                );
                if (soon) {
                  return (
                    <span key={s.id} className={className} aria-disabled="true">
                      {body}
                    </span>
                  );
                }
                return (
                  <a key={s.id} href={hisSiteHref(s.id)} className={className}>
                    {body}
                  </a>
                );
              })}
            </div>

            <div className="my-4 border-t border-slate-900" />

            <div className="space-y-1">
              <span className="mb-1 block px-2 text-[10px] font-black tracking-[0.2em] text-slate-500 uppercase">
                Information
              </span>
              <a
                href={hisInfoHref("about")}
                className="flex w-full items-center gap-3 rounded-xl border border-transparent px-3 py-2 text-left text-xs font-bold tracking-wider text-slate-300 uppercase transition-all hover:bg-slate-900/50 hover:text-slate-100"
              >
                <span>ℹ️</span>
                <span>About Legends</span>
              </a>
              <a
                href={hisInfoHref("contact")}
                className="flex w-full items-center gap-3 rounded-xl border border-transparent px-3 py-2 text-left text-xs font-bold tracking-wider text-slate-300 uppercase transition-all hover:bg-slate-900/50 hover:text-slate-100"
              >
                <span>✉️</span>
                <span>Contact Us</span>
              </a>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

/** The desktop "More ▾": his sports still to come, as his site lists them. */
export function MoreMenu() {
  const root = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useClosable(root);
  return (
    <div ref={root} className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className={
          "flex cursor-pointer items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[10px] font-black tracking-widest uppercase transition-all duration-200 " +
          (open
            ? "border-slate-700 bg-slate-800/60 text-slate-200"
            : "border-transparent text-slate-400 hover:bg-slate-900/40 hover:text-slate-200")
        }
      >
        <span>More</span>
        <span className={`transition-transform duration-200 ${open ? "rotate-180" : ""}`}>▾</span>
      </button>
      {open ? (
        <>
          <div className="absolute top-[calc(100%+6px)] left-0 z-50 w-52 rounded-2xl border border-slate-800 bg-slate-950 p-2 shadow-2xl shadow-black/60">
            <span className="block px-2 py-1 text-[9px] font-black tracking-[0.2em] text-slate-600 uppercase">
              Coming Soon
            </span>
            {SOON_SPORTS.map((s) => (
              <span
                key={s.id}
                aria-disabled="true"
                className="flex w-full items-center justify-between rounded-xl px-3 py-2 text-left text-xs font-bold tracking-wider text-slate-500 uppercase"
              >
                <span className="flex items-center gap-2.5">
                  <span>{s.icon}</span>
                  <span>{s.name}</span>
                </span>
                <span className="rounded border border-slate-800 bg-slate-900 px-1.5 py-0.5 text-[7px] font-black tracking-widest text-slate-600">
                  SOON
                </span>
              </span>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}

export type ToolItem = { label: string; icon: string; href: string; active: boolean };

/**
 * "Tools" at the end of our tab row (client, 2026-10-15): the arb calculator,
 * the slip builder and the slip checker, which were three tabs. Outside the
 * sideways-scrolling tab row, or the open menu would be clipped by it. Hrefs
 * arrive already carrying the sport; the button reads as active on a tool page.
 */
export function ToolsMenu({ items }: { items: ToolItem[] }) {
  const root = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useClosable(root);
  const onTool = items.some((item) => item.active);
  return (
    <div ref={root} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-haspopup="menu"
        className={
          "flex cursor-pointer items-center gap-1 rounded-lg border px-3 py-1.5 text-[10px] font-black tracking-wider uppercase transition-all duration-200 md:px-4 md:tracking-widest " +
          (onTool
            ? "border-blue-500/30 bg-blue-600/20 text-blue-400 shadow-[0_0_12px_rgba(59,130,246,0.15)]"
            : open
              ? "border-slate-700 bg-slate-800/60 text-slate-200"
              : "border-transparent text-slate-400 hover:bg-slate-900/40 hover:text-slate-200")
        }
      >
        <span>🧰</span>
        <span>Tools</span>
        <span className={`transition-transform duration-200 ${open ? "rotate-180" : ""}`}>▾</span>
      </button>
      {open ? (
        <div
          role="menu"
          className="absolute top-[calc(100%+6px)] right-0 z-50 w-52 rounded-2xl border border-slate-800 bg-slate-950 p-2 shadow-2xl shadow-black/60"
        >
          {items.map((item) => (
            <a
              key={item.href}
              href={item.href}
              role="menuitem"
              aria-current={item.active ? "page" : undefined}
              className={
                "flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-xs font-bold tracking-wider uppercase transition-all " +
                (item.active
                  ? "bg-blue-600/20 text-blue-400"
                  : "text-slate-300 hover:bg-slate-900 hover:text-white")
              }
            >
              <span>{item.icon}</span>
              <span>{item.label}</span>
            </a>
          ))}
        </div>
      ) : null}
    </div>
  );
}
