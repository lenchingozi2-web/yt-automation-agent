import { NextResponse } from "next/server";
import { getRequestClient } from "@/src/lib/supabase-admin";

export const dynamic = "force-dynamic";

type ScriptPayload = {
  id?: string;
  title: string;
  description: string;
  tags?: string[];
  format?: "16:9" | "9:16";
  duration_seconds?: number;
  scenes: Array<{ scene_number: number; narration: string; search_queries: string[] }>;
};

export async function POST(request: Request) {
  try {
    const payload = await request.json() as ScriptPayload;
    const record = { title: payload.title, description: payload.description, tags: payload.tags ?? [], format: payload.format ?? "16:9", duration_seconds: payload.duration_seconds ?? 0, scenes: payload.scenes, status: "pending" };
    const { data, error } = await getRequestClient(request).from("videos").insert(record).select().single();
    if (error) throw error;
    return NextResponse.json(data);
  } catch (error) {
    console.error("Video save failed", error);
    return NextResponse.json({ error: error instanceof Error ? error.message : String(error) }, { status: 500 });
  }
}

export async function PATCH(request: Request) {
  try {
    const payload = await request.json() as ScriptPayload;
    if (!payload.id) return NextResponse.json({ error: "A video id is required." }, { status: 400 });
    const { data, error } = await getRequestClient(request).from("videos").update({ title: payload.title, description: payload.description, tags: payload.tags ?? [], format: payload.format ?? "16:9", duration_seconds: payload.duration_seconds ?? 0, scenes: payload.scenes }).eq("id", payload.id).eq("status", "pending").select().single();
    if (error) throw error;
    return NextResponse.json(data);
  } catch (error) {
    console.error("Video update failed", error);
    return NextResponse.json({ error: error instanceof Error ? error.message : String(error) }, { status: 500 });
  }
}
