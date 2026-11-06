import { NextResponse } from "next/server";
import { normalizeDraft, normalizeVideo, toSafeMessage } from "@/src/lib/studio";
import { dispatchStudioWorker, errorResponse, quotaOf, safeInsertVideo, statusFor, studioClient, studioUserId } from "@/src/lib/studio-server";
export const maxDuration = 60;
export const dynamic = "force-dynamic";
type Model = { name: string; supportedGenerationMethods?: string[] };
type GeminiResponse = { models?: Model[]; candidates?: Array<{ content?: { parts?: Array<{ text?: string; thought?: boolean }> } }>; error?: { message?: string } };
async function geminiRequest(url: string, key: string, init: RequestInit = {}) {
  const response = await fetch(url, { ...init, cache: "no-store",
    headers: { "x-goog-api-key": key, ...(init.body ? { "Content-Type": "application/json" } : {}) },
    signal: AbortSignal.timeout(init.body ? 22000 : 4000) });
  const body = await response.json().catch(() => ({})) as GeminiResponse;
  if (!response.ok) throw new Error(body.error?.message || "Gemini returned HTTP " + response.status);
  return body;
}
async function availableModels(key: string) {
  try {
    const result = await geminiRequest("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000", key);
    const names = (result.models || []).filter(m => (m.supportedGenerationMethods || []).includes("generateContent")).map(m => m.name.replace(/^models\//, ""));
    names.sort((a,b) => Number(/latest/.test(b)) - Number(/latest/.test(a)) || b.localeCompare(a, undefined, { numeric: true }));
    return [...names.filter(n => /flash/i.test(n) && !/lite|image|tts|audio|embedding/i.test(n)),
      ...names.filter(n => /pro/i.test(n) && !/image|tts|audio/i.test(n)),
      ...names.filter(n => !/flash|pro/i.test(n))].filter((v,i,a) => a.indexOf(v) === i);
  } catch { return []; }
}
export async function POST(request: Request) {
  try {
    const client = await studioClient(request);
    const body = await request.json() as Record<string, unknown>;
    const prompt = typeof body.prompt === "string" ? body.prompt.trim() : "";
    if (prompt.length < 3 || prompt.length > 4000) return NextResponse.json({ ok: false, error: "Enter a topic between 3 and 4000 characters." }, { status: 400 });
    const autoRender = body.autoRender === true;
    if (autoRender) {
      const quota = await quotaOf(client);
      if (quota.used >= quota.limit) return NextResponse.json({ ok: false, error: "Daily production allowance reached. You can retry existing failed videos." }, { status: 429 });
    }
    const key = [process.env.GEMINI_API_KEY, process.env.GOOGLE_API_KEY, process.env.GOOGLE_GENERATIVE_AI_API_KEY].find(Boolean)?.trim().replace(/^["']|["']$/g, "") || "";
    if (!key) throw new Error("Gemini API key is not configured.");
    const seconds = Math.max(60, Math.min(720, Number(body.duration) || 180));
    const format = body.format === "9:16" ? "9:16" : "16:9";
    const sceneCount = Math.max(4, Math.min(60, Math.ceil(seconds / 12)));
    const instruction = ["Return strictly valid JSON with title, description, tags (array), format, duration_seconds, scenes (array).",
      "Each scene has scene_number, narration, search_queries (2-3 Pixabay video search phrases) and estimated_duration (seconds).",
      "Write natural conversational English with contractions and varied sentence lengths. One strong opening hook, concrete examples, and a useful closing thought. No stage directions, markdown, canned intros or invented statistics.",
      "Every scene must contain narration and relevant search phrases. Use about " + Math.round(seconds * 2.45) + " spoken words total. Pixabay footage only.",
      "Approximately " + sceneCount + " scenes; about " + Math.round(seconds / sceneCount) + " seconds per scene.",
      "Video length " + seconds + " seconds. Aspect ratio " + format + ". Topic: " + prompt].join("\n");
    const listed = await availableModels(key);
    const models = [...listed, "gemini-flash-latest"].filter((v,i,a) => a.indexOf(v) === i).slice(0, 2);
    let problem = "";
    for (const model of models) {
      try {
        const result = await geminiRequest("https://generativelanguage.googleapis.com/v1beta/models/" + model + ":generateContent", key, {
          method: "POST", body: JSON.stringify({ contents: [{ parts: [{ text: instruction }] }], generationConfig: { responseMimeType: "application/json", maxOutputTokens: 65536 } }),
        });
        const content = result.candidates?.[0]?.content?.parts?.filter(p => !p.thought).map(p => p.text || "").join("") || "";
        const parsed: unknown = JSON.parse(content.trim().replace(/^\x60{3}(?:json)?\s*/i, "").replace(/\s*\x60{3}$/, ""));
        const draft = normalizeDraft({ ...(parsed as object), format, duration_seconds: seconds });
        if (draft.scenes.length === 0 || draft.scenes.length > 60 || draft.scenes.some(s => !s.narration.trim() || !s.search_queries.length)) throw new Error("The generated script has an incomplete scene. Please try again.");
        const words = draft.scenes.reduce((sum, s) => sum + s.narration.split(/\s+/).length, 0);
        if (words < seconds * 1.6 || words > seconds * 3.4) throw new Error("The generated narration did not match the requested duration. Please try again.");
        if (autoRender) {
          const saved = await safeInsertVideo(client, draft, await studioUserId(request), typeof body.requestId === "string" && /^[0-9a-f-]{36}$/i.test(body.requestId) ? body.requestId : undefined);
          const dispatch = await dispatchStudioWorker();
          return NextResponse.json({ ok: true, draft, video: normalizeVideo(saved), message: "Video queued automatically.", note: dispatch.note });
        }
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
