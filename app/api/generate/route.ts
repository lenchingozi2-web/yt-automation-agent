import { GoogleGenAI } from "@google/genai";
import { NextResponse } from "next/server";
import { getAdminClient } from "@/src/lib/supabase-admin";

const schemaPrompt = `Return only valid JSON with this shape: {"title": string, "description": string, "tags": string[], "scenes": [{"scene_number": number, "narration": string, "search_queries": string[]}]}. Create a compelling YouTube script. Make scenes visually specific and give each scene 2-3 Pixabay search queries.`;

export async function POST(request: Request) {
  try {
    const { prompt } = await request.json();
    if (typeof prompt !== "string" || prompt.trim().length < 3) return NextResponse.json({ error: "Add a topic or creative brief." }, { status: 400 });
    if (!process.env.GEMINI_API_KEY) throw new Error("Missing GEMINI_API_KEY");
    const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });
    const response = await ai.models.generateContent({ model: "gemini-2.5-flash", contents: `${schemaPrompt}\n\nCreative brief: ${prompt.trim()}`, config: { responseMimeType: "application/json" } });
    const script = JSON.parse(response.text ?? "{}");
    const { data, error } = await getAdminClient().from("videos").insert({ ...script, status: "pending" }).select().single();
    if (error) throw error;
    return NextResponse.json(data);
  } catch (error) {
    console.error("Script generation failed", error);
    return NextResponse.json({ error: "Could not generate the script." }, { status: 500 });
  }
}