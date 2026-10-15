import type { NextConfig } from "next";

import { BASE_PATH, SITE_HOST } from "./lib/core/site";

const nextConfig: NextConfig = {
  // The app lives under /football on the client's site; see lib/core/site.ts.
  basePath: BASE_PATH || undefined,

  /**
   * EVERY ADDRESS FROM BEFORE THE MOVE STILL WORKS. Anything that is not under
   * the base path is an old link (`/props?sport=nfl`, a shared game page) and
   * is sent to the same page on the client's site, query string included.
   *
   * NEVER A /football PATH. The client's site forwards /football/* to this
   * deployment (his vercel.json), so a rule that redirected /football/* on
   * this host would answer his forwarded request with a redirect, and the
   * reader's browser would loop between the two sites.
   */
  async redirects() {
    if (!BASE_PATH) return [];
    const segment = BASE_PATH.slice(1);
    const home = SITE_HOST ? `https://${SITE_HOST}${BASE_PATH}` : BASE_PATH;
    return [
      { source: "/", destination: home, basePath: false, permanent: false },
      {
        source: `/:path((?!${segment}(?:/|$)).+)`,
        destination: `${home}/:path`,
        basePath: false,
        permanent: false,
      },
    ];
  },
};

export default nextConfig;
