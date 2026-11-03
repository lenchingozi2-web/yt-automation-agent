import { NextResponse } from "next/server";
import { normalizeVideo } from "@/src/lib/studio";
import { errorResponse, quotaOf, statusFor, studioClient } from "@/src/lib/studio-server";
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
    return NextResponse.json({ ok: true, videos, quota }, { headers: { "Cache-Control": "no-store, max-age=0" } });
  } catch (error) {
    if (statusFor(error) !== 401) console.error("Studio queue fetch failed", error);
    return NextResponse.json(errorResponse(error), { status: statusFor(error), headers: { "Cache-Control": "no-store" } });
  }
}
