import { NextResponse } from "next/server";
import { getBriefing } from "@/src/lib/studio-briefing";
import { errorResponse, statusFor, studioClient } from "@/src/lib/studio-server";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const ideas = await getBriefing(await studioClient(request));
    const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Lagos", dateStyle: "short" }).format(new Date());
    return NextResponse.json({ ok: true, ideas, today }, { headers: { "Cache-Control": "private, no-store" } });
  } catch (error) {
    return NextResponse.json(errorResponse(error), { status: statusFor(error) });
  }
}
