"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Share a graphic (client, 2026-10-12): opens a preview of the PNG with the
 * ways out of it — the phone's own share sheet, a download, the link.
 *
 * WHY A PREVIEW AND NOT STRAIGHT TO THE SHARE SHEET. `navigator.share` must be
 * called inside the tap that asked for it, and Safari drops that permission
 * across the wait for the image to render. So the first tap fetches and shows
 * the graphic (which the client asked to see anyway), and the Share button in
 * the preview is a fresh tap with the file already in hand.
 *
 * `navigator.share` with a file exists on phones and on some desktops; where
 * it does not, the button is not shown and Download is the way out.
 */
export function ShareButton({
  imagePath,
  pagePath,
  fileName,
  title,
  compact = false,
}: {
  /** Same-origin path of the PNG route. */
  imagePath: string;
  /** Same-origin path of the page the graphic describes, for Copy link. */
  pagePath: string;
  fileName: string;
  title: string;
  /** An icon-sized button for a card, rather than the labelled one. */
  compact?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [copied, setCopied] = useState(false);
  const [canShareFile, setCanShareFile] = useState(false);

  // Release the preview's blob when it is replaced or the button unmounts.
  useEffect(() => () => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
  }, [objectUrl]);

  const open = async (event: React.MouseEvent) => {
    // On a game card the button sits beside the card's link; never follow it.
    event.preventDefault();
    event.stopPropagation();
    dialog.current?.showModal();
    setCopied(false);
    if (state === "ready" || state === "loading") return;
    setState("loading");
    try {
      const response = await fetch(imagePath);
      if (!response.ok) throw new Error(`image ${response.status}`);
      const blob = await response.blob();
      const next = new File([blob], fileName, { type: "image/png" });
      setFile(next);
      setObjectUrl(URL.createObjectURL(blob));
      setCanShareFile(typeof navigator.canShare === "function" && navigator.canShare({ files: [next] }));
      setState("ready");
    } catch (error) {
      console.error("Share image failed", error);
      setState("error");
    }
  };

  const pageUrl = () => new URL(pagePath, window.location.origin).toString();

  const share = async () => {
    if (!file) return;
    try {
      await navigator.share({ files: [file], title, text: `${title} ${pageUrl()}` });
    } catch (error) {
      // Closing the share sheet rejects with AbortError; that is not a fault.
      if ((error as Error).name !== "AbortError") console.error("Share failed", error);
    }
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(pageUrl());
      setCopied(true);
    } catch (error) {
      console.error("Copy link failed", error);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={open}
        title={`Share: ${title}`}
        aria-label={`Share: ${title}`}
        className={
          compact
            ? "border-border-subtle text-muted hover:border-border-strong hover:text-ink rounded-lg border px-2 py-1 text-xs font-bold transition-colors"
            : "border-border-subtle text-ink hover:border-accent-cyan/60 w-fit rounded-lg border px-3 py-2 text-xs font-extrabold tracking-[0.12em] uppercase transition-colors"
        }
      >
        Share ↗
      </button>

      <dialog
        ref={dialog}
        onClick={(event) => {
          // A click on the backdrop lands on the dialog itself: close.
          if (event.target === dialog.current) dialog.current?.close();
        }}
        className="bg-panel border-border-subtle text-ink m-auto max-h-[92dvh] w-[min(92vw,26rem)] rounded-2xl border p-0 backdrop:bg-black/70"
      >
        <div className="flex flex-col gap-3 p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="section-header">Share</span>
            <button
              type="button"
              onClick={() => dialog.current?.close()}
              className="text-muted hover:text-ink text-sm font-bold"
              aria-label="Close"
            >
              ✕
            </button>
          </div>

          <div className="bg-panel-inset border-border-subtle flex aspect-[4/5] items-center justify-center overflow-hidden rounded-xl border">
            {state === "ready" && objectUrl ? (
              // A blob URL, not a remote image: next/image has nothing to optimise.
              // eslint-disable-next-line @next/next/no-img-element
              <img src={objectUrl} alt={title} className="h-full w-full object-contain" />
            ) : state === "error" ? (
              <p className="text-muted px-6 text-center text-xs">
                The graphic could not be made just now. Try again in a minute.
              </p>
            ) : (
              <p className="text-muted text-xs">Making the graphic…</p>
            )}
          </div>

          <div className="flex flex-wrap gap-2">
            {canShareFile ? (
              <button type="button" onClick={share} disabled={state !== "ready"} className="cta flex-1 px-3 py-2">
                Share →
              </button>
            ) : null}
            {state === "ready" && objectUrl ? (
              <a
                href={objectUrl}
                download={fileName}
                className={(canShareFile ? "border-border-subtle text-ink border " : "cta ") + "flex-1 rounded-lg px-3 py-2 text-center text-xs font-extrabold tracking-[0.12em] uppercase"}
              >
                Download
              </a>
            ) : null}
            <button
              type="button"
              onClick={copy}
              className="border-border-subtle text-ink hover:border-border-strong flex-1 rounded-lg border px-3 py-2 text-xs font-extrabold tracking-[0.12em] uppercase"
            >
              {copied ? "Link copied ✓" : "Copy link"}
            </button>
          </div>
        </div>
      </dialog>
    </>
  );
}
