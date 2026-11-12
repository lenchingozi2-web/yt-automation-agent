export type Scene = { scene_number: number; narration: string; search_queries: string[]; estimated_duration?: number; source_ids?: number[] };
export type Draft = { title: string; description: string; tags: string[]; format: "16:9" | "9:16"; duration_seconds: number; scenes: Scene[]; thumbnail_title?: string; sources?: ResearchSource[] };
export type ResearchSource = { id?: number; title: string; url: string; publisher: string };
export type RevisionMode = "audio" | "visual" | "rewrite";
export type VideoRevision = { parent_id: string; scene_number: number; mode: RevisionMode; original_media: boolean };
export type StudioVideo = Draft & { id: string; status: string; video_url?: string; youtube_url?: string; youtube_id?: string; thumbnail_url?: string; preview_expired_at?: string; error_message?: string; progress_note?: string; publish_status?: string; publish_error?: string; publish_approved_at?: string; thumbnail_publish_status?: string; actual_duration_seconds?: number; render_version?: string; automation?: string; created_at?: string; revision?: VideoRevision; scene_times?: { scene_number: number; start: number }[]; };
export function toSafeMessage(input: unknown, fallback = "Action completed."): string {
  const seen = new WeakSet<object>();
  function read(value: unknown, depth: number): string {
    if (typeof value === "string") { const s = value.trim(); return s && s !== "[object Object]" ? s : ""; }
    if (value instanceof Error) return read(value.message, depth + 1);
    if (value == null || depth > 4) return "";
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    if (typeof value === "object") {
      if (seen.has(value)) return "";
      seen.add(value);
      const obj = value as Record<string, unknown>;
      for (const key of ["message", "details", "hint", "error", "status", "code"]) {
        const m = read(obj[key], depth + 1); if (m) return m;
      }
    }
    return "";
  }
  return read(input, 0) || fallback;
}
function objectOf(value: unknown): Record<string, unknown> {
  if (typeof value === "string") { try { return objectOf(JSON.parse(value) as unknown); } catch { return {}; } }
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
export function revisionTarget(scene: unknown, direction: string, sceneCount: number) {
  const mentions = [...new Set([...direction.matchAll(/\bscene\s*(?:#|number\s+)?(\d+)\b/gi)].map((match) => Number(match[1])))];
  const selected = scene == null ? mentions[0] : Number(scene);
  return Number.isInteger(selected) && selected >= 1 && selected <= sceneCount &&
    mentions.every((number) => number === selected) ? selected : undefined;
}

export function revisionMode(value: unknown, direction: string): RevisionMode {
  if (value === "audio" || value === "visual" || value === "rewrite") return value;
  const rewrite = /\b(?:rewrit\w*|rephras\w*|fact.?check|correct (?:the )?(?:claim|script|narration)|(?:change|edit|replace|update|fix) (?:the )?(?:words|wording|script|narration|claim|dialogue)|(?:script|narration) (?:says|mentions|is wrong))\b/i;
  if (rewrite.test(direction)) return "rewrite";
  if (/\b(?:footage|visuals?|align\w*|shots?|(?:replace|change) (?:the )?scene|clips?|images?|b-roll)\b/i.test(direction)) return "visual";
  if (/\b(?:voice(?:over)?|audio|silent|sound|crackl?\w*|narration is missing)\b/i.test(direction)) return "audio";
  return "rewrite";
}

export function footageSearches(scene: Scene, direction: string): string[] {
  const subject = /\b(?:show(?:ing)?|use|feature|depict(?:ing)?|cut to|replace\s+(?:the\s+)?(?:footage|visuals?|shots?|clips?|(?:scene\s+\d+))\s+with)\s+([^.!?\n]+)/i.exec(direction)?.[1]
    ?.replace(/^\s*(?:an?|the)\s+/i, "")
    .replace(/\b(?:instead|to (?:match|align)|that (?:matches|aligns))\b.*$/i, "").trim();
  const visualSubjects = [
    [/\b(?:blood\s?spot|heel prick|blood sample|blood test)\b/i, "blood sample laboratory"],
    [/\b(?:newborn|infant|baby)\b/i, "newborn hospital"],
    [/\b(?:genetic|dna|methylation)\b/i, "laboratory pipette"],
    [/\b(?:counsel\w*|parents|family|families)\b/i, "doctor consultation"],
    [/\b(?:microscop\w*|specimen)\b/i, "laboratory microscope"],
  ] as const;
  const relevant = visualSubjects.flatMap(([pattern, query]) => pattern.test(scene.narration) ? [query] : []);
  const terms = [
    subject?.split(/\s+/).slice(0, 6).join(" "),
    ...relevant.slice(0, 2),
    ...scene.search_queries,
  ].filter((term): term is string => !!term && !/^(?:more |better |different )?(?:relevant|aligned|matching|appropriate|different|better|new|related)(?:\s+(?:footage|visuals?|clips?))?$/i.test(term));
  return [...new Set(terms.map((term) => term.trim().toLowerCase()))].slice(0, 5);
}

const stringOf = (v: unknown, fallback = "") => typeof v === "string" ? v : fallback;
export function normalizeDraft(value: unknown): Draft {
  const outer = objectOf(value);
  const packed = objectOf(outer.script);
  const metadata = objectOf(outer.metadata);
  let rawScenes = outer.scenes;
  if (typeof rawScenes === "string") { try { rawScenes = JSON.parse(rawScenes) as unknown; } catch { rawScenes = undefined; } }
  const firstRaw = Array.isArray(rawScenes) ? objectOf(rawScenes[0]) : {};
  const source = { ...objectOf(firstRaw._studio), ...metadata, ...packed, ...Object.fromEntries(Object.entries(outer).filter(([,v]) => v != null && v !== "")) };
  const nested = objectOf(rawScenes || source.scenes);
  const entries = Array.isArray(rawScenes) ? rawScenes : Array.isArray(source.scenes) ? source.scenes : Array.isArray(nested.scenes) ? nested.scenes : Array.isArray(outer.script) ? outer.script : [];
  const scenes: Scene[] = entries.map((raw: unknown, index: number) => {
    const r = objectOf(raw);
    return { scene_number: index + 1, narration: stringOf(r.narration || r.voiceover || r.text).trim(),
      search_queries: (Array.isArray(r.search_queries) ? r.search_queries : stringOf(r.search_queries || r.keywords).split(",")).filter((q: unknown): q is string => typeof q === "string" && !!q.trim()).map(q => q.trim()).slice(0, 5),
      estimated_duration: Number(r.estimated_duration) || 15,
      source_ids: Array.isArray(r.source_ids) ? [...new Set(r.source_ids.filter((id: unknown): id is number => Number.isSafeInteger(id) && Number(id) > 0))].slice(0, 12) : [] };
  });
  const first = entries.length > 0 ? objectOf(entries[0]) : {};
  const ratio = source.aspect_ratio || source.aspectRatio || source.format || nested.aspect_ratio || nested.format || first.format;
  const minutes = Number(source.durationMinutes);
  const duration = Number(source.duration_seconds || source.duration || nested.duration_seconds || nested.duration || (minutes > 0 ? minutes * 60 : 0));
  const sources = (Array.isArray(source.sources) ? source.sources : []).flatMap((item) => {
    const raw = objectOf(item);
    const url = stringOf(raw.url);
    const title = stringOf(raw.title).slice(0, 240);
    if (!title || !/^https:\/\/(?:pubmed\.ncbi\.nlm\.nih\.gov\/\d+\/?|en\.wikipedia\.org\/\?curid=\d+)$/.test(url)) return [];
    return [{ id: Number.isSafeInteger(raw.id) ? Number(raw.id) : undefined, url, title, publisher: stringOf(raw.publisher).slice(0, 120) }];
  }).slice(0, 120);
  return { title: stringOf(source.title, "Untitled production"), description: stringOf(source.description || nested.description),
    tags: (Array.isArray(source.tags) ? source.tags : Array.isArray(nested.tags) ? nested.tags : stringOf(source.tags).split(",")).filter((t: unknown): t is string => typeof t === "string" && !!t.trim()),
    format: ratio === "9:16" ? "9:16" : "16:9",
    duration_seconds: Number.isFinite(duration) && duration > 0 ? Math.round(duration) : 180, scenes, sources,
    thumbnail_title: stringOf(source.thumbnail_title || nested.thumbnail_title || source.thumbnailTitle || source.title, "New story") };
}
export function normalizeVideo(raw: unknown): StudioVideo {
  const row = objectOf(raw);
  const draft = normalizeDraft(raw);
  const packed = objectOf(row.script);
  let storedScenes = row.scenes;
  if (typeof storedScenes === "string") { try { storedScenes = JSON.parse(storedScenes) as unknown; } catch { storedScenes = undefined; } }
  const firstScene = Array.isArray(storedScenes) ? objectOf(storedScenes[0]) : {};
  const meta = { ...objectOf(firstScene._studio), ...objectOf(storedScenes), ...objectOf(packed.metadata), ...objectOf(row.metadata), ...packed, ...Object.fromEntries(Object.entries(row).filter(([,v]) => v != null && v !== "")) };
  const youtubeId = stringOf(meta.youtube_id || meta.youtube_video_id);
  const revision = objectOf(meta.revision);
  const revisionDetails: VideoRevision | undefined = typeof revision.parent_id === "string" && Number.isInteger(revision.scene_number) &&
    (revision.mode === "audio" || revision.mode === "visual" || revision.mode === "rewrite") ? {
      parent_id: revision.parent_id, scene_number: Number(revision.scene_number), mode: revision.mode,
      original_media: revision.original_media === true,
    } : undefined;
  const timeline = meta.scene_times || meta.scene_timeline;
  const sceneTimes = Array.isArray(timeline) ? timeline.flatMap((entry) => {
    const { scene_number, start } = objectOf(entry);
    return Number.isInteger(scene_number) && typeof start === "number" && start >= 0 ? [{ scene_number: Number(scene_number), start }] : [];
  }).filter((entry, i, items) => i === 0 || items[i - 1].scene_number !== entry.scene_number) : [];
  return { ...draft, revision: revisionDetails, scene_times: sceneTimes, id: stringOf(meta.id), status: stringOf(meta.status, "pending").toLowerCase(),
    video_url: meta.preview_expired_at ? "" : stringOf(meta.video_url || meta.storage_url),
    youtube_url: stringOf(meta.youtube_url) || (youtubeId ? "https://www.youtube.com/watch?v=" + encodeURIComponent(youtubeId) : ""),
    youtube_id: youtubeId, created_at: stringOf(meta.created_at),
    thumbnail_url: stringOf(meta.thumbnail_url), preview_expired_at: stringOf(meta.preview_expired_at),
    error_message: stringOf(meta.error_message || meta.error),
    progress_note: stringOf(meta.progress_note || meta.progress || meta.note),
    publish_status: stringOf(meta.publish_status), publish_error: stringOf(meta.publish_error),
    publish_approved_at: stringOf(meta.publish_approved_at),
    thumbnail_publish_status: stringOf(meta.thumbnail_publish_status),
    actual_duration_seconds: Number(meta.actual_duration_seconds) || undefined,
    render_version: stringOf(meta.render_version), automation: stringOf(meta.automation) };
}
