import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { normalizeDraft, toSafeMessage, type Draft, type VideoRevision } from "@/src/lib/studio";

export class StudioAuthError extends Error { constructor(message = "Please sign in to use Studio.") { super(message); } }
const url = () => process.env.NEXT_PUBLIC_SUPABASE_URL || process.env.SUPABASE_URL || "";
const anon = () => process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "";
const role = () => process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_SECRET_KEY || "";
export class StudioRequestError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

export async function studioContext(request: Request): Promise<{ client: SupabaseClient; userId: string }> {
  const auth = request.headers.get("authorization") || "";
  const jwt = /^Bearer (.+)$/i.exec(auth)?.[1];
  if (!jwt) throw new StudioAuthError();
  if (!url() || !anon()) throw new Error("Studio Supabase URL or anon key is missing.");
  const userClient = createClient(url(), anon(), { auth: { autoRefreshToken: false, persistSession: false },
    global: { headers: { Authorization: "Bearer " + jwt } } });
  const { data, error } = await userClient.auth.getUser(jwt);
  if (error || !data.user) throw new StudioAuthError("Your session has expired. Please sign in again.");
  if (!role()) throw new Error("Studio storage credentials are missing.");
  // A service-role key bypasses RLS: never grant it to arbitrary authenticated users.
  const allowed = (process.env.STUDIO_ALLOWED_EMAILS || process.env.STUDIO_OWNER_EMAIL || "")
    .split(/[,;]/).map(email => email.trim().toLowerCase()).filter(Boolean);
  if (!allowed.length) throw new Error("Set STUDIO_ALLOWED_EMAILS to the studio owner's sign-in email before enabling service-role access.");
  if (!data.user.email || !allowed.includes(data.user.email.toLowerCase())) {
    throw new StudioAuthError("Your account is not authorized for this Studio.");
  }
  return {
    client: createClient(url(), role(), { auth: { autoRefreshToken: false, persistSession: false } }),
    userId: data.user.id,
  };
}
export async function studioClient(request: Request) {
  return (await studioContext(request)).client;
}

export function statusFor(error: unknown) {
  if (error instanceof StudioAuthError) return 401;
  return error instanceof StudioRequestError ? error.status : 500;
}

export async function requestObject(request: Request): Promise<Record<string, unknown>> {
  const value = await request.json().catch(() => null);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new StudioRequestError("Send a valid JSON object.", 400);
  return value;
}

export const jobIdOf = (value: unknown) => typeof value === "string" &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value) ? value : undefined;

export async function findVideo(client: SupabaseClient, id: string) {
  const { data, error } = await client.from("videos").select("*").eq("id", id).maybeSingle();
  if (error) throw error;
  return data;
}

