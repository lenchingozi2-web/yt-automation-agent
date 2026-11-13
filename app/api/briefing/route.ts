import { NextResponse } from "next/server";
import { getBriefing } from "@/src/lib/studio-briefing";
import { dispatchStudioAction, errorResponse, statusFor, studioClient, StudioRequestError } from "@/src/lib/studio-server";

export const dynamic = "force-dynamic";

const todayInLagos = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Lagos", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());

export async function GET(request: Request) {
  try {
    const ideas = await getBriefing(await studioClient(request));
    const today = todayInLagos();
    return NextResponse.json({ ok: true, ideas, today }, { headers: { "Cache-Control": "private, no-store" } });
  } catch (error) {
    return NextResponse.json(errorResponse(error), { status: statusFor(error) });
  }
}


export async function POST(request: Request) {
  try {
    const ideas = await getBriefing(await studioClient(request));
    if (ideas[0]?.briefing_date === todayInLagos()) return NextResponse.json({ ok: true, message: "Today’s briefing is ready. Refresh to see it." });
    if (!await dispatchStudioAction("nightly_research.yml", { preview_only: "false" })) {
      throw new StudioRequestError("Could not start briefing research. Try again later.", 503);
    }
    return NextResponse.json({ ok: true, message: "Today’s research is queued. Refresh in a few minutes." }, { status: 202 });
  } catch (error) {
    return NextResponse.json(errorResponse(error), { status: statusFor(error) });
  }
}
