import { NextResponse } from "next/server";
import { normalizeDraft, normalizeVideo, toSafeMessage } from "@/src/lib/studio";
import { researchedDraft } from "@/src/lib/studio-research";
import { dispatchStudioWorker, errorResponse, findVideo, jobIdOf, quotaOf, recentTitles, requestObject, safeInsertVideo, statusFor, studioContext } from "@/src/lib/studio-server";

export const maxDuration = 180;
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    const { client, userId } = await studioContext(request);
    const body = await requestObject(request);
    const prompt = typeof body.prompt === "string" ? body.prompt.trim() : "";
    if (prompt.length < 3 || prompt.length > 4000) return NextResponse.json({ ok: false, error: "Enter a topic between 3 and 4000 characters." }, { status: 400 });
    const autoRender = body.autoRender === true;
    const requestId = jobIdOf(body.requestId);
    if (autoRender) {
      const existing = requestId ? await findVideo(client, requestId) : null;
      if (existing) return NextResponse.json({ ok: true, message: "This production is already queued.", draft: normalizeDraft(existing), video: normalizeVideo(existing) });
      const quota = await quotaOf(client);
      if (quota.used >= quota.limit) return NextResponse.json({ ok: false, error: "Daily production allowance reached. You can retry existing failed videos." }, { status: 429 });
    }
    const seconds = Math.round(Math.max(60, Math.min(720, Number(body.duration) || 180)));
    const format = body.format === "9:16" ? "9:16" : "16:9";
    const draft = await researchedDraft(prompt, seconds, format, await recentTitles(client));
    if (!autoRender) return NextResponse.json({ ok: true, message: "Script researched and checked. Review your scenes below.", draft });
    try {
      const saved = await safeInsertVideo(client, draft, userId, requestId);
      const dispatch = await dispatchStudioWorker();
      return NextResponse.json({ ok: true, draft, video: normalizeVideo(saved), message: "Video queued automatically.", note: dispatch.note });
    } catch (error) {
      console.error("Studio could not queue generated draft", error);
      return NextResponse.json({ ok: true, draft, message: "Script generated. The render could not be queued.", note: toSafeMessage(error) });
    }
  } catch (error) {
    console.error("Studio script generation failed", error);
    return NextResponse.json(errorResponse(error), { status: statusFor(error) });
  }
}
