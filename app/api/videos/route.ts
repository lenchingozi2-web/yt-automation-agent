import { NextResponse } from "next/server";
import { normalizeVideo } from "@/src/lib/studio";
import { errorResponse, findVideo, jobIdOf, quotaOf, statusFor, studioClient, studioContext, StudioRequestError } from "@/src/lib/studio-server";
export const dynamic = "force-dynamic";
export async function GET(request: Request) {
  try {
    const client = await studioClient(request);
    const [jobResult, quota] = await Promise.all([
      client.from("videos").select("*").order("created_at", { ascending: false }).limit(50),
      quotaOf(client),
    ]);
    if (jobResult.error) throw jobResult.error;
    const videos = (jobResult.data || []).map(normalizeVideo);
    const known = new Set(videos.map((video) => video.id));
    const missing = [...new Set(videos.flatMap(({ revision }) => revision
      ? [revision.parent_id, revision.root_id].filter((id): id is string => typeof id === "string" && !!jobIdOf(id) && !known.has(id))
      : []))];
    if (missing.length) {
      const sources = await client.from("videos").select("*").in("id", missing);
      if (sources.error) throw sources.error;
      videos.push(...(sources.data || []).map(normalizeVideo));
    }
    return NextResponse.json({ ok: true, videos, quota }, { headers: { "Cache-Control": "no-store, max-age=0" } });
  } catch (error) {
    if (statusFor(error) !== 401) console.error("Studio queue fetch failed", error);
    return NextResponse.json(errorResponse(error), { status: statusFor(error), headers: { "Cache-Control": "no-store" } });
  }
}


/**
 * Delete a finished Studio production and its own Supabase Storage objects.
 * YouTube uploads remain untouched. A deletion marker lets a failed partial
 * cleanup safely resume on the next request.
 */
export async function DELETE(request: Request) {
  try {
    const { client } = await studioContext(request);
    const id = jobIdOf(new URL(request.url).searchParams.get("id"));
    if (!id) throw new StudioRequestError("Choose a valid production.", 400);
    let row = await findVideo(client, id);
    if (!row) return NextResponse.json({ ok: true, message: "Production already removed.", deletedId: id });
    if (!["completed", "failed"].includes(row.status)) {
      throw new StudioRequestError("A production cannot be deleted while it is queued or rendering.", 409);
    }
    const video = normalizeVideo(row);
    if (["uploading", "pending", "retry"].includes(video.publish_status || "") && video.publish_approved_at && !video.youtube_url) {
      throw new StudioRequestError("The YouTube upload is still active. Finish or cancel that upload before deleting.", 409);
    }
    // A revision under construction may still require the source video.
    const { data: active, error: activeError } = await client.from("videos").select("id,scenes")
      .in("status", ["pending", "rendering"]).limit(100);
    if (activeError) throw activeError;
    for (const candidate of active || []) {
      const revision = normalizeVideo(candidate).revision;
      if (revision && (revision.parent_id === id || revision.root_id === id)) {
        throw new StudioRequestError("A revision still uses this video. Delete it after the revision finishes.", 409);
      }
    }
    const parsed = typeof row.scenes === "string" ? JSON.parse(row.scenes) : row.scenes;
    if (!Array.isArray(parsed) || !parsed.length || !parsed[0] || typeof parsed[0] !== "object") {
      throw new StudioRequestError("This production has an unsupported record format. Use the maintenance cleanup.", 409);
    }
    const currentMeta = parsed[0]._studio && typeof parsed[0]._studio === "object" ? parsed[0]._studio : {};
    const marker = typeof currentMeta.discard_requested_at === "string" && currentMeta.discard_requested_at
      ? currentMeta.discard_requested_at : new Date().toISOString();
    if (!currentMeta.discard_requested_at) {
      const markedScenes = [{ ...parsed[0], _studio: { ...currentMeta, discard_requested_at: marker,
        publish_requested: false } }, ...parsed.slice(1)];
      const { data, error } = await client.from("videos").update({
        scenes: typeof row.scenes === "string" ? JSON.stringify(markedScenes) : markedScenes,
      }).eq("id", id).eq("status", row.status)
        .is("scenes->0->_studio->>discard_requested_at", null).select("*").maybeSingle();
      if (error) throw error;
      if (!data) throw new StudioRequestError("The production changed. Refresh and try again.", 409);
      row = data;
    }

    const videoBucket = process.env.SUPABASE_VIDEO_BUCKET || "renders";
    const thumbnailBucket = process.env.SUPABASE_THUMBNAIL_BUCKET || "thumbnails";
    const knownFiles = [
      { bucket: videoBucket, name: id + ".mp4" },
      { bucket: videoBucket, name: id + "-pace-v1.mp4" },
      { bucket: thumbnailBucket, name: id + ".jpg" },
    ];
    const buckets = [...new Set([videoBucket, thumbnailBucket, "studio-masters"])];
    let objectsDeleted = 0;
    let bytesReclaimed = 0;
    const associated = (bucket: string, name: string) =>
      knownFiles.some(file => file.bucket === bucket && file.name === name) ||
      (bucket === "studio-masters" && name.startsWith(id + "-") && /^[-a-z0-9.]+$/i.test(name));
    async function listAssociated(bucket: string) {
      const results: { name: string; size: number }[] = [];
      const storage = client.storage.from(bucket);
      for (let offset = 0; offset < 10000; offset += 100) {
        const { data, error } = await storage.list("", { search: id, limit: 100, offset });
        if (error) {
          if (bucket === "studio-masters" && (String(error.statusCode) === "404" || /bucket not found/i.test(error.message))) return [];
          throw error;
        }
        const entries = data || [];
        for (const item of entries) {
          if (item.id && associated(bucket, item.name)) {
            results.push({ name: item.name, size: Number(item.metadata?.size) || 0 });
          }
        }
        if (entries.length < 100) break;
      }
      return results;
    }
    for (const bucket of buckets) {
      const objects = await listAssociated(bucket);
      if (!objects.length) continue;
      const { error } = await client.storage.from(bucket).remove(objects.map(item => item.name));
      if (error) throw error;
      const remaining = await listAssociated(bucket);
      if (remaining.length) throw new StudioRequestError(
        "Some Studio files remain in storage. Repeat deletion to finish safely.", 503);
      objectsDeleted += objects.length;
      bytesReclaimed += objects.reduce((sum, item) => sum + item.size, 0);
    }

    const { data: deleted, error: deleteError } = await client.from("videos").delete()
      .eq("id", id).eq("status", row.status)
      .eq("scenes->0->_studio->>discard_requested_at", marker).select("id").maybeSingle();
    if (deleteError) throw deleteError;
    if (!deleted) throw new StudioRequestError("Media was removed, but the record changed. Retry to complete deletion.", 409);
    return NextResponse.json({ ok: true, deletedId: id, objectsDeleted, bytesReclaimed,
      message: "Video and associated Supabase media removed. Any YouTube upload is unchanged." },
      { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    if (statusFor(error) >= 500) console.error("Studio delete failed", error);
    return NextResponse.json(errorResponse(error), { status: statusFor(error),
      headers: { "Cache-Control": "no-store" } });
  }
}
