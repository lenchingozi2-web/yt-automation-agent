import { NextResponse } from "next/server";
import { canRetryYouTubeThumbnail, normalizeVideo, youtubePublishAction } from "@/src/lib/studio";
import { dispatchStudioWorker, errorResponse, findVideo, jobIdOf, quotaOf, requestObject, safeInsertVideo, statusFor, studioContext, StudioRequestError } from "@/src/lib/studio-server";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    const { client, userId } = await studioContext(request);
    const body = await requestObject(request);
    const publishing = body.action === "publish";
    let saved: Record<string, unknown>;
    if (body.id !== undefined || publishing || body.action === "retry_thumbnail") {
      const id = jobIdOf(body.id);
      if (!id) throw new StudioRequestError("Choose a valid video.", 400);
      const original = await findVideo(client, id);
      if (!original) throw new StudioRequestError("Video not found.", 404);
      if (body.action === "retry_thumbnail") {
        if (!canRetryYouTubeThumbnail(normalizeVideo(original))) {
          throw new StudioRequestError("This upload has no saved thumbnail to retry.", 409);
        }
        const dispatch = await dispatchStudioWorker(id);
        if (!dispatch.dispatched) throw new StudioRequestError("The thumbnail retry could not start. Try again or use YouTube Studio.", 503);
        return NextResponse.json({ ok: true, message: "Thumbnail retry started." }, { headers: { "Cache-Control": "no-store" } });
      }
      const storedScenes = typeof original.scenes === "string" ? JSON.parse(original.scenes) : original.scenes;
      const scenes = Array.isArray(storedScenes) ? storedScenes.map((scene: Record<string, unknown>) => ({ ...scene })) : [];
      if (!scenes.length) throw new StudioRequestError("This video has no editable scenes.", 409);
      const packedScenes = () => typeof original.scenes === "string" ? JSON.stringify(scenes) : scenes;
      const first = scenes[0];
      const meta = (first._studio && typeof first._studio === "object" ? first._studio : {}) as Record<string, unknown>;
      if (publishing) {
        if (body.approved !== true) throw new StudioRequestError("Approve this version from the video preview before uploading.", 400);
        const video = normalizeVideo(original);
        if (video.youtube_url || video.publish_status === "uploaded") {
          return NextResponse.json({ ok: true, message: "This video is already uploaded.", video });
        }
        if (meta.publish_approved_at && ["pending", "uploading"].includes(video.publish_status ?? "")) {
          return NextResponse.json({ ok: true, message: "This upload is already in the queue.", video });
        }
        const action = youtubePublishAction(video);
        if (!action) throw new StudioRequestError("Choose a completed video with a saved MP4.", 409);
        if (action === "recheck" && body.checkedYouTube !== true) {
          throw new StudioRequestError("Check YouTube Studio for an existing copy before retrying.", 409);
        }
        const release = body.publishAt == null || body.publishAt === "" ? null : typeof body.publishAt === "string" ? Date.parse(body.publishAt) : NaN;
        if (release !== null && (!Number.isFinite(release) || release < Date.now() + 15 * 60_000)) {
          throw new StudioRequestError("Choose a release time at least 15 minutes from now, or publish now.", 400);
        }
        first._studio = {
          ...meta,
          publish_at: release === null ? null : new Date(release).toISOString(),
          publish_approved_at: new Date().toISOString(),
          publish_approved_by: userId,
          publish_requested: true,
          publish_status: "pending",
          publish_attempts: 0,
          publish_error: null,
          publish_retry_at: null,
          progress_note: "Approved for public YouTube upload.",
        };
        const field = "scenes->0->_studio->>publish_status";
        const update = client.from("videos").update({ scenes: packedScenes() }).eq("id", id).eq("status", "completed");
        const statusGuard = typeof meta.publish_status === "string" ? update.eq(field, meta.publish_status) : update.is(field, null);
        const approvalField = "scenes->0->_studio->>publish_approved_at";
        const guarded = typeof original.scenes === "string" ? update.eq("scenes", original.scenes) :
          typeof meta.publish_approved_at === "string" ? statusGuard.eq(approvalField, meta.publish_approved_at) : statusGuard.is(approvalField, null);
        const { data, error } = await guarded.select("*").maybeSingle();
        if (error) throw error;
        if (!data) {
          const current = normalizeVideo(await findVideo(client, id));
          if (current.youtube_url || (current.publish_approved_at && ["pending", "uploading"].includes(current.publish_status ?? ""))) {
            return NextResponse.json({ ok: true, message: current.youtube_url ? "This video is already uploaded." : "This upload is already in the queue.", video: current });
          }
          throw new StudioRequestError("This upload changed while you were viewing it. Refresh the queue.", 409);
        }
        saved = data;
      } else {
        if (!["failed", "pending"].includes(original.status)) {
          throw new StudioRequestError("Only queued or failed renders can be retried.", 409);
        }
        first._studio = { ...meta, render_attempts: 0, progress_note: "Queued for rendering" };
        const { data, error } = await client.from("videos").update({ status: "pending", error_message: null, scenes: packedScenes() })
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
      ok: true, message: publishing ? (normalizeVideo(saved).publish_at ? "Approved. Studio will upload and schedule the release on YouTube." : "Approved. The saved MP4 is queued for a public YouTube upload.") : "Video queued for rendering.",
      video: normalizeVideo(saved), workerDispatched: dispatch.dispatched, note: dispatch.note,
    }, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    if (statusFor(error) >= 500) console.error("Studio render queue failed", error);
    return NextResponse.json(errorResponse(error), { status: statusFor(error), headers: { "Cache-Control": "no-store" } });
  }
}
