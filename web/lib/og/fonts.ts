/**
 * Inter for the share images, the site's own face (app/layout.tsx).
 *
 * `next/og` cannot use `next/font`, and it reads TTF/OTF/WOFF but not WOFF2.
 * Google Fonts serves TTF to a request with no browser User-Agent, so the CSS
 * is fetched plainly and its one `src` url followed. Each weight is fetched
 * once per server instance and kept.
 *
 * A failed fetch is not a failed image: the weight is left out and `next/og`
 * falls back to its bundled sans. The graphic then looks slightly off-brand,
 * which beats a broken share button.
 */

type OgFont = { name: string; data: ArrayBuffer; weight: 400 | 700 | 800; style: "normal" };

const WEIGHTS = [400, 700, 800] as const;
const cache = new Map<number, Promise<ArrayBuffer | null>>();

function load(weight: number): Promise<ArrayBuffer | null> {
  let pending = cache.get(weight);
  if (!pending) {
    pending = (async () => {
      try {
        const css = await fetch(`https://fonts.googleapis.com/css2?family=Inter:wght@${weight}`).then(
          (r) => (r.ok ? r.text() : Promise.reject(new Error(`css ${r.status}`))),
        );
        const url = css.match(/src: url\((.+?)\) format\('(?:opentype|truetype|woff)'\)/)?.[1];
        if (!url) throw new Error("no TTF/WOFF source in the font CSS");
        const font = await fetch(url);
        if (!font.ok) throw new Error(`font ${font.status}`);
        return await font.arrayBuffer();
      } catch (error) {
        console.error(`Inter ${weight} for share images failed; using the default sans.`, error);
        cache.delete(weight); // try again on the next image
        return null;
      }
    })();
    cache.set(weight, pending);
  }
  return pending;
}

export async function shareFonts(): Promise<OgFont[]> {
  const data = await Promise.all(WEIGHTS.map(load));
  return WEIGHTS.flatMap((weight, i) =>
    data[i] ? [{ name: "Inter", data: data[i] as ArrayBuffer, weight, style: "normal" as const }] : [],
  );
}
