import { NextResponse } from "next/server";
import { dispatchStudioAction, errorResponse, statusFor, studioClient, studioDispatchAvailable, StudioRequestError } from "@/src/lib/studio-server";
export const dynamic = "force-dynamic";
type BucketInfo = { name: string; bytes: number; count: number; available: boolean; partial: boolean; error?: string };
export async function GET(request: Request) {
  try {
    const client = await studioClient(request);
    const names = [process.env.SUPABASE_VIDEO_BUCKET || "renders", process.env.SUPABASE_THUMBNAIL_BUCKET || "thumbnails", "studio-masters"];
    const buckets: BucketInfo[] = [];
    for (const name of names) {
      let bytes = 0, count = 0, partial = false;
      let unavailable = false, absent = false;
      const storage = client.storage.from(name);
      // Studio stores flat object names in these three buckets.
      for (let offset = 0; offset < 20000; offset += 200) {
        const { data, error } = await storage.list("", { limit: 200, offset, sortBy: { column: "name", order: "asc" } });
        if (error) {
          if (name === "studio-masters" && offset === 0 && (String(error.statusCode) === "404" || /bucket not found/i.test(error.message))) absent = true;
          else { unavailable = true; partial = offset > 0; }
          break;
        }
        const entries = data || [];
        for (const item of entries) {
          if (!item.id) { partial = true; continue; }
          const size = Number(item.metadata?.size);
          if (item.metadata?.size == null || !Number.isFinite(size) || size < 0) partial = true;
          else bytes += size;
          count++;
        }
        if (entries.length < 200) break;
        if (offset === 19800) partial = true;
      }
      if (absent) continue;
      buckets.push({ name, bytes, count, available: !unavailable || count > 0, partial,
        ...(unavailable ? { error: "Some storage objects could not be listed." } : {}) });
    }
    return NextResponse.json({ ok: true, buckets, retention: {
      published_preview_hours: 12, published_thumbnail_days: 90, unpublished: "Retained until safely backed up or manually removed.",
      metadata: "Retained indefinitely.", cleanup: "Scheduled daily at midnight WAT.",
    }, immediate_dispatch_available: studioDispatchAvailable() },
      { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return NextResponse.json(errorResponse(error), { status: statusFor(error) });
  }
}


export async function POST(request: Request) {
  try {
    await studioClient(request);
    if (!await dispatchStudioAction("studio_maintenance.yml", { dry_run: "false" })) {
      throw new StudioRequestError("Could not start cleanup. Scheduled retention will try again.", 503);
    }
    return NextResponse.json({ ok: true, message: "Storage cleanup queued. Refresh usage after the run finishes." }, { status: 202 });
  } catch (error) {
    return NextResponse.json(errorResponse(error), { status: statusFor(error) });
  }
}
