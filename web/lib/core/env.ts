/**
 * Environment access for the web app.
 *
 * The Next.js app is READ-ONLY against Supabase and holds exactly two values:
 * the project URL and the anon (publishable) key. The service role key must
 * never appear in this app — it lives only in the Python worker's environment.
 * CLAUDE.md §0 treats this repo as public-adjacent, so that boundary is a hard
 * rule rather than a convention.
 */

function required(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(
      `Missing environment variable ${name}. Copy .env.example to web/.env.local and fill it in.`,
    );
  }
  return value;
}

export function supabaseUrl(): string {
  return required(
    "NEXT_PUBLIC_SUPABASE_URL",
    process.env.NEXT_PUBLIC_SUPABASE_URL,
  );
}

export function supabaseAnonKey(): string {
  return required(
    "NEXT_PUBLIC_SUPABASE_ANON_KEY",
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  );
}

/** True when both Supabase values are present, for graceful degradation. */
export function isSupabaseConfigured(): boolean {
  return Boolean(
    process.env.NEXT_PUBLIC_SUPABASE_URL &&
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  );
}
