import { NextResponse } from "next/server";
import { normalizeVideo, toSafeMessage } from "@/src/lib/studio";
import { dispatchStudioWorker, errorResponse, quotaOf, safeInsertVideo, statusFor, studioClient } from "@/src/lib/studio-server";
export const dynamic = "force-dynamic";
export async function POST(request: Request) {
  try {
    const client = await studioClient(request);
    const body = await request.json() as Record<string, unknown>;
    let saved: Record<string, unknown>;
    if (typeof body.id === "string" && body.id) {
      // Retries do not count as newly created videos.
      const { data, error } = await client.from("videos").update({ status: "pending" })
        .eq("id", body.id).in("status", ["failed", "pending"]).select("*").single();
      if (error || !data) throw new Error(toSafeMessage(error, "Only pending or failed videos can be retried."));
      saved = data as Record<string, unknown>;
    } else {
      const quota = await quotaOf(client);
      if (quota.used >= quota.limit) {
        return NextResponse.json({ ok: false,
          error: "Today's production allowance is full (" + quota.limit + "/" + quota.limit + "). Your next allowance starts at midnight Nigeria time. Failed jobs can still be retried.",
          quota }, { status: 429 });
      }
      saved = await safeInsertVideo(client, body);
    }
    const dispatch = await dispatchStudioWorker();
    return NextResponse.json({ ok: true, message: "Video queued for rendering!",
      video: normalizeVideo(saved), workerDispatched: dispatch.dispatched, note: dispatch.note },
      { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    console.error("Studio render queue failed", error);
    return NextResponse.json(errorResponse(error), { status: statusFor(error) });
  }
}
