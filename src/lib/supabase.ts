import { createClient } from "@supabase/supabase-js";
import type { SupabaseClient } from "@supabase/supabase-js";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
let client: SupabaseClient | null | undefined;

export function getSupabaseClient(): SupabaseClient | null {
	if (client !== undefined) {
		return client;
	}

	if (!supabaseUrl || !supabaseAnonKey) {
		client = null;
		return client;
	}

	client = createClient(supabaseUrl, supabaseAnonKey);
	return client;
}
