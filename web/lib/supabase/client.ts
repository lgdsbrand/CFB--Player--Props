"use client";

import { createClient } from "@supabase/supabase-js";

import { supabaseAnonKey, supabaseUrl } from "@/lib/core/env";

let browserClient: ReturnType<typeof createClient> | null = null;

/**
 * Supabase client for Client Components (filters, search, live board updates).
 *
 * Memoised so interactive filtering does not open a new connection per render.
 * Anon key only — see lib/supabase/server.ts for why that is safe here.
 */
export function getBrowserSupabaseClient() {
  if (!browserClient) {
    browserClient = createClient(supabaseUrl(), supabaseAnonKey(), {
      auth: {
        persistSession: false,
        autoRefreshToken: false,
      },
    });
  }
  return browserClient;
}
