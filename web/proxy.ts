import { type NextRequest, NextResponse } from "next/server";

import { SITE_HOST } from "./lib/core/site";

/**
 * Crawlers get the canonical page; filtered URLs are marked not-for-indexing.
 *
 * WHY (2026-10-24). Every board filter is a link, and every combination of them
 * is a URL that renders on the server with database reads. Production saw 598
 * distinct board queries in 26 minutes at one in the morning, Eastern — a
 * crawler walking the filter pills, not readers — and each distinct query was
 * memory PostgREST kept (see `lib/data/board.ts`). The board no longer
 * multiplies statements, but a crawler walking thousands of filter URLs is
 * still thousands of renders for pages nobody will open.
 *
 * So, for any URL carrying more than the canonical parameters (sport, season,
 * week):
 *   - a self-identified crawler is redirected to the canonical URL, and never
 *     renders the filtered one;
 *   - everyone else is served it with `X-Robots-Tag: noindex, nofollow`, so a
 *     crawler that does not say what it is still stops following from there.
 *
 * THE REDIRECT NAMES THE CLIENT'S HOST (`SITE_HOST`). Readers reach this app
 * through his domain, which forwards `/football/*` here, so a URL built from
 * this request would name this deployment and move the crawler off his site. A
 * relative Location would be right too, but Next's proxy layer rejects one
 * ("Invalid URL", measured) — it parses the header as an absolute URL.
 *
 * `robots.txt` cannot do this job from here: the app lives under `/football`,
 * and crawlers only read the file at the root of the client's domain.
 */

const CANONICAL_PARAMS = new Set(["sport", "season", "week"]);

/**
 * User agents that say they are crawlers. Social-preview fetchers are listed
 * too: they only need the canonical page, and the share images (which carry
 * their own parameters) are excluded by the matcher, not here.
 */
const CRAWLER =
  /bot\b|bot\/|crawler|crawl|spider|slurp|bingpreview|facebookexternalhit|meta-externalagent|gptbot|chatgpt|oai-searchbot|claudebot|claude-web|anthropic-ai|perplexity|bytespider|amazonbot|petalbot|applebot|yandex|baiduspider|ahrefs|semrush|mj12|dotbot|dataforseo|ccbot|google-extended|imagesift|timpibot|seznam|scrapy/i;

export function proxy(request: NextRequest) {
  const { nextUrl } = request;
  const extra = [...nextUrl.searchParams.keys()].some((key) => !CANONICAL_PARAMS.has(key));
  if (!extra) return NextResponse.next();

  if (CRAWLER.test(request.headers.get("user-agent") ?? "")) {
    const canonical = new URLSearchParams();
    for (const key of CANONICAL_PARAMS) {
      const value = nextUrl.searchParams.get(key);
      if (value !== null) canonical.set(key, value);
    }
    const query = canonical.toString();
    const target = new URL(
      `${nextUrl.basePath}${nextUrl.pathname}`,
      SITE_HOST ? `https://${SITE_HOST}` : nextUrl.origin,
    );
    target.search = query;
    return NextResponse.redirect(target, 308);
  }

  const response = NextResponse.next();
  response.headers.set("X-Robots-Tag", "noindex, nofollow");
  return response;
}

export const config = {
  // Pages only: not the API, Next's own assets, files, or the share images
  // (link previews fetch those with their parameters and must get the image).
  matcher: ["/((?!api/|_next/|.*share-image|.*opengraph|slip-check/image|.*\\.[a-z0-9]+$).*)"],
};
