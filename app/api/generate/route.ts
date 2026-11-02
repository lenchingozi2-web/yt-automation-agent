import { NextResponse } from "next/server";
import { getRequestClient } from "@/src/lib/supabase-admin";

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
  const body = await response.json() as { candidates?: Array<{ content?: { parts?: Array<{ text?: string }> } }>; error?: { message?: string } };
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
      const { data, error } = await getRequestClient(request).from("videos").insert({ ...script, status: "pending" }).select().single();
      if (error) {
        const message = String(error.message || error);
        if (/column|schema cache|does not exist/i.test(message)) {
          const minimal = await getRequestClient(request).from("videos").insert({ title: script.title, description: script.description, scenes: script.scenes, status: "pending" }).select().single();
          if (minimal.error) throw minimal.error;
          video = minimal.data as Record<string, unknown>;
          dbWarning = `Saved with core columns only after schema error: ${message}`;
        } else throw error;
      } else video = data as Record<string, unknown>;
    } catch (error) {
      dbWarning = error instanceof Error ? error.message : String(error);
      console.warn("Script generated but database insert failed", error);
    }

    return NextResponse.json({ script, video, dbWarning });
  } catch (error) {
    console.error("Script generation failed", error);
    return NextResponse.json({ error: error instanceof Error ? error.message : String(error) }, { status: 500 });
  }
}
