import type { Draft, StudioVideo } from "./studio.ts";

const existingPrompt = /\b(?:like (?:this|the) video|hit (?:the )?like|(?:like and |and )subscribe|subscribe (?:to (?:this|the|our|my) channel|to LenxiQ|for more)|give (?:this|it) a like)\b/i;
const CTA_FALLBACK = "If this breakdown helped, give the video a like and subscribe to LenxiQ for more stories like this.";

export function addViewerPrompt(draft: Draft, suggestion?: unknown): Draft {
  if (draft.duration_seconds < 300 || draft.scenes.length < 6 || draft.scenes.some(({ narration }) => existingPrompt.test(narration))) return draft;
  const proposed = typeof suggestion === "string" ? suggestion.replace(/\s+/g, " ").trim() : "";
  const narration = proposed && proposed.split(" ").length <= 25 && /\b(?:hit|give|leave|tap|drop|video a)\b[^.!?]*\blike\b|\blike (?:this|the) video\b/i.test(proposed)
    && /\bsubscribe\b/i.test(proposed) && !/[<>]|https?:|\d/.test(proposed) ? proposed : CTA_FALLBACK;
  const boundary = draft.scenes.findIndex((scene, index) => index >= draft.scenes.length * .25 && index <= draft.scenes.length * .60 && scene.chapter_title);
  const at = boundary > 0 ? boundary : Math.round(draft.scenes.length * .42);
  if (draft.scenes.length >= 60) {
    return { ...draft, scenes: draft.scenes.map((scene, index) => index === at - 1
      ? { ...scene, narration: scene.narration + " " + narration } : scene) };
  }
  const prompt = { scene_number: 0, narration, search_queries: draft.scenes[at - 1].search_queries, source_ids: [] };
  const scenes = [...draft.scenes.slice(0, at), prompt, ...draft.scenes.slice(at)]
    .map((scene, index) => ({ ...scene, scene_number: index + 1 }));
  return { ...draft, scenes };
}

export function formatYouTubeTime(seconds: number) {
  const total = Math.floor(seconds);
  const minutes = Math.floor(total / 60);
  return [String(minutes).padStart(2, "0"), String(total % 60).padStart(2, "0")].join(":");
}

export function teaserDraft(video: StudioVideo, firstScene: number): Draft | undefined {
  if (!video.youtube_url || ((video.actual_duration_seconds ?? 0) < 300 && video.duration_seconds < 300) || !Number.isInteger(firstScene) || firstScene < 1) return;
  if (!video.scenes[firstScene - 1]?.narration) return;
  const scenes = [];
  let words = 0;
  for (const scene of video.scenes.slice(firstScene - 1)) {
    if (!scene.narration || existingPrompt.test(scene.narration)) break;
    const next = scene.narration.split(/\s+/).length;
    if (words + next > 155) break;
    scenes.push({ ...scene, chapter_title: undefined, scene_number: scenes.length + 1 });
    words += next;
    if (words >= 116) break;
  }
  if (words < 65) return;
  scenes.push({ scene_number: scenes.length + 1, narration: "For the full breakdown, watch the longer video on LenxiQ.",
    search_queries: scenes[scenes.length - 1].search_queries, source_ids: [] });
  const ids = new Set(scenes.flatMap((scene) => scene.source_ids ?? []));
  const sources = video.sources?.filter(({ id }) => id != null && ids.has(id));
  const links = sources?.map(({ publisher, url }) => [publisher, url].filter(Boolean).join(" — ")).join("\n");
  return {
    title: video.title.slice(0, 100), format: "9:16", duration_seconds: 60, tags: video.tags,
    thumbnail_title: video.thumbnail_title, sources, scenes,
    description: `An excerpt from ${video.title}\n\nFull video\n${video.youtube_url}` + (links ? "\n\nSources\n" + links : ""),
  };
}

export function communityPostFor(video: StudioVideo) {
  const hook = video.community_post || `What would you like me to explore next after watching “${video.title}”?`;
  return hook + (video.youtube_url ? "\n\n" + video.youtube_url : "");
}

export function parseLagosPublishTime(value: string) {
  if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d$/.test(value)) return undefined;
  const date = new Date(value + ":00+01:00");
  if (!Number.isFinite(date.getTime())) return undefined;
  const local = new Date(date.getTime() + 3600000).toISOString().slice(0, 16);
  return local === value ? date.toISOString() : undefined;
}

export function formatLagosPublishInput(value?: string) {
  const timestamp = Date.parse(value || "");
  return Number.isFinite(timestamp) ? new Date(timestamp + 3600000).toISOString().slice(0, 16) : "";
}
