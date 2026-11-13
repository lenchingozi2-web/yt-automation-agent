import { useDeferredValue, useMemo, useRef, useState } from "react";
import Image from "next/image";
import { ArrowUpRight, Clapperboard, Download, Film, LoaderCircle, Play, RefreshCw, Search, X } from "lucide-react";
import { canRetryYouTubeThumbnail, groupProductions, youtubePublishAction, youtubeStudioUrl, type RevisionMode, type StudioVideo } from "@/src/lib/studio";
import { Modal, Notice, type Feedback } from "./studio-shell";

const FILTERS = ["All", "In progress", "Ready", "Needs attention"] as const;
type Filter = typeof FILTERS[number];
const PAGE_SIZE = 8;
const dateFormat = new Intl.DateTimeFormat("en-NG", { day: "numeric", month: "short", timeZone: "Africa/Lagos" });
const timeFormat = new Intl.DateTimeFormat("en-NG", { hour: "numeric", minute: "2-digit", timeZone: "Africa/Lagos" });

function clock(value?: string) {
  return value && Number.isFinite(Date.parse(value)) ? timeFormat.format(new Date(value)) + " WAT" : "";
}

function renderTime(video: StudioVideo) {
  const seconds = Math.round((Date.parse(video.completed_at ?? "") - Date.parse(video.render_started_at ?? "")) / 1000);
  if (!Number.isFinite(seconds) || seconds < 1) return "";
  return seconds < 60 ? seconds + " sec" : Math.floor(seconds / 60) + " min " + seconds % 60 + " sec";
}

function videoState(video: StudioVideo) {
  if (video.status === "failed") return { label: video.revision ? "Revision failed" : "Render failed", tone: "error", filter: "Needs attention" };
  if (video.status === "rendering") return { label: video.revision ? "Rendering revision" : "Rendering", tone: "active", filter: "In progress" };
  if (video.status === "pending") return { label: video.revision ? "Revision queued" : "Queued", tone: "muted", filter: "In progress" };
  if (video.youtube_url && video.thumbnail_publish_status === "failed") {
    return { label: "Thumbnail needs attention", tone: "error", filter: "Needs attention" };
  }
  if (video.youtube_url) return { label: "Uploaded to YouTube", tone: "success", filter: "Ready" };

  if (video.publish_status === "uploading") return { label: "Uploading to YouTube", tone: "active", filter: "In progress" };
  if (video.publish_status === "pending") return { label: "Upload queued", tone: "active", filter: "In progress" };
  if (video.publish_status === "upload_uncertain") return { label: "Check YouTube Studio", tone: "error", filter: "Needs attention" };
  if (video.publish_status === "failed") return { label: "Upload needs attention", tone: "error", filter: "Needs attention" };
  if (["auth_required", "not_configured"].includes(video.publish_status ?? "")) return { label: "YouTube reconnect required", tone: "error", filter: "Needs attention" };
  if (video.publish_status === "retry") return { label: "Upload retry scheduled", tone: "error", filter: "Needs attention" };
  if (video.video_url && !video.publish_approved_at) return { label: "Ready for review", tone: "success", filter: "Ready" };
  return { label: "MP4 ready", tone: "success", filter: "Ready" };
}

function downloadUrl(video: StudioVideo) {
  const url = new URL(video.video_url!, window.location.origin);
  url.searchParams.set("download", (video.title || "studio-video").replace(/[^a-z0-9_-]/gi, "-").slice(0, 60) + ".mp4");
  return url.href;
}

