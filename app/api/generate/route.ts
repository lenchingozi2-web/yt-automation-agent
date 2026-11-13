import { NextResponse } from "next/server";
import { normalizeDraft, normalizeVideo, toSafeMessage } from "@/src/lib/studio";
import { researchedDraft } from "@/src/lib/studio-research";
import { getResearchIdea, markBriefingQueued } from "@/src/lib/studio-briefing";
import { dispatchStudioWorker, errorResponse, findVideo, jobIdOf, quotaOf, recentTitles, requestObject, safeInsertVideo, statusFor, studioContext, StudioRequestError } from "@/src/lib/studio-server";

export const maxDuration = 300;
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    const { client, userId } = await studioContext(request);
    const body = await requestObject(request);
    const autoRender = body.autoRender === true;
    const storyId = jobIdOf(body.researchIdeaId);
    if (body.researchIdeaId !== undefined && !storyId) throw new StudioRequestError("Choose a valid briefing story.", 400);
    const prompt = typeof body.prompt === "string" ? body.prompt.trim() : "";
    if (!storyId && (prompt.length < 3 || prompt.length > 4000)) return NextResponse.json({ ok: false, error: "Enter a topic between 3 and 4000 characters." }, { status: 400 });
    const requestId = storyId || jobIdOf(body.requestId);
    if (autoRender) {
      const existing = requestId ? await findVideo(client, requestId) : null;
      if (existing) return NextResponse.json({ ok: true, message: "This production is already queued.", draft: normalizeDraft(existing), video: normalizeVideo(existing) });
      const quota = await quotaOf(client);
      if (quota.used >= quota.limit) return NextResponse.json({ ok: false, error: "Daily production allowance reached. You can retry existing failed videos." }, { status: 429 });
    }
    const seconds = Math.round(Math.max(60, Math.min(720, Number(body.duration) || 180)));
    const format = body.format === "9:16" ? "9:16" : "16:9";
    const story = storyId ? await getResearchIdea(client, storyId) : undefined;
    const brief = story ? "Create a video script from this story. Naturally cite its references in the voiceover and append the exact URLs to the YouTube description." : prompt;
    const draft = await researchedDraft(brief, seconds, format, await recentTitles(client), story);
    if (!autoRender) return NextResponse.json({ ok: true, message: "Script researched and checked. Review your scenes below.", draft });
    try {
      const saved = await safeInsertVideo(client, draft, userId, requestId);
      if (storyId) await markBriefingQueued(client, storyId);
      const dispatch = await dispatchStudioWorker();
      return NextResponse.json({ ok: true, draft, video: normalizeVideo(saved), message: "Video queued automatically.", note: dispatch.note });
    } catch (error) {
      console.error("Studio could not queue generated draft", error);
      if (storyId) throw error;
      return NextResponse.json({ ok: true, draft, message: "Script generated. The render could not be queued.", note: toSafeMessage(error) });
    }
  } catch (error) {
    console.error("Studio script generation failed", error);
    return NextResponse.json(errorResponse(error), { status: statusFor(error) });
  }
}
