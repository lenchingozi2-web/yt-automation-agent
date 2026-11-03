import { NextResponse } from "next/server";
import { normalizeVideo, toSafeMessage } from "@/src/lib/studio";
import { dispatchStudioWorker, errorResponse, safeInsertVideo, statusFor, studioClient } from "@/src/lib/studio-server";
export const dynamic = "force-dynamic";
export async function POST(request: Request) {
  try {
    const client = await studioClient(request);
    const body = await request.json() as Record<string, unknown>;
    let saved: Record<string, unknown>;
    if (typeof body.id === "string" && body.id) {
      const { data, error } = await client.from("videos").update({ status: "pending" }).eq("id", body.id).in("status", ["failed", "pending"]).select("*").single();
      if (error || !data) throw new Error(toSafeMessage(error, "Only a pending or failed video can be retried."));
      saved = data as Record<string, unknown>;
    } else {
      saved = await safeInsertVideo(client, body);
    }
    const dispatch = await dispatchStudioWorker();
    return NextResponse.json({ ok: true, message: "Video queued for rendering!", video: normalizeVideo(saved), workerDispatched: dispatch.dispatched, note: dispatch.note }, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    console.error("Studio render queue failed", error);
    return NextResponse.json(errorResponse(error), { status: statusFor(error) });
  }
}
