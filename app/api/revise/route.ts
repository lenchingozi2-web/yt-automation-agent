import { NextResponse } from "next/server";
import { footageSearches, normalizeDraft, normalizeVideo, revisionMode, revisionTarget } from "@/src/lib/studio";
import { rewriteScene } from "@/src/lib/studio-research";
import { dispatchStudioWorker, errorResponse, findVideo, jobIdOf, quotaOf, requestObject, safeInsertVideo, statusFor, studioContext, StudioRequestError } from "@/src/lib/studio-server";

export const dynamic = "force-dynamic";
export const maxDuration = 140;

export async function POST(request: Request) {
  try {
    const { client, userId } = await studioContext(request);
    const body = await requestObject(request);
    const id = jobIdOf(body.id);
    const requestId = jobIdOf(body.requestId);
    if (!id || !requestId || requestId === id) throw new StudioRequestError("Choose a video and start a new revision.", 400);
    const [original, existing] = await Promise.all([findVideo(client, id), findVideo(client, requestId)]);
    if (!original || (original.user_id && original.user_id !== userId)) throw new StudioRequestError("Video not found.", 404);
    if (!["completed", "failed"].includes(original.status)) throw new StudioRequestError("Wait for this render to finish before revising it.", 409);
    const video = normalizeVideo(original);
    if (existing) {
      const saved = normalizeVideo(existing);
      if (existing.user_id !== userId || saved.revision?.parent_id !== id) throw new StudioRequestError("Start a new revision.", 409);
      return NextResponse.json({ ok: true, message: "This revision is already queued.", video: saved });
    }
    const draft = normalizeDraft(original);
    const direction = typeof body.direction === "string" ? body.direction.trim() : "";
    const narration = typeof body.narration === "string" ? body.narration.trim() : "";
    const manualQueries = typeof body.footage === "string" ? body.footage.trim() : "";
    if (direction.length > 1600 || narration.length > 800 || manualQueries.length > 350) throw new StudioRequestError("Shorten your revision instructions.", 400);
    const sceneNumber = revisionTarget(body.scene, direction, draft.scenes.length);
    if (!sceneNumber) throw new StudioRequestError("Choose one scene to revise. Its number must match any scene named in the instructions.", 400);
    const mode = narration ? "rewrite" : manualQueries ? "visual" : revisionMode(body.mode, direction);
    if (!direction && !narration && !manualQueries && mode !== "audio") throw new StudioRequestError("Describe what you want to change.", 400);
    const originalMedia = Boolean(video.video_url && video.scene_times?.length === draft.scenes.length);
    if (!originalMedia) throw new StudioRequestError("This video’s source preview or saved scene timings are unavailable for a targeted revision.", 409);
    const quota = await quotaOf(client);
    if (quota.used >= quota.limit) throw new StudioRequestError("Today’s render allowance is full. It resets at midnight WAT.", 429);
    const scene = draft.scenes[sceneNumber - 1];
    const queries = manualQueries ? [...new Set(manualQueries.split(",").map((term) => term.trim()).filter(Boolean))].slice(0, 5) : undefined;
    if (manualQueries && !queries?.length) throw new StudioRequestError("Enter at least one footage search term.", 400);
    if (mode === "visual") {
      scene.search_queries = queries || footageSearches(scene, direction);
    } else if (mode === "rewrite") {
      const result = await rewriteScene(draft, sceneNumber, direction, narration || undefined, queries);
      const all = [...(draft.sources || [])];
      const ids = new Map<number | undefined, number>();
      let nextId = Math.max(0, ...all.map((source) => source.id || 0)) + 1;
      for (const source of result.sources.filter((source) => result.scene.source_ids?.includes(source.id || 0))) {
        const known = all.find((item) => item.url === source.url && item.id);
        const id = known?.id || nextId++;
        if (!known) all.push({ ...source, id });
        ids.set(source.id, id);
      }
      draft.sources = all;
      draft.scenes[sceneNumber - 1] = { ...result.scene, source_ids: result.scene.source_ids?.map((id) => ids.get(id)!) };
    }
    let source = video;
    const visited = new Set([id]);
    while (source.revision && !source.revision.root_id && !visited.has(source.revision.parent_id)) {
      visited.add(source.revision.parent_id);
      const parent = await findVideo(client, source.revision.parent_id);
      if (!parent || (parent.user_id && parent.user_id !== userId)) break;
      source = normalizeVideo(parent);
    }
    const rootId = source.revision?.root_id || source.revision?.parent_id || source.id;
    const saved = await safeInsertVideo(client, draft, userId, requestId, {
      parent_id: id, root_id: rootId, scene_number: sceneNumber, mode, original_media: originalMedia,
    });
    const dispatch = await dispatchStudioWorker();
    return NextResponse.json({ ok: true, video: normalizeVideo(saved), message: "Scene " + sceneNumber + " revision queued under the original video.", note: dispatch.note });
  } catch (error) {
    if (statusFor(error) >= 500) console.error("Studio revision failed", error);
    return NextResponse.json(errorResponse(error), { status: statusFor(error), headers: { "Cache-Control": "no-store" } });
  }
}
