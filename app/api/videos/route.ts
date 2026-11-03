import { NextResponse } from "next/server";
import { getAdminClient, getRequestClient } from "@/src/lib/supabase-admin";

export const dynamic = "force-dynamic";

type Scene = { scene_number: number; narration: string; search_queries: string[] };
type ScriptPayload = {
  id?: string;
  title: string;
  description?: string;
  tags?: string[];
  format?: "16:9" | "9:16";
  duration_seconds?: number;
  scenes?: Scene[];
};

function messageOf(error: unknown): string {
  if (typeof error === "string") return error;
  if (error && typeof error === "object") {
    const value = error as { message?: unknown; error?: unknown; details?: unknown };
    for (const candidate of [value.message, value.error, value.details]) {
      if (typeof candidate === "string" && candidate.trim()) return candidate;
    }
    try { return JSON.stringify(error); } catch { return "Database request failed."; }
  }
  return "Database request failed.";
}

function queueClient(request: Request) {
  try { return getAdminClient(); }
  catch { return getRequestClient(request); }
}

function isSchemaError(error: unknown) {
  return /column|schema cache|does not exist|could not find the .* column/i.test(messageOf(error));
}

function countsFor(rows: Array<{ status?: string }>) {
  return rows.reduce<Record<string, number>>((counts, row) => {
    const status = row.status || "unknown";
    counts[status] = (counts[status] || 0) + 1;
    return counts;
  }, { pending: 0, rendering: 0, completed: 0, failed: 0 });
}

export async function GET(request: Request) {
  try {
    const { data, error } = await queueClient(request).from("videos").select("*").order("created_at", { ascending: false });
    if (error) throw error;
    const videos = (data || []) as Array<{ status?: string }>;
    return NextResponse.json({ videos, counts: countsFor(videos) }, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    console.error("Video queue fetch failed", error);
    return NextResponse.json({ error: messageOf(error), videos: [], counts: { pending: 0, rendering: 0, completed: 0, failed: 0 } }, { status: 500 });
  }
}

async function insertVideo(request: Request, payload: ScriptPayload) {
  const client = queueClient(request);
  const full = {
    title: payload.title,
    description: payload.description ?? "",
    tags: payload.tags ?? [],
    format: payload.format ?? "16:9",
    duration_seconds: payload.duration_seconds ?? 0,
    scenes: payload.scenes ?? [],
    status: "pending",
  };
  const first = await client.from("videos").insert(full).select().single();
  if (!first.error) return first.data;
  if (!isSchemaError(first.error)) throw first.error;

  const core = await client.from("videos").insert({ title: payload.title, status: "pending", scenes: payload.scenes ?? [] }).select().single();
  if (!core.error) return core.data;
  if (!isSchemaError(core.error)) throw core.error;

  const script = { title: payload.title, status: "pending", script: { ...full } };
  const scriptFallback = await client.from("videos").insert(script).select().single();
  if (scriptFallback.error) throw scriptFallback.error;
  return scriptFallback.data;
}

async function updateVideo(request: Request, payload: ScriptPayload) {
  if (!payload.id) throw new Error("A video id is required.");
  const client = queueClient(request);
  const full = {
    title: payload.title,
    description: payload.description ?? "",
    tags: payload.tags ?? [],
    format: payload.format ?? "16:9",
    duration_seconds: payload.duration_seconds ?? 0,
    scenes: payload.scenes ?? [],
  };
  const first = await client.from("videos").update(full).eq("id", payload.id).eq("status", "pending").select().single();
  if (!first.error) return first.data;
  if (!isSchemaError(first.error)) throw first.error;

  const core = await client.from("videos").update({ title: payload.title, scenes: payload.scenes ?? [] }).eq("id", payload.id).eq("status", "pending").select().single();
  if (!core.error) return core.data;
  if (!isSchemaError(core.error)) throw core.error;

  const scriptFallback = await client.from("videos").update({ title: payload.title, script: { ...full } }).eq("id", payload.id).eq("status", "pending").select().single();
  if (scriptFallback.error) throw scriptFallback.error;
  return scriptFallback.data;
}

export async function POST(request: Request) {
  try {
    const payload = await request.json() as ScriptPayload;
    if (!payload.title || !Array.isArray(payload.scenes)) return NextResponse.json({ error: "A title and scenes are required." }, { status: 400 });
    return NextResponse.json(await insertVideo(request, payload));
  } catch (error) {
    console.error("Video save failed", error);
    return NextResponse.json({ error: messageOf(error) }, { status: 500 });
  }
}

export async function PATCH(request: Request) {
  try {
    const payload = await request.json() as ScriptPayload;
    return NextResponse.json(await updateVideo(request, payload));
  } catch (error) {
    console.error("Video update failed", error);
    return NextResponse.json({ error: messageOf(error) }, { status: 500 });
  }
}
