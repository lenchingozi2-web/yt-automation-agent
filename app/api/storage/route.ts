import { NextResponse } from "next/server";
import { dispatchStudioAction, errorResponse, statusFor, studioClient, studioDispatchAvailable, StudioRequestError } from "@/src/lib/studio-server";
export const dynamic = "force-dynamic";
type BucketInfo = { name: string; bytes: number; count: number; available: boolean; partial: boolean; error?: string };
export async function GET(request: Request) {
  try {
    const client = await studioClient(request);
    const names = [process.env.SUPABASE_VIDEO_BUCKET || "renders", process.env.SUPABASE_THUMBNAIL_BUCKET || "thumbnails"];
    const buckets: BucketInfo[] = [];
    for (const name of names) {
      // Metadata listing is used for a rough current size; Supabase Billing is authoritative.
      const { data, error } = await client.storage.from(name).list("", { limit: 1000, offset: 0 });
      if (error) {
        buckets.push({ name, bytes: 0, count: 0, partial: false, available: false,
          error: "Bucket unavailable or storage permission denied." });
        continue;
      }
      const files = (data || []).filter(item => item.id);
      buckets.push({ name, bytes: files.reduce((sum, item) => sum + (Number(item.metadata?.size) || 0), 0),
        count: files.length, available: true, partial: files.length >= 1000 });
    }
    return NextResponse.json({ ok: true, buckets, retention: {
      published_preview_days: 7, published_thumbnail_days: 90, unpublished: "Retained until safely backed up or manually removed.",
      metadata: "Retained indefinitely.", cleanup: "Scheduled daily through GitHub Actions.",
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
