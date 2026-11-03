import { NextResponse } from "next/server";
import { getAdminClient, getRequestClient } from "@/src/lib/supabase-admin";

export const maxDuration = 60;
export const dynamic = "force-dynamic";

const schemaPrompt = `Return only valid JSON with this exact shape: {"title": string, "description": string, "tags": string[], "format": "16:9" | "9:16", "duration_seconds": number, "scenes": [{"scene_number": number, "narration": string, "search_queries": string[]}]}. Create a compelling YouTube script. Make scenes visually specific and give each scene 2-3 concise Pixabay search queries. Do not wrap the JSON in markdown fences.`;

type Script = {
  title: string;
  description: string;
  tags: string[];
  format: "16:9" | "9:16";
  duration_seconds: number;
  scenes: Array<{ scene_number: number; narration: string; search_queries: string[] }>;
};


function queueClient(request: Request) {
  try { return getAdminClient(); }
  catch { return getRequestClient(request); }
}

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

function isSchemaError(error: unknown) {
  return /column|schema cache|does not exist|could not find the .* column/i.test(messageOf(error));
}

function durationSeconds(value: unknown, prompt: string) {
  if (typeof value === "number" && Number.isFinite(value) && value > 0) return Math.round(value);
  const match = prompt.match(/(?:about\s+|for\s+)?(\d+(?:\.\d+)?)\s*(seconds?|secs?|minutes?|mins?)/i);
  if (!match) return 180;
  const amount = Number(match[1]);
  return Math.round(/minute|min/i.test(match[2]) ? amount * 60 : amount);
}

function sceneCount(seconds: number) {
  return Math.max(4, Math.min(48, Math.ceil(seconds / 15)));
}

function cleanJson(text: string) {
  return text.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/i, "").trim();
}

async function generateWithModel(apiKey: string, model: string, prompt: string) {
  const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${encodeURIComponent(apiKey)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-goog-api-key": apiKey },
    body: JSON.stringify({
      contents: [{ parts: [{ text: prompt }] }],
      generationConfig: { responseMimeType: "application/json", maxOutputTokens: 65536 },
    }),
    cache: "no-store",
  });
  const raw = await response.text();
  let body: { candidates?: Array<{ content?: { parts?: Array<{ text?: string }> } }>; error?: { message?: string } } = {};
  try {
    body = JSON.parse(raw) as typeof body;
  } catch {
    if (!response.ok) throw new Error(raw.trim() || `Gemini ${model} request failed (${response.status})`);
    throw new Error(`Gemini ${model} returned a non-JSON response: ${raw.trim().slice(0, 300) || "empty response"}`);
  }
  if (!response.ok) throw new Error(body.error?.message || `Gemini ${model} request failed (${response.status})`);
  const text = body.candidates?.[0]?.content?.parts?.map((part) => part.text || "").join("") || "{}";
  return JSON.parse(cleanJson(text)) as Script;
}

function normalizeScript(script: Script, format: "16:9" | "9:16", seconds: number): Script {
  const scenes = Array.isArray(script.scenes) ? script.scenes : [];
  return {
    title: String(script.title || "Untitled production"),
    description: String(script.description || ""),
    tags: Array.isArray(script.tags) ? script.tags.map(String) : [],
    format: script.format === "9:16" || format === "9:16" ? "9:16" : "16:9",
    duration_seconds: seconds,
    scenes: scenes.map((scene, index) => ({
      scene_number: index + 1,
      narration: String(scene.narration || ""),
      search_queries: Array.isArray(scene.search_queries) ? scene.search_queries.map(String).slice(0, 3) : [],
    })),
  };
}

export async function POST(request: Request) {
  try {
    const body = await request.json() as { prompt?: unknown; duration?: unknown; format?: unknown };
    const prompt = typeof body.prompt === "string" ? body.prompt.trim() : "";
    if (prompt.length < 3) return NextResponse.json({ error: "Add a topic or creative brief." }, { status: 400 });
    const apiKey = (process.env.GEMINI_API_KEY || process.env.GOOGLE_API_KEY || process.env.GOOGLE_GENERATIVE_AI_API_KEY || "").trim().replace(/^["']|["']$/g, "");
    if (!apiKey) throw new Error("Missing GEMINI_API_KEY");

    const seconds = durationSeconds(body.duration, prompt);
    const format = body.format === "9:16" ? "9:16" : "16:9";
    const plannedScenes = sceneCount(seconds);
    const generationPrompt = `${schemaPrompt}\nTarget duration: ${seconds} seconds.\nTarget format: ${format}.\nCreate approximately ${plannedScenes} scenes, with about 15-20 seconds of narration per scene.\nCreative brief: ${prompt}`;
    const candidateModels = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-flash-latest", "gemini-2.5-flash"];
    let script: Script | undefined;
    let firstModelError = "";
    for (const model of candidateModels) {
      try {
        script = normalizeScript(await generateWithModel(apiKey, model, generationPrompt), format, seconds);
        break;
      } catch (error) {
        if (!firstModelError) firstModelError = error instanceof Error ? error.message : String(error);
      }
    }
    if (!script) throw new Error(firstModelError || "Gemini generation failed.");

    let dbWarning: string | null = null;
    let video: Record<string, unknown> | null = null;
    try {
      const client = queueClient(request);
      const full = await client.from("videos").insert({ ...script, status: "pending" }).select().single();
      if (!full.error) video = full.data as Record<string, unknown>;
      else if (isSchemaError(full.error)) {
        const core = await client.from("videos").insert({ title: script.title, status: "pending", scenes: script.scenes }).select().single();
        if (!core.error) {
          video = core.data as Record<string, unknown>;
          dbWarning = `Saved with core columns only after schema error: ${messageOf(full.error)}`;
        } else {
          const fallback = await client.from("videos").insert({ title: script.title, status: "pending", script: { ...script } }).select().single();
          if (fallback.error) throw fallback.error;
          video = fallback.data as Record<string, unknown>;
          dbWarning = `Saved with script column after schema error: ${messageOf(full.error)}`;
        }
      } else throw full.error;
    } catch (error) {
      dbWarning = messageOf(error);
      console.warn("Script generated but database insert failed", error);
    }

    return NextResponse.json({ script, video, dbWarning });
  } catch (error) {
    console.error("Script generation failed", error);
    return NextResponse.json({ error: messageOf(error) }, { status: 500 });
  }
}