export function errorResponse(error: unknown) { return { ok: false, error: toSafeMessage(error, "The request failed.") }; }
const missingColumn = (error: unknown) => {
  const x = error as { code?: string } | null;
  return x?.code === "PGRST204" || /column .* does not exist|could not find the .* column|schema cache/i.test(toSafeMessage(error, ""));
};
function scriptPayload(draft: Draft) {
  return { ...draft, aspect_ratio: draft.format, duration: draft.duration_seconds, metadata: {
    aspect_ratio: draft.format, duration: draft.duration_seconds, description: draft.description,
    tags: draft.tags, sources: draft.sources, video_url: "", youtube_url: "", error_message: "" } };
}
export async function safeInsertVideo(client: SupabaseClient, approved: unknown, userId?: string, jobId?: string, revision?: VideoRevision) {
  const draft = normalizeDraft(approved);
  if (!draft.title.trim() || draft.scenes.length < 1 || draft.scenes.some(scene => !scene.narration.trim())) {
    throw new StudioRequestError("A title and narration for every scene are required.", 400);
  }
  if (draft.scenes.length > 60) throw new StudioRequestError("Maximum 60 scenes per video.", 400);
  if (draft.duration_seconds < 60 || draft.duration_seconds > 720) {
    throw new StudioRequestError("Set a target duration between 60 and 720 seconds.", 400);
  }
  const { data: stored, error: storageError } = await client.storage.from(process.env.SUPABASE_VIDEO_BUCKET || "renders").list("", { limit: 1000 });
  if (!storageError && stored && stored.reduce((sum, obj) => sum + (Number(obj.metadata?.size) || 0), 0) >= 800 * 1024 * 1024) {
    throw new StudioRequestError("Preview storage is nearly full. Publish or safely archive older videos before queueing another.", 507);
  }
  const packed = scriptPayload(draft);
  const metadata = { ...packed.metadata, ...(revision ? { revision } : {}) };
  // Actual production table uses scenes JSON and may have no metadata/script columns.
  // Keep custom thumbnail, duration and YouTube metadata alongside the first scene.
  const enrichedScenes = draft.scenes.map((scene, index) => index === 0
    ? { ...scene, _studio: { ...metadata, duration_seconds: draft.duration_seconds, thumbnail_title: draft.thumbnail_title || draft.title, format: draft.format, tags: draft.tags } }
    : scene);
  const withUser = (payload: Record<string, unknown>) => ({ ...payload, ...(userId ? { user_id: userId } : {}), ...(jobId ? { id: jobId } : {}) });
  const candidates: Record<string, unknown>[] = [
    withUser({ title: draft.title, status: "pending", description: draft.description, tags: draft.tags,
      format: draft.format, duration_seconds: draft.duration_seconds, thumbnail_title: draft.thumbnail_title || draft.title, scenes: enrichedScenes }),
    withUser({ title: draft.title, status: "pending", description: draft.description, scenes: enrichedScenes }),
    withUser({ title: draft.title, status: "pending", scenes: enrichedScenes }),
    withUser({ title: draft.title, status: "pending", scenes: JSON.stringify(enrichedScenes) }),
    withUser({ title: draft.title, status: "pending", script: { ...packed, metadata, ...(revision ? { revision } : {}) } }),
    withUser({ title: draft.title, status: "pending", scenes: { ...packed, ...metadata } }),
  ];
  let lastError: unknown;
  for (const payload of candidates) {
    const { data, error } = await client.from("videos").insert(payload).select("*").single();
    if (!error && data) return data;
    if (error?.code === "23505" && jobId) {
      const existing = await client.from("videos").select("*").eq("id", jobId).single();
      if (existing.data) return existing.data;
    }
    lastError = error;
    if (!missingColumn(error) && !/invalid input syntax|json|expected array|type mismatch|null value|check constraint|violates not-null/i.test(toSafeMessage(error))) break;
  }
  throw new Error("Could not save approved video: " + toSafeMessage(lastError, "Check the videos table schema and RLS policies."));
}
export async function recentTitles(client: SupabaseClient) {
  const { data, error } = await client.from("videos").select("title").order("created_at", { ascending: false }).limit(35);
  if (error) throw error;
  return [...new Set((data || []).map((row) => String(row.title || "").slice(0, 150)).filter(Boolean))];
}
export const STUDIO_DAILY_LIMIT = Math.max(1, Math.min(20, Number(process.env.STUDIO_DAILY_LIMIT || 3) || 3));
export function lagosStartOfDay(): string {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "Africa/Lagos", day: "2-digit", month: "2-digit", year: "numeric" }).formatToParts(new Date());
  const get = (kind: string) => parts.find(p => p.type === kind)?.value || "";
  return new Date(get("year") + "-" + get("month") + "-" + get("day") + "T00:00:00+01:00").toISOString();
}
export async function quotaOf(client: SupabaseClient) {
  const { count, error } = await client.from("videos").select("id", { count: "exact", head: true })
    .gte("created_at", lagosStartOfDay());
  if (error) throw error;
  return { used: count ?? 0, limit: STUDIO_DAILY_LIMIT, timezone: "Africa/Lagos" };
}

export async function dispatchStudioWorker(thumbnailVideoId?: string) {
  const token = process.env.STUDIO_GITHUB_DISPATCH_TOKEN?.trim() || process.env.GITHUB_TOKEN || process.env.GH_PAT || process.env.GH_TOKEN;
  if (!token) return { dispatched: false, note: "The automatic render worker checks the queue on a schedule. Processing may be delayed." };
  try {
    const response = await fetch("https://api.github.com/repos/lenchingozi2-web/yt-automation-agent/actions/workflows/studio_worker.yml/dispatches", {
      method: "POST",
      headers: { Accept: "application/vnd.github+json", Authorization: "Bearer " + token, "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json" },
      body: JSON.stringify({ ref: "main", ...(thumbnailVideoId ? { inputs: { thumbnail_video_id: thumbnailVideoId } } : {}) }),
      cache: "no-store", signal: AbortSignal.timeout(8000),
    });
    return response.ok ? { dispatched: true, note: "Worker dispatched." } :
      { dispatched: false, note: "Automatic rendering is queued. Scheduled processing will take over if immediate dispatch is unavailable (" + response.status + ")." };
  } catch {
    return { dispatched: false, note: "Automatic rendering is queued. Scheduled processing will take over if immediate dispatch is unavailable." };
  }
}
