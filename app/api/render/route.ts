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
      const { data: original, error: readError } = await client.from("videos").select("*").eq("id", body.id).single();
      if (readError || !original) throw new Error("Video not found.");
      const scenes = Array.isArray(original.scenes) ? original.scenes.map((s: Record<string, unknown>) => ({ ...s })) : [];
      if (!scenes.length) throw new Error("This video has no editable scenes.");
      const first = scenes[0] as Record<string, unknown>;
      const meta = (first._studio && typeof first._studio === "object" ? first._studio : {}) as Record<string, unknown>;
      if (body.action === "publish") {
        const video = normalizeVideo(original);
        if (original.status !== "completed" || !video.video_url || video.youtube_url) {
          throw new Error("Only a saved video without a YouTube upload can be sent for upload.");
        }
        first._studio = { ...meta, publish_requested: true, publish_status: "pending", publish_attempts: 0, publish_error: null };
        const { data, error } = await client.from("videos").update({ scenes }).eq("id", body.id).eq("status", "completed").select("*").single();
        if (error || !data) throw new Error(toSafeMessage(error, "Could not queue upload."));
        saved = data;
      } else {
        first._studio = { ...meta, render_attempts: 0, progress_note: "Queued for rendering" };
        const { data, error } = await client.from("videos").update({ status: "pending", error_message: null, scenes })
          .eq("id", body.id).in("status", ["failed", "pending"]).select("*").single();
        if (error || !data) throw new Error(toSafeMessage(error, "Only pending or failed videos can be retried."));
        saved = data;
      }
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
    return NextResponse.json({ ok: true, message: body.action === "publish" ? "YouTube upload queued; your saved MP4 will be reused." : "Video queued for rendering!",
      video: normalizeVideo(saved), workerDispatched: dispatch.dispatched, note: dispatch.note },
      { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    console.error("Studio render queue failed", error);
    return NextResponse.json(errorResponse(error), { status: statusFor(error) });
  }
}
