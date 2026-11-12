import { NextResponse } from "next/server";
import { normalizeVideo } from "@/src/lib/studio";
import { dispatchStudioWorker, errorResponse, findVideo, jobIdOf, quotaOf, requestObject, safeInsertVideo, statusFor, studioContext, StudioRequestError } from "@/src/lib/studio-server";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    const { client, userId } = await studioContext(request);
    const body = await requestObject(request);
    const publishing = body.action === "publish";
    let saved: Record<string, unknown>;
    if (body.id !== undefined || publishing) {
      const id = jobIdOf(body.id);
      if (!id) throw new StudioRequestError("Choose a valid video.", 400);
      const original = await findVideo(client, id);
      if (!original) throw new StudioRequestError("Video not found.", 404);
      const scenes = Array.isArray(original.scenes) ? original.scenes.map((scene: Record<string, unknown>) => ({ ...scene })) : [];
      if (!scenes.length) throw new StudioRequestError("This video has no editable scenes.", 409);
      const first = scenes[0];
      const meta = (first._studio && typeof first._studio === "object" ? first._studio : {}) as Record<string, unknown>;
      if (publishing) {
        if (body.approved !== true) throw new StudioRequestError("Watch the preview and approve the upload from the video player.", 400);
        const video = normalizeVideo(original);
        if (original.status !== "completed" || !video.video_url || video.youtube_url) {
          throw new StudioRequestError("Choose a completed video with an MP4 that has not been uploaded.", 409);
        }
        if (meta.publish_approved_at && (video.publish_status === "uploading" || meta.publish_requested)) {
          return NextResponse.json({ ok: true, message: "This upload is already in the queue.", video });
        }
        first._studio = {
          ...meta,
          publish_approved_at: new Date().toISOString(),
          publish_approved_by: userId,
          publish_requested: true,
          publish_status: "pending",
          publish_attempts: 0,
          publish_error: null,
        };
        const field = "scenes->0->_studio->>publish_status";
        const update = client.from("videos").update({ scenes }).eq("id", id).eq("status", "completed");
        const statusGuard = typeof meta.publish_status === "string" ? update.eq(field, meta.publish_status) : update.is(field, null);
        const approvalField = "scenes->0->_studio->>publish_approved_at";
        const guarded = typeof meta.publish_approved_at === "string" ? statusGuard.eq(approvalField, meta.publish_approved_at) : statusGuard.is(approvalField, null);
        const { data, error } = await guarded.select("*").maybeSingle();
        if (error) throw error;
        if (!data) throw new StudioRequestError("This upload changed while you were viewing it. Refresh the queue.", 409);
        saved = data;
      } else {
        if (!["failed", "pending"].includes(original.status)) {
          throw new StudioRequestError("Only queued or failed renders can be retried.", 409);
        }
        first._studio = { ...meta, render_attempts: 0, progress_note: "Queued for rendering" };
        const { data, error } = await client.from("videos").update({ status: "pending", error_message: null, scenes })
          .eq("id", id).eq("status", original.status).select("*").maybeSingle();
        if (error) throw error;
        if (!data) throw new StudioRequestError("This render changed while you were viewing it. Refresh the queue.", 409);
        saved = data;
      }
    } else {
      const requestId = jobIdOf(body.requestId);
      const existing = requestId ? await findVideo(client, requestId) : null;
      if (existing) return NextResponse.json({ ok: true, message: "This production is already queued.", video: normalizeVideo(existing) });
      const quota = await quotaOf(client);
      if (quota.used >= quota.limit) {
        return NextResponse.json({
          ok: false, error: "Today’s production allowance is full. It resets at midnight WAT. You can still retry failed jobs.", quota,
        }, { status: 429 });
      }
      saved = await safeInsertVideo(client, body, userId, requestId);
    }
    const dispatch = await dispatchStudioWorker();
    return NextResponse.json({
      ok: true, message: publishing ? "Approved. The worker will upload the saved MP4 privately to YouTube." : "Video queued for rendering.",
      video: normalizeVideo(saved), workerDispatched: dispatch.dispatched, note: dispatch.note,
    }, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    if (statusFor(error) >= 500) console.error("Studio render queue failed", error);
    return NextResponse.json(errorResponse(error), { status: statusFor(error), headers: { "Cache-Control": "no-store" } });
  }
}
