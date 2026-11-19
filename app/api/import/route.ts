import { NextResponse } from "next/server";
import { enrichImportedDraft } from "@/src/lib/studio-research";
import { normalizeDraft } from "@/src/lib/studio";
import { MAX_VIDEO_SCENES } from "@/src/lib/studio-limits";
import { studioContext, requestObject, errorResponse, statusFor, StudioRequestError } from "@/src/lib/studio-server";

export const dynamic = "force-dynamic";
export const maxDuration = 300;

export async function POST(request: Request) {
  try {
    await studioContext(request);
    const body = await requestObject(request);
    const raw = body.draft as {scenes?: unknown[]} | undefined;
    if(!raw || !Array.isArray(raw.scenes) || !raw.scenes.length || raw.scenes.length > MAX_VIDEO_SCENES)
      throw new StudioRequestError("Invalid imported scenes.",400);
    if(JSON.stringify(raw).length > 600000)throw new StudioRequestError("Imported draft is too large.",400);
    const draft=normalizeDraft(raw);
    const notes=Array.isArray(body.notes)?body.notes.filter((n): n is string=>typeof n==="string").slice(0,20):[];
    // Keep original content, including exact narration and optional chapter metadata.
    if(draft.scenes.length !== raw.scenes.length)throw new StudioRequestError("Import scenes could not be preserved.",400);
    return NextResponse.json({ok:true,...await enrichImportedDraft(draft,notes)});
  } catch(error) {
    return NextResponse.json(errorResponse(error),{status:statusFor(error)});
  }
}
