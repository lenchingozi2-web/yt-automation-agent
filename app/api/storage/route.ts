import { NextResponse } from "next/server";
import { errorResponse, statusFor, studioClient } from "@/src/lib/studio-server";
export const dynamic = "force-dynamic";
type BucketInfo = { name: string; bytes: number; count: number; available: boolean; partial: boolean; error?: string };
export async function GET(request: Request) {
  try {
    const client = await studioClient(request);
    const names = [process.env.SUPABASE_VIDEO_BUCKET || "renders", "thumbnails"];
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
    }, immediate_dispatch_available: !!(process.env.GITHUB_TOKEN || process.env.GH_PAT || process.env.GH_TOKEN) },
      { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return NextResponse.json(errorResponse(error), { status: statusFor(error) });
  }
}
