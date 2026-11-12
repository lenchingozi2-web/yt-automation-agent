import { useDeferredValue, useMemo, useState } from "react";
import Image from "next/image";
import { ArrowUpRight, Clapperboard, Download, Film, LoaderCircle, Play, RefreshCw, Search, X } from "lucide-react";
import type { StudioVideo } from "@/src/lib/studio";
import { Modal, Notice, type Feedback } from "./studio-shell";

const FILTERS = ["All", "In progress", "Ready", "Needs attention"] as const;
type Filter = typeof FILTERS[number];
const PAGE_SIZE = 8;
const dateFormat = new Intl.DateTimeFormat("en-NG", { day: "numeric", month: "short", timeZone: "Africa/Lagos" });

function videoState(video: StudioVideo) {
  if (video.status === "failed") return { label: "Render failed", tone: "error", filter: "Needs attention" };
  if (video.status === "rendering") return { label: "Rendering", tone: "active", filter: "In progress" };
  if (video.status === "pending") return { label: "Queued", tone: "muted", filter: "In progress" };
  if (video.youtube_url) return { label: "Uploaded privately", tone: "success", filter: "Ready" };
  if (video.video_url && !video.publish_approved_at) return { label: "Ready for review", tone: "success", filter: "Ready" };
  if (video.publish_status === "uploading") return { label: "Uploading to YouTube", tone: "active", filter: "In progress" };
  if (video.publish_status === "pending") return { label: "Upload queued", tone: "active", filter: "In progress" };
  if (video.publish_status === "upload_uncertain") return { label: "Check YouTube Studio", tone: "error", filter: "Needs attention" };
  if (video.publish_status === "auth_required") return { label: "YouTube reconnect required", tone: "error", filter: "Needs attention" };
  if (video.publish_status === "retry") return { label: "Upload retry scheduled", tone: "error", filter: "Needs attention" };
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

function Preview({ video, close, approve, busy, feedback }: {
  video: StudioVideo; close: () => void; approve: () => Promise<boolean>; busy: boolean; feedback: Feedback | null;
}) {
  const [failed, setFailed] = useState(false);
  const [watched, setWatched] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const canApprove = !video.youtube_url && (!video.publish_approved_at ||
    ["auth_required", "retry", "upload_uncertain", "not_configured"].includes(video.publish_status ?? ""));
  async function send() {
    setAttempted(true);
    if (await approve()) close();
  }
  return <Modal titleId="preview-title" close={close} className="preview-dialog">
    <header className="dialog-header">
      <div><p>Video preview</p><h2 id="preview-title">{video.title}</h2></div>
      <button className="icon-button" aria-label="Close preview" onClick={close}><X size={20} /></button>
    </header>
    <div className="preview-body">
      <video src={video.video_url} poster={video.thumbnail_url || undefined} controls playsInline autoPlay preload="metadata"
        className={video.format === "9:16" ? "vertical" : ""} aria-label={video.title}
        onEnded={() => setWatched(true)} onError={() => setFailed(true)} />
      {failed && <p role="alert" className="media-error">The preview could not load. Try downloading the MP4 or opening its YouTube copy.</p>}
    </div>
    <footer className="dialog-footer preview-footer">
      {attempted && feedback?.kind === "error" && <Notice feedback={feedback} />}
      {canApprove && <p className="review-note">
        {video.publish_status === "upload_uncertain" ? "Check YouTube Studio for an existing copy before approving another upload."
          : watched ? "Approve to upload this video privately to YouTube." : "Watch the preview to the end to unlock approval."}
      </p>}
      <div className="preview-actions">
        <a className="button button-secondary" href={downloadUrl(video)} download><Download size={17} /> Download MP4</a>
        {video.youtube_url && <a className="button button-secondary" href={video.youtube_url} target="_blank" rel="noreferrer">Open YouTube <ArrowUpRight size={17} /></a>}
        {canApprove && <button className="button button-primary" disabled={!watched || failed || busy} onClick={() => void send()}>
          {busy && <LoaderCircle size={16} className="spinner" />} Approve &amp; upload to YouTube
        </button>}
      </div>
    </footer>
  </Modal>;
}

export function ProductionQueue({ videos, loading, error, refreshing, refresh, busy, retry, feedback, reuse }: {
  videos: StudioVideo[]; loading: boolean; error: string; refreshing: boolean; refresh: () => void;
  busy: string | null; retry: (video: StudioVideo, publish?: boolean) => Promise<boolean>; feedback: Feedback | null;
  reuse: (video: StudioVideo) => void;
}) {
  const [filter, setFilter] = useState<Filter>("All");
  const [search, setSearch] = useState("");
  const [shown, setShown] = useState(PAGE_SIZE);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const query = useDeferredValue(search.trim().toLowerCase());
  const records = useMemo(() => videos.map((video) => ({ video, state: videoState(video) })), [videos]);
  const filtered = records.filter(({ video, state }) => (filter === "All" || state.filter === filter) && (!query || video.title.toLowerCase().includes(query)));
  const preview = videos.find((video) => video.id === previewId && video.video_url);
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
      : <ul className="queue-list">{filtered.slice(0, shown).map(({ video, state }) => {
        const problem = video.status === "failed" ? video.error_message : video.publish_approved_at ? video.publish_error : "";
        const progress = video.status === "rendering" || video.thumbnail_publish_status === "failed" ? video.progress_note : "";
        return <li className="queue-row" key={video.id}>
          <Thumbnail video={video} />
          <div className="video-info">
            <div className="video-meta"><span className={"status status-" + state.tone}>{state.label}</span><span>{video.format}</span>{dateOf(video.created_at) && <span>{dateOf(video.created_at)}</span>}</div>
            <h3>{video.title}</h3>
            {problem ? <p className="video-problem">{problem}</p> : progress ? <p role="status">{progress}</p> : video.description ? <p className="video-description">{video.description}</p> : null}
            {video.preview_expired_at && !video.video_url && <p>MP4 preview expired. The YouTube copy is available.</p>}
          </div>
          <div className="video-actions">
            {video.video_url && video.status === "completed" && <button className="button button-secondary" onClick={() => setPreviewId(video.id)}>
              <Play size={15} /> {video.youtube_url || video.publish_approved_at ? "Preview" : "Review video"}
            </button>}
            {video.youtube_url && <a className="button button-secondary" href={video.youtube_url} target="_blank" rel="noreferrer">YouTube <ArrowUpRight size={16} /></a>}
            {video.status === "failed" && <button className="button button-secondary" disabled={!!busy} onClick={() => void retry(video)}>
              {busy === video.id && <LoaderCircle className="spinner" size={16} />} Retry render
            </button>}
            {video.status === "completed" && <button className="text-button" onClick={() => reuse(video)}>Reuse script</button>}
          </div>
        </li>;
      })}</ul>}
    {filtered.length > shown && <div className="queue-more"><button className="button button-secondary" onClick={() => setShown((count) => count + PAGE_SIZE)}>Show more videos</button></div>}
    {preview && <Preview key={preview.id} video={preview} close={() => setPreviewId(null)}
      approve={() => retry(preview, true)} busy={busy !== null} feedback={feedback} />}
  </section>;
}
