import { NextResponse } from 'next/server';
import { GoogleGenAI } from '@google/genai';

const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY as string });

export async function POST(req: Request) {
  try {
    const { prompt } = await req.json();
    
    const systemPrompt = `You are an expert YouTube scriptwriter. Based on the user's prompt, generate a complete video script. 
    Respond strictly in valid JSON format matching this schema:
    {
      "title": "Catchy YouTube Title",
      "description": "Short video description",
      "bgm_query": "search term for background music, e.g., 'cinematic ambient'",
      "scenes": [
        {
          "scene_number": 1,
          "narration": "The exact spoken text for the scene.",
          "search_queries": ["search term 1", "search term 2"]
        }
      ]
    }`;

    const response = await ai.models.generateContent({
      model: 'gemini-2.5-flash',
      contents: systemPrompt + "\n\nUser Prompt: " + prompt,
      config: {
        responseMimeType: "application/json",
      }
    });

    const result = JSON.parse(response.text as string);
    return NextResponse.json(result);
  } catch (error) {
    console.error("AI Generation Error:", error);
    return NextResponse.json({ error: 'Failed to generate script' }, { status: 500 });
  }
}