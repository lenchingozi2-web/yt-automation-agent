import { NextResponse } from "next/server";
import { normalizeDraft, toSafeMessage } from "@/src/lib/studio";
import { errorResponse, statusFor, studioClient } from "@/src/lib/studio-server";
export const maxDuration = 60;
export const dynamic = "force-dynamic";
type Model = { name: string; supportedGenerationMethods?: string[] };
type GeminiResponse = { models?: Model[]; candidates?: Array<{ content?: { parts?: Array<{ text?: string }> } }>; error?: { message?: string } };
async function geminiRequest(url: string, key: string, init: RequestInit = {}) {
  const response = await fetch(url, { ...init, cache: "no-store",
    headers: { "x-goog-api-key": key, ...(init.body ? { "Content-Type": "application/json" } : {}) },
    signal: AbortSignal.timeout(45000) });
  const body = await response.json().catch(() => ({})) as GeminiResponse;
  if (!response.ok) throw new Error(body.error?.message || "Gemini returned HTTP " + response.status);
  return body;
}
async function availableModels(key: string) {
  try {
    const result = await geminiRequest("https://generativelanguage.googleapis.com/v1beta/models?key=" + encodeURIComponent(key), key);
    const names = (result.models || []).filter(m => (m.supportedGenerationMethods || []).includes("generateContent")).map(m => m.name.replace(/^models\//, ""));
    return [...names.filter(n => /flash/i.test(n) && !/lite|image|tts|audio|embedding/i.test(n)),
      ...names.filter(n => /pro/i.test(n) && !/image|tts|audio/i.test(n)),
      ...names.filter(n => !/flash|pro/i.test(n))].filter((v,i,a) => a.indexOf(v) === i);
  } catch { return []; }
}
export async function POST(request: Request) {
  try {
    await studioClient(request);
    const body = await request.json() as Record<string, unknown>;
    const prompt = typeof body.prompt === "string" ? body.prompt.trim() : "";
    if (prompt.length < 3 || prompt.length > 4000) return NextResponse.json({ ok: false, error: "Enter a topic between 3 and 4000 characters." }, { status: 400 });
    const key = [process.env.GEMINI_API_KEY, process.env.GOOGLE_API_KEY, process.env.GOOGLE_GENERATIVE_AI_API_KEY].find(Boolean)?.trim().replace(/^["']|["']$/g, "") || "";
    if (!key) throw new Error("Gemini API key is not configured.");
    const seconds = Math.max(60, Math.min(720, Number(body.duration) || 180));
    const format = body.format === "9:16" ? "9:16" : "16:9";
    const sceneCount = Math.max(4, Math.min(45, Math.ceil(seconds / 17)));
    const instruction = ["Return strictly valid JSON with title, description, tags (array), format, duration_seconds, scenes (array).",
      "Each scene has scene_number, narration, search_queries (2-3 Pixabay video search phrases) and estimated_duration (seconds).",
      "Write vivid, natural narration long enough for the full target duration without repetition. Pixabay only, no Pexels.",
      "Approximately " + sceneCount + " scenes; about " + Math.round(seconds / sceneCount) + " seconds per scene.",
      "Video length " + seconds + " seconds. Aspect ratio " + format + ". Topic: " + prompt].join("\n");
    const listed = await availableModels(key);
    const models = [...listed, "gemini-flash-latest", "gemini-3.8-flash", "gemini-2.5-flash"].filter((v,i,a) => a.indexOf(v) === i).slice(0, 5);
    let problem = "";
    for (const model of models) {
      try {
        const result = await geminiRequest("https://generativelanguage.googleapis.com/v1beta/models/" + model + ":generateContent", key, {
          method: "POST", body: JSON.stringify({ contents: [{ parts: [{ text: instruction }] }], generationConfig: { responseMimeType: "application/json", maxOutputTokens: 65536 } }),
        });
        const content = result.candidates?.[0]?.content?.parts?.map(p => p.text || "").join("") || "";
        const parsed: unknown = JSON.parse(content.trim().replace(/^\x60{3}(?:json)?\s*/i, "").replace(/\s*\x60{3}$/, ""));
        const draft = normalizeDraft({ ...(parsed as object), format, duration_seconds: seconds });
        if (draft.scenes.length === 0) throw new Error("Gemini returned no scenes.");
        return NextResponse.json({ ok: true, message: "Script generated! Review your scenes below.", draft });
      } catch (error) {
        problem = toSafeMessage(error, "Gemini did not return a valid script.");
        if (/API key not valid|UNAUTHENTICATED|quota|RESOURCE_EXHAUSTED/i.test(problem)) break;
      }
    }
    throw new Error(problem || "No Gemini model produced a valid script.");
  } catch (error) {
    console.error("Studio script generation failed", error);
    return NextResponse.json(errorResponse(error), { status: statusFor(error) });
  }
}
