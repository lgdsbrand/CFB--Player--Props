import { createClient } from "@supabase/supabase-js";

import { supabaseAnonKey, supabaseUrl } from "@/lib/core/env";

/**
 * Supabase client for Server Components and Route Handlers.
 *
 * Uses the anon key. There are no write policies anywhere in the schema, so the
 * worst a leaked anon key permits is reading public sports data — writes happen
 * exclusively from the Python worker over a service-role/direct connection.
 *
 * The app never calls CFBD or any odds provider directly; it only ever reads
 * what the worker has already written (CLAUDE.md §2).
 */
export function createServerSupabaseClient() {
  return createClient(supabaseUrl(), supabaseAnonKey(), {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
    },
  });
}
