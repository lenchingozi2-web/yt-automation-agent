import type { SupabaseClient } from "@supabase/supabase-js";
import { StudioRequestError } from "@/src/lib/studio-server";
import type { BriefingEvidence } from "@/src/lib/studio-research";

export type ResearchIdea = BriefingEvidence & {
  id: string;
  briefing_date: string;
  topic_area: "health" | "education" | "intelligence" | null;
  status: "pending" | "queued";
};

const columns = "id,briefing_date,title,hook_summary,references,status,topic_area";
function checkStorage(error: { code: string; message: string } | null) {
  if (!error) return;
  if (["PGRST205", "42P01", "42501"].includes(error.code)) {
    throw new StudioRequestError("Daily Briefing storage is not available yet.", 503);
  }
  throw error;
}

export async function getBriefing(client: SupabaseClient): Promise<ResearchIdea[]> {
  const { data, error } = await client.from("research_ideas").select(columns)
    .order("briefing_date", { ascending: false }).order("position").limit(3);
  checkStorage(error);
  const ideas = (data ?? []) as ResearchIdea[];
  const pending = ideas.filter((idea) => idea.status === "pending").map((idea) => idea.id);
  if (!pending.length) return ideas;
  const { data: queued, error: jobError } = await client.from("videos").select("id").in("id", pending);
  if (jobError) throw jobError;
  const jobs = new Set(queued?.map((job) => job.id));
  return ideas.map((idea) => jobs.has(idea.id) ? { ...idea, status: "queued" } : idea);
}

export async function getResearchIdea(client: SupabaseClient, id: string): Promise<ResearchIdea> {
  const { data, error } = await client.from("research_ideas").select(columns).eq("id", id).maybeSingle();
  checkStorage(error);
  if (!data) throw new StudioRequestError("This briefing story has rotated out. Refresh the briefing to see the current stories.", 404);
  return data as ResearchIdea;
}

export async function markBriefingQueued(client: SupabaseClient, id: string) {
  const { error } = await client.from("research_ideas").update({ status: "queued" }).eq("id", id);
  if (error) console.error("Studio could not mark the briefing story as queued", error.code);
}
