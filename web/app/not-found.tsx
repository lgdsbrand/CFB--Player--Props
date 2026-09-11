import { Suspense } from "react";

import { NotFoundView } from "@/components/not-found-view";
import { SiteHeader } from "@/components/site-header";

/**
 * Every 404 in the app, including `notFound()` from a game or player page.
 *
 * `Suspense` because the view reads the search params, and this route is
 * prerendered: without a boundary the build bails the whole page out of static
 * rendering. The fallback is the plain header, so nothing jumps when it resolves.
 */
export default function NotFound() {
  return (
    <Suspense fallback={<SiteHeader />}>
      <NotFoundView />
    </Suspense>
  );
}
