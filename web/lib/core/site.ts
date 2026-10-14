/**
 * Where this app is served from (the move onto the client's site, 2026-10-14).
 *
 * The football app moves under `/football` on the client's own site
 * (lgdsanalytics.vercel.app), which forwards that path here with two Vercel
 * rewrites in his repo. Next.js prefixes `Link`, the router, `redirect()` and
 * static assets itself once `basePath` is set (next.config.ts reads
 * `BASE_PATH` from here). It does NOT prefix a URL built by hand: a `fetch` of
 * one of our own routes, a page URL built for sharing, a metadata image. Every
 * one of those goes through `withBasePath`, and `base-path.test.ts` refuses a
 * new one that does not.
 *
 * A CODE CONSTANT, NOT A DASHBOARD SETTING. The client's Vercel is a Hobby
 * account with no way to add a collaborator, so nothing here can depend on an
 * environment variable set in his dashboard. Live since 2026-10-14 (go-live);
 * the environment variable can still override it, e.g. NEXT_PUBLIC_BASE_PATH=""
 * to run the app at the root locally.
 */

export const BASE_PATH: string = process.env.NEXT_PUBLIC_BASE_PATH ?? "/football";

/** A root-relative path to one of our own routes, prefixed with the base path. */
export function withBasePath(path: string): string {
  if (!BASE_PATH || !path.startsWith("/") || path.startsWith("//")) return path;
  // Already prefixed: the base path itself, then "/", "?" or nothing.
  if (path.startsWith(BASE_PATH) && ["", "/", "?"].includes(path.charAt(BASE_PATH.length))) {
    return path;
  }
  return `${BASE_PATH}${path}`;
}

/**
 * The address the share graphics print. Behind the client's site the request
 * arrives at THIS deployment's own host, so reading it off the request would
 * print cfb-player-props.vercel.app on an image shared from his site. Set to
 * an empty value to fall back to the request's host.
 */
export const SITE_HOST: string | null =
  process.env.NEXT_PUBLIC_SITE_HOST ?? "lgdsanalytics.vercel.app";

/** The host a share graphic should name, given the request it was asked for on. */
export function shareSiteHost(requestHost: string): string {
  return SITE_HOST || requestHost;
}
