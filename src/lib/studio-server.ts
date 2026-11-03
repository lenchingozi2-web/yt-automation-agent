import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { normalizeDraft, toSafeMessage, type Draft } from "@/src/lib/studio";

export class StudioAuthError extends Error { constructor(message = "Please sign in to use Studio.") { super(message); } }
const url = () => process.env.NEXT_PUBLIC_SUPABASE_URL || process.env.SUPABASE_URL || "";
const anon = () => process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "";
const role = () => process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_SECRET_KEY || "";
export async function studioClient(request: Request): Promise<SupabaseClient> {
  const auth = request.headers.get("authorization") || "";
  const jwt = /^Bearer (.+)$/i.exec(auth)?.[1];
  if (!jwt) throw new StudioAuthError();
  if (!url() || !anon()) throw new Error("Studio Supabase URL or anon key is missing.");
  const userClient = createClient(url(), anon(), { auth: { autoRefreshToken: false, persistSession: false },
    global: { headers: { Authorization: "Bearer " + jwt } } });
  const { data, error } = await userClient.auth.getUser(jwt);
  if (error || !data.user) throw new StudioAuthError("Your session has expired. Please sign in again.");
  if (!role()) return userClient;
  return createClient(url(), role(), { auth: { autoRefreshToken: false, persistSession: false } });
}
export function statusFor(error: unknown) { return error instanceof StudioAuthError ? 401 : 500; }
export function errorResponse(error: unknown) { return { ok: false, error: toSafeMessage(error, "The request failed.") }; }
const missingColumn = (error: unknown) => {
  const x = error as { code?: string } | null;
  return x?.code === "PGRST204" || /column .* does not exist|could not find the .* column|schema cache/i.test(toSafeMessage(error, ""));
};
function scriptPayload(draft: Draft) {
  return { ...draft, aspect_ratio: draft.format, duration: draft.duration_seconds, metadata: {
    aspect_ratio: draft.format, duration: draft.duration_seconds, description: draft.description,
    tags: draft.tags, scenes: draft.scenes, video_url: "", youtube_url: "", error_message: "" } };
}
export async function safeInsertVideo(client: SupabaseClient, approved: unknown) {
  const draft = normalizeDraft(approved);
  if (!draft.title.trim() || draft.scenes.length < 1 || draft.scenes.some(s => !s.narration.trim())) throw new Error("A title and narration for every scene are required.");
  if (draft.scenes.length > 60) throw new Error("Maximum 60 scenes per video.");
  const packed = scriptPayload(draft);
  const candidates: Record<string, unknown>[] = [
    { title: draft.title, status: "pending", description: draft.description, tags: draft.tags, format: draft.format, duration_seconds: draft.duration_seconds, scenes: draft.scenes },
    { title: draft.title, status: "pending", script: packed },
    { title: draft.title, status: "pending", scenes: packed },
    { title: draft.title, status: "pending", scenes: draft.scenes },
    { title: draft.title, status: "pending", script: JSON.stringify(packed) },
  ];
  let lastError: unknown;
  for (const payload of candidates) {
    const { data, error } = await client.from("videos").insert(payload).select("*").single();
    if (!error && data) return data;
    lastError = error;
    if (!missingColumn(error) && !/invalid input syntax for type|json|expected array|type mismatch/i.test(toSafeMessage(error))) break;
  }
  throw new Error("Could not save approved video: " + toSafeMessage(lastError, "Check the videos table schema and RLS policies."));
}
export async function dispatchStudioWorker() {
  const token = process.env.GITHUB_TOKEN || process.env.GH_PAT || process.env.GH_TOKEN;
  if (!token) return { dispatched: false, note: "Queued. Scheduled GitHub Actions will collect the video (workflow dispatch token is not configured)." };
  try {
    const response = await fetch("https://api.github.com/repos/lenchingozi2-web/yt-automation-agent/actions/workflows/studio_worker.yml/dispatches", {
      method: "POST",
      headers: { Accept: "application/vnd.github+json", Authorization: "Bearer " + token, "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json" },
      body: JSON.stringify({ ref: "main" }), cache: "no-store", signal: AbortSignal.timeout(8000),
    });
    return response.ok ? { dispatched: true, note: "Worker dispatched." } :
      { dispatched: false, note: "Queued. Worker dispatch returned " + response.status + "; scheduled workflow may collect it." };
  } catch {
    return { dispatched: false, note: "Queued. Worker dispatch unreachable; scheduled workflow may collect it." };
  }
}