function formattedDuration(video: StudioVideo) {
  const total = Math.max(0, Math.round(video.actual_duration_seconds || video.duration_seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function Thumbnail({ video }: { video: StudioVideo }) {
  return <div className="thumbnail">
    {video.thumbnail_url
      ? <Image src={video.thumbnail_url} width={200} height={112} alt="" unoptimized />
      : <Film size={24} aria-hidden="true" />}
    <span>{formattedDuration(video)}</span>
  </div>;
}

export type RevisionInput = {
  direction: string; scene?: number; mode?: RevisionMode; narration?: string; footage?: string; requestId: string;
};

function RevisionForm({ video, busy, submit }: {
  video: StudioVideo; busy: boolean; submit: (input: RevisionInput) => Promise<void>;
}) {
  const [scene, setScene] = useState("");
  const [mode, setMode] = useState<RevisionMode | "auto">("auto");
  const [direction, setDirection] = useState("");
  const [narration, setNarration] = useState("");
  const [footage, setFootage] = useState("");
  const requestId = useRef("");
  const original = video.scenes[Number(scene) - 1];
  return <form className="revision-form" onChange={() => { requestId.current = ""; }} onSubmit={(event) => {
    event.preventDefault();
    requestId.current ||= crypto.randomUUID();
    void submit({ scene: scene ? Number(scene) : undefined, mode: mode === "auto" ? undefined : mode,
      direction, narration: narration.trim() !== original?.narration ? narration : undefined,
      footage: footage.trim() !== original?.search_queries.join(", ") ? footage : undefined,
      requestId: requestId.current });
  }}>
    <label>What should change?
      <textarea rows={2} maxLength={1600} value={direction} onChange={(event) => setDirection(event.target.value)}
        placeholder="Scene 5 lacks voice" />
    </label>
    <div className="revision-fields">
      <label>Scene
        <select value={scene} onChange={(event) => { const value = event.target.value;
          const selected = video.scenes[Number(value) - 1];
          setScene(value);
          setNarration(selected?.narration || "");
          setFootage(selected?.search_queries.join(", ") || ""); }}>
          <option value="">From your instructions</option>
          {video.scenes.map((item) => <option key={item.scene_number} value={item.scene_number}>Scene {item.scene_number}</option>)}
        </select>
      </label>
      <label>Change
        <select value={mode} onChange={(event) => setMode(event.target.value as RevisionMode | "auto")}>
          <option value="auto">From your instructions</option>
          <option value="audio">Repair voice</option>
          <option value="visual">Replace footage</option>
          <option value="rewrite">Rewrite scene</option>
        </select>
      </label>
    </div>
    {original && <details>
      <summary>Edit the scene directly</summary>
      <label>Narration<textarea rows={3} maxLength={800} value={narration} placeholder={original.narration}
        onChange={(event) => setNarration(event.target.value)} /></label>
      <label>Footage search terms <small>Separated by commas</small>
        <input maxLength={350} value={footage} placeholder={original.search_queries.join(", ")} onChange={(event) => setFootage(event.target.value)} />
      </label>
    </details>}
    <button type="submit" className="button button-primary" disabled={busy}>
      {busy ? <LoaderCircle size={16} className="spinner" /> : <RefreshCw size={16} />} {busy ? "Queuing…" : "Render revision"}
    </button>
  </form>;
}

function Preview({ video, close, approve, revise, retryThumbnail, busy, feedback, latest, editable }: {
  video: StudioVideo; close: () => void; approve: () => Promise<boolean>;
  revise: (input: RevisionInput) => Promise<boolean>; retryThumbnail: () => Promise<boolean>; busy: boolean; feedback: Feedback | null;
  latest: boolean; editable: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [playerVersion, setPlayerVersion] = useState(0);
  const [editing, setEditing] = useState(!video.video_url && !video.youtube_url);
  const [attempted, setAttempted] = useState(false);
  const player = useRef<HTMLVideoElement>(null);
  const youtubeEdit = youtubeStudioUrl(video);
  const thumbnailFailed = video.thumbnail_publish_status === "failed";
  const publishAction = youtubePublishAction(video);
  const queued = !publishAction && ["pending", "uploading"].includes(video.publish_status ?? "");
  const publishLabel = publishAction === "approve" ? "Approve & publish on YouTube" : "Retry YouTube upload";
  async function send() {
    setAttempted(true);
    if (await approve()) setConfirming(false);
  }
  return <Modal titleId="preview-title" close={close} className="preview-dialog">
    <header className="dialog-header">
      <div><p>{video.revision ? "Scene " + video.revision.scene_number + " revision" : video.video_url ? "Video preview" : "YouTube upload"}</p>
        <h2 id="preview-title">{video.title}</h2></div>
      <button className="icon-button" aria-label="Close preview" onClick={close}><X size={20} /></button>
    </header>
    <div className="preview-body">
      {video.video_url && <video key={playerVersion} ref={player} src={video.video_url} poster={video.thumbnail_url || undefined} controls playsInline autoPlay preload="metadata"
        className={video.format === "9:16" ? "vertical" : ""} aria-label={video.title}
        onCanPlay={() => setFailed(false)} onError={() => setFailed(true)} />}
      {failed && <div className="media-error" role="alert">The preview could not load. <button type="button" className="text-button" onClick={() => {
        setFailed(false);
        setPlayerVersion((version) => version + 1);
      }}>Retry playback</button> or use Download MP4.</div>}
      {!!video.scene_times?.length && video.video_url && <label className="preview-scenes">Jump to scene
        <select value="" onChange={(event) => { if (player.current && event.target.value) player.current.currentTime = Number(event.target.value); }}>
          <option value="">Choose a scene</option>
          {video.scene_times.map(({ scene_number, start }) => <option key={scene_number} value={start}>Scene {scene_number} · {Math.floor(start / 60)}:{String(Math.floor(start % 60)).padStart(2, "0")}</option>)}
        </select>
      </label>}
      {!!video.sources?.length && <details className="preview-sources">
        <summary>Research sources</summary>
        <ul>{video.sources.map((source) => <li key={source.url}><a href={source.url} target="_blank" rel="noreferrer">{source.title}</a></li>)}</ul>
      </details>}
      {youtubeEdit && <section className="revision-panel" aria-label="YouTube upload details">
        <p className="review-note">{video.youtube_visibility_at_upload === "public" ? "YouTube accepted the public upload."
          : video.youtube_visibility_at_upload ? `YouTube reported ${video.youtube_visibility_at_upload} visibility. Check YouTube Studio to review it.`
          : "Manage this video’s visibility in YouTube Studio."}</p>
        {["pending", "retry"].includes(video.thumbnail_publish_status ?? "") && <p className="review-note" role="status">
          YouTube thumbnail is queued. Studio will retry if processing is delayed.
        </p>}
        {thumbnailFailed && <div className="grid gap-2">
          <p className="review-note">{video.progress_note || "YouTube did not accept the custom thumbnail."}</p>
          {video.progress_note?.includes("HTTP 403") && <p className="review-note">
            Custom thumbnails require channel verification. <a className="text-button" href="https://www.youtube.com/verify" target="_blank" rel="noreferrer">Verify on YouTube</a>
          </p>}
          {canRetryYouTubeThumbnail(video) && <button type="button" className="button button-secondary justify-self-start" disabled={busy}
            onClick={async () => { setAttempted(true); if (await retryThumbnail()) close(); }}>
            <RefreshCw size={16} /> Retry thumbnail
          </button>}
        </div>}
      </section>}
      {editing && <div className="revision-panel">
        {attempted && feedback?.kind === "error" && <Notice feedback={feedback} />}
        <p className="review-note">{video.scene_times?.length && video.video_url
          ? "Your current preview stays available while Studio renders the revision."
          : "This preview has no saved scene timings for a targeted revision."}</p>
        {!!video.scene_times?.length && video.video_url && <RevisionForm video={video} busy={busy} submit={async (input) => { setAttempted(true); if (await revise(input)) close(); }} />}
      </div>}
    </div>
    <footer className="dialog-footer preview-footer">
      {!latest && <p className="review-note">Earlier version</p>}
      {!editing && attempted && feedback && <Notice feedback={feedback} />}
      {!editing && queued && <p className="review-note" role="status">{video.publish_status === "uploading"
        ? (video.progress_note || "Uploading to YouTube.") + " You can close this window while it runs." : "Approved. Waiting for the YouTube upload worker."}</p>}
      {!editing && publishAction && !confirming && <p className="review-note">{publishAction === "recheck"
        ? "Check YouTube Studio for an existing copy before retrying." : "Review the preview or downloaded MP4. Approval queues a public upload."}</p>}
      {!editing && publishAction === "recheck" && confirming && <p id="publish-confirmation" className="review-note" role="status">
        Confirm that you checked YouTube Studio and found no existing copy. Retrying will request a public upload.
      </p>}
      {!editing && video.publish_error && !queued && <p className="video-problem" role="status">
        {video.publish_error}{video.publish_status === "retry" && clock(video.publish_retry_at) && " Next attempt after " + clock(video.publish_retry_at) + "."}
      </p>}
      <div className="preview-actions">
        {video.video_url && <a className="button button-secondary" href={downloadUrl(video)} download><Download size={17} /> Download MP4</a>}
        {youtubeEdit && <a className="button button-secondary" href={youtubeEdit} target="_blank" rel="noreferrer">YouTube Studio <ArrowUpRight size={17} /></a>}
        {editable && !!video.scenes.length && video.video_url && <button type="button" className="button button-secondary" disabled={busy} onClick={() => {
          setEditing(!editing); setAttempted(false); setConfirming(false); if (!editing) player.current?.pause();
        }}>{editing ? "Close editor" : "Regenerate or edit"}</button>}
        {publishAction === "recheck" && !editing && <a className="button button-secondary" href="https://studio.youtube.com/" target="_blank" rel="noreferrer">Check YouTube Studio <ArrowUpRight size={17} /></a>}
        {publishAction && !editing && confirming && <button type="button" className="button button-secondary" disabled={busy} onClick={() => setConfirming(false)}>Cancel</button>}
        {publishAction && !editing && <button type="button" className="button button-primary" disabled={busy}
          aria-describedby={confirming ? "publish-confirmation" : undefined} onClick={() => {
            player.current?.pause();
            if (publishAction !== "recheck" || confirming) void send();
            else { setAttempted(false); setConfirming(true); }
          }}>
          {busy && attempted && <LoaderCircle size={16} className="spinner" />}
          {busy && attempted ? "Sending approval…" : confirming ? "Confirm retry" : publishLabel}
        </button>}
      </div>
    </footer>
  </Modal>;
}

export function ProductionQueue({ videos, loading, error, refreshing, refresh, busy, retry, revise, retryThumbnail, feedback, reuse }: {
  videos: StudioVideo[]; loading: boolean; error: string; refreshing: boolean; refresh: () => void;
  busy: string | null; retry: (video: StudioVideo, publish?: boolean) => Promise<boolean>; feedback: Feedback | null;
  retryThumbnail: (video: StudioVideo) => Promise<boolean>;
  revise: (video: StudioVideo, input: RevisionInput) => Promise<boolean>;
  reuse: (video: StudioVideo) => void;
}) {
  const [filter, setFilter] = useState<Filter>("All");
  const [search, setSearch] = useState("");
  const [shown, setShown] = useState(PAGE_SIZE);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const query = useDeferredValue(search.trim().toLowerCase());
  const records = useMemo(() => groupProductions(videos).map((production) => ({ ...production, state: videoState(production.latest) })), [videos]);
  const filtered = records.filter(({ latest, state }) => (filter === "All" || state.filter === filter) && (!query || latest.title.toLowerCase().includes(query)));
  const previewGroup = records.find(({ versions }) => versions.some((version) => version.id === previewId));
  const preview = previewGroup?.versions.find((version) => version.id === previewId && version.status === "completed" && (version.video_url || version.youtube_url));
  const dateOf = (date?: string) => date && Number.isFinite(Date.parse(date)) ? dateFormat.format(new Date(date)) : "";

  return <section className="panel queue" id="queue" aria-labelledby="queue-title">
    <div className="queue-top">
      <div className="section-heading">
        <div className="section-icon"><Film size={21} /></div>
        <div><h2 id="queue-title">Production queue</h2><p>Track renders, watch previews, and approve uploads.</p></div>
      </div>
      <button className="button button-secondary" disabled={refreshing} onClick={refresh}>
        <RefreshCw size={16} className={refreshing ? "spinner" : ""} /> <span>Refresh</span>
      </button>
    </div>
    <div className="queue-controls">
      <div className="filters" role="group" aria-label="Filter productions">
        {FILTERS.map((name) => <button key={name} aria-pressed={filter === name} onClick={() => { setFilter(name); setShown(PAGE_SIZE); }}>
          {name}<span>{name === "All" ? records.length : records.filter(({ state }) => state.filter === name).length}</span>
        </button>)}
      </div>
      <label className="queue-search"><Search size={17} /><input aria-label="Search recent videos" type="search" value={search}
        onChange={(event) => { setSearch(event.target.value); setShown(PAGE_SIZE); }} placeholder="Search videos" /></label>
    </div>
    {error && <p className="queue-warning" role="status">{videos.length > 0 ? "Showing the last loaded queue. " : ""}{error}</p>}
    {loading ? <div className="queue-empty" role="status"><LoaderCircle size={25} className="spinner" /><p>Loading your production queue…</p></div>
      : filtered.length === 0 ? <div className="queue-empty">
        <Clapperboard size={28} />
        <h3>{query || filter !== "All" ? "No matching videos" : error ? "Queue unavailable" : "No productions yet"}</h3>
        {query || filter !== "All"
          ? <button className="text-button" onClick={() => { setSearch(""); setFilter("All"); setShown(PAGE_SIZE); }}>Clear filters</button>
          : error ? <button className="text-button" disabled={refreshing} onClick={refresh}>Try again</button>
            : <a className="text-button" href="#compose">Create a video</a>}
      </div>
      : <ul className="queue-list">{filtered.slice(0, shown).map(({ id, latest, playable, versions, state }) => {
        const problem = latest.status === "failed" ? latest.error_message : latest.publish_approved_at ? latest.publish_error : "";
        const progress = latest.status === "rendering" || latest.thumbnail_publish_status === "failed" ? latest.progress_note : "";
        const waiting = latest.status === "pending" ? clock(latest.created_at) : "";
        const renderedIn = latest.revision && latest.status === "completed" ? renderTime(latest) : "";
        return <li className="queue-row" key={id}>
          <Thumbnail video={playable || latest} />
          <div className="video-info">
            <div className="video-meta"><span className={"status status-" + state.tone}>{state.label}</span><span>{latest.format}</span>{dateOf(latest.created_at) && <span>{dateOf(latest.created_at)}</span>}</div>
            <h3>{latest.title}</h3>
            {latest.revision && <p>Scene {latest.revision.scene_number} revision{renderedIn && " · Rendered in " + renderedIn}</p>}
            {problem ? <p className="video-problem">{problem}</p> : progress ? <p role="status">{progress}</p>
              : latest.status === "pending" ? <p role="status">Waiting for the render worker{waiting && " since " + waiting}.</p>
              : latest.description ? <p className="video-description">{latest.description}</p> : null}
            {latest.preview_expired_at && !playable && <p>MP4 preview expired. The YouTube copy is available.</p>}
          </div>
          <div className="video-actions">
            {playable && <button className="button button-secondary" onClick={() => setPreviewId(playable.id)}>
              <Play size={15} /> {playable.id !== latest.id ? "Preview previous version"
                : playable.youtube_url || playable.publish_approved_at ? "Preview" : latest.revision ? "Review revision" : "Review video"}
            </button>}
            {latest.youtube_url && !playable && <button className="button button-secondary" onClick={() => setPreviewId(latest.id)}>
              YouTube details
            </button>}
            {latest.status === "failed" && <button className="button button-secondary" disabled={!!busy} onClick={() => void retry(latest)}>
              {busy === latest.id && <LoaderCircle className="spinner" size={16} />} Retry {latest.revision ? "revision" : "render"}
            </button>}
            {latest.status === "completed" && <button className="text-button" onClick={() => reuse(latest)}>Reuse script</button>}
          </div>
          {versions.length > 1 && <details className="queue-versions">
            <summary>Versions ({versions.length})</summary>
            <ul>{versions.map((version) => <li key={version.id}>
              <span>{version.revision ? "Scene " + version.revision.scene_number + " revision" : "Original"}
                {version.id === latest.id && " · Latest"} <small>{videoState(version).label}</small>
              </span>
              {version.status === "completed" && (version.video_url || version.youtube_url) && <button type="button" className="text-button" onClick={() => setPreviewId(version.id)}>
                {version.video_url ? "Preview" : "YouTube details"}
              </button>}
            </li>)}</ul>
          </details>}
        </li>;
      })}</ul>}
    {filtered.length > shown && <div className="queue-more"><button className="button button-secondary" onClick={() => setShown((count) => count + PAGE_SIZE)}>Show more videos</button></div>}
    {preview && previewGroup && <Preview key={preview.id} video={preview} close={() => setPreviewId(null)}
      latest={preview.id === previewGroup.latest.id}
      editable={preview.id === previewGroup.latest.id || (previewGroup.latest.status === "failed" && preview.id === previewGroup.playable?.id)}
      approve={() => retry(preview, true)} revise={(input) => revise(preview, input)}
      retryThumbnail={() => retryThumbnail(preview)} busy={busy !== null} feedback={feedback} />}
  </section>;
}
