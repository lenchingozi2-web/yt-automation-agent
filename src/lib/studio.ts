export type Scene = { scene_number: number; narration: string; search_queries: string[]; estimated_duration?: number };
export type Draft = { title: string; description: string; tags: string[]; format: "16:9" | "9:16"; duration_seconds: number; scenes: Scene[]; thumbnail_title?: string };
export type StudioVideo = Draft & { id: string; status: string; video_url?: string; youtube_url?: string; youtube_id?: string; thumbnail_url?: string; preview_expired_at?: string; error_message?: string; progress_note?: string; publish_status?: string; publish_error?: string; publish_approved_at?: string; thumbnail_publish_status?: string; actual_duration_seconds?: number; render_version?: string; automation?: string; created_at?: string };
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
const stringOf = (v: unknown, fallback = "") => typeof v === "string" ? v : fallback;
export function normalizeDraft(value: unknown): Draft {
  const outer = objectOf(value);
  const packed = objectOf(outer.script);
  const metadata = objectOf(outer.metadata);
  const firstRaw = Array.isArray(outer.scenes) ? objectOf(outer.scenes[0]) : {};
  const source = { ...objectOf(firstRaw._studio), ...metadata, ...packed, ...Object.fromEntries(Object.entries(outer).filter(([,v]) => v != null && v !== "")) };
  const nested = objectOf(source.scenes);
  const entries = Array.isArray(source.scenes) ? source.scenes : Array.isArray(nested.scenes) ? nested.scenes : Array.isArray(outer.script) ? outer.script : [];
  const scenes: Scene[] = entries.map((raw: unknown, index: number) => {
    const r = objectOf(raw);
    return { scene_number: index + 1, narration: stringOf(r.narration || r.voiceover || r.text).trim(),
      search_queries: (Array.isArray(r.search_queries) ? r.search_queries : stringOf(r.keywords).split(",")).filter((q: unknown): q is string => typeof q === "string" && !!q.trim()).map(q => q.trim()).slice(0, 5),
      estimated_duration: Number(r.estimated_duration) || 15 };
  });
  const first = entries.length > 0 ? objectOf(entries[0]) : {};
  const ratio = source.aspect_ratio || source.aspectRatio || source.format || nested.aspect_ratio || nested.format || first.format;
  const minutes = Number(source.durationMinutes);
  const duration = Number(source.duration_seconds || source.duration || nested.duration_seconds || nested.duration || (minutes > 0 ? minutes * 60 : 0));
  return { title: stringOf(source.title, "Untitled production"), description: stringOf(source.description || nested.description),
    tags: (Array.isArray(source.tags) ? source.tags : Array.isArray(nested.tags) ? nested.tags : stringOf(source.tags).split(",")).filter((t: unknown): t is string => typeof t === "string" && !!t.trim()),
    format: ratio === "9:16" ? "9:16" : "16:9",
    duration_seconds: Number.isFinite(duration) && duration > 0 ? Math.round(duration) : 180, scenes,
    thumbnail_title: stringOf(source.thumbnail_title || nested.thumbnail_title || source.thumbnailTitle || source.title, "New story") };
}
export function normalizeVideo(raw: unknown): StudioVideo {
  const row = objectOf(raw);
  const draft = normalizeDraft(raw);
  const packed = objectOf(row.script);
  const firstScene = Array.isArray(row.scenes) && row.scenes.length > 0 ? objectOf(row.scenes[0]) : {};
  const meta = { ...objectOf(firstScene._studio), ...objectOf(row.scenes), ...objectOf(packed.metadata), ...objectOf(row.metadata), ...packed, ...Object.fromEntries(Object.entries(row).filter(([,v]) => v != null && v !== "")) };
  const youtubeId = stringOf(meta.youtube_id || meta.youtube_video_id);
  return { ...draft, id: stringOf(meta.id), status: stringOf(meta.status, "pending").toLowerCase(),
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
