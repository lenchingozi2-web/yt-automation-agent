import { NextResponse } from "next/server";
import { normalizeVideo, toSafeMessage } from "@/src/lib/studio";
import { dispatchStudioWorker, errorResponse, quotaOf, safeInsertVideo, statusFor, studioClient, studioUserId } from "@/src/lib/studio-server";
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
      // Reserve headroom on Supabase Free (1 GB total storage). Do not burn
      // rendering time when existing unpublished previews may fill the bucket.
      const { data: objects, error: storageError } = await client.storage
        .from(process.env.SUPABASE_VIDEO_BUCKET || "renders").list("", { limit: 1000 });
      if (!storageError && objects) {
        const bytes = objects.reduce((sum, object) => sum + (Number(object.metadata?.size) || 0), 0);
        if (bytes >= 800 * 1024 * 1024) {
          return NextResponse.json({ ok: false,
            error: "Preview storage is nearing the Supabase Free limit. Publish or safely archive older videos before queueing another render.",
            storage_bytes: bytes }, { status: 507 });
        }
      }
      saved = await safeInsertVideo(client, body, await studioUserId(request));
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
