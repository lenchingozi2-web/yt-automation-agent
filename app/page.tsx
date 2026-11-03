"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { ArrowUpRight, Check, ChevronRight, Download, LoaderCircle, LockKeyhole, LogOut, Play, Plus, RefreshCw, Sparkles, Trash2, X } from "lucide-react";
import { getSupabaseClient } from "@/src/lib/supabase";
import { normalizeDraft, normalizeVideo, toSafeMessage } from "@/src/lib/studio";

type Scene = { scene_number: number; narration: string; search_queries: string[]; estimated_duration?: number };
type Script = { title: string; description: string; tags: string[]; format: "16:9" | "9:16"; duration_seconds: number; scenes: Scene[]; id?: string; status?: string };
type Video = Script & { id: string; status: string; youtube_url?: string; youtube_id?: string; youtube_video_id?: string; video_url?: string; storage_url?: string; progress_note?: string; error_message?: string; created_at?: string };

const statuses = ["pending", "rendering", "completed", "failed"];
const durationOptions = [{ label: "60s", value: 60 }, { label: "90s", value: 90 }, { label: "3 min", value: 180 }, { label: "5 min", value: 300 }, { label: "8 min", value: 480 }, { label: "12 min", value: 720 }];

function Logo() { return <div className="flex items-center gap-3"><div className="grid h-10 w-10 place-items-center rounded-xl bg-[#d3f36b] text-[#090b0f]"><svg width="32" height="32" viewBox="0 0 32 32" aria-hidden="true"><rect x="4" y="6" width="24" height="20" rx="6" fill="currentColor" /><path d="m13 11 9 5-9 5v-10Z" fill="#090b0f" /></svg></div><div><div className="text-[15px] font-bold tracking-tight">YT Automation</div><div className="mono text-[10px] uppercase tracking-[.22em] text-[#8e958f]">Studio / 01</div></div></div>; }
function statusStyle(status: string) { if (status === "completed") return "border-[#5d8a42] bg-[#17241a] text-[#b8e889]"; if (status === "failed") return "border-[#7a4036] bg-[#281b1a] text-[#ff9d87]"; if (status === "rendering") return "border-[#876c34] bg-[#282215] text-[#f3ca73]"; return "border-[#3c4d54] bg-[#182126] text-[#b2c5cb]"; }
function youtubeLink(video: Video) { const id = video.youtube_id || video.youtube_video_id; return video.youtube_url || (id ? `https://www.youtube.com/watch?v=${id}` : null); }
function blankScene(number: number): Scene { return { scene_number: number, narration: "", search_queries: ["cinematic b-roll"] }; }
export default function Home() {
  const supabase = getSupabaseClient();
  const [videos, setVideos] = useState<Video[]>([]); const [prompt, setPrompt] = useState(""); const [pastedScript, setPastedScript] = useState(""); const [composerMode, setComposerMode] = useState<"generate" | "paste">("generate");
  const [format, setFormat] = useState<"16:9" | "9:16">("16:9"); const [duration, setDuration] = useState(180); const [customMinutes, setCustomMinutes] = useState(""); const [selected, setSelected] = useState<Script | null>(null);
  const [email, setEmail] = useState(""); const [password, setPassword] = useState(""); const [user, setUser] = useState<{ email?: string } | null>(null); const [authMode, setAuthMode] = useState<"signin" | "signup">("signin"); const [busy, setBusy] = useState(false); const [notice, setNotice] = useState(""); const [installEvent, setInstallEvent] = useState<Event | null>(null);
  const loadVideos = useCallback(async () => {
    if (!supabase) return;
    try {
      const session = await supabase.auth.getSession();
      const response = await fetch("/api/videos", { cache: "no-store", headers: { ...(session.data.session?.access_token ? { Authorization: `Bearer ${session.data.session.access_token}` } : {}) } });
      const payload = await response.json() as { videos?: Video[]; error?: unknown };
      if (!response.ok) throw new Error(toSafeMessage(payload.error));
      setVideos(Array.isArray(payload.videos) ? payload.videos.map((entry) => normalizeVideo(entry) as Video) : []);
    } catch (error) {
      setNotice(toSafeMessage(error));
    }
  }, [supabase]);
  useEffect(() => {
    if (!supabase) return;
    supabase.auth.getUser().then(({ data }) => setUser(data.user ? { email: data.user.email } : null));
    const { data: listener } = supabase.auth.onAuthStateChange((_event, session) => setUser(session?.user ? { email: session.user.email } : null));
    const initialLoad = window.setTimeout(() => void loadVideos(), 0);
    const timer = window.setInterval(() => void loadVideos(), 4000);
    const onInstall = (event: Event) => { event.preventDefault(); setInstallEvent(event); };
    window.addEventListener("beforeinstallprompt", onInstall);
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").then((registration) => registration.update()).catch(() => undefined);
    return () => { listener.subscription.unsubscribe(); window.clearTimeout(initialLoad); window.clearInterval(timer); window.removeEventListener("beforeinstallprompt", onInstall); };
  }, [loadVideos, supabase]);
  const counts = useMemo(() => statuses.reduce<Record<string, number>>((result, status) => { result[status] = videos.filter((video) => video.status === status).length; return result; }, {}), [videos]);
  async function authenticate(event: FormEvent) { event.preventDefault(); setBusy(true); setNotice(toSafeMessage("")); if (!supabase) { setNotice(toSafeMessage("Connect Supabase environment variables to sign in.")); setBusy(false); return; } const response = authMode === "signin" ? await supabase.auth.signInWithPassword({ email, password }) : await supabase.auth.signUp({ email, password }); setNotice(toSafeMessage(response.error?.message ?? (authMode === "signup" ? "Check your email to confirm your account." : "Welcome back."))); setBusy(false); }
  async function apiHeaders() {
    const session = await supabase?.auth.getSession();
    return { "Content-Type": "application/json", ...(session?.data.session?.access_token ? { Authorization: `Bearer ${session.data.session.access_token}` } : {}) };
  }
  async function generate(event: FormEvent) {
    event.preventDefault(); setBusy(true); setNotice(toSafeMessage("", ""));
    try {
      if (composerMode === "paste") {
        let parsed: unknown;
        try { parsed = JSON.parse(pastedScript); }
        catch {
          parsed = { title: "Manual production", description: "", tags: [], format, duration_seconds: duration || 180,
            scenes: pastedScript.split(/\n\s*\n/).filter(Boolean).map((narration, index) => ({
              scene_number: index + 1, narration: narration.trim(), search_queries: ["cinematic b-roll"] })) };
        }
        const draft = normalizeDraft(parsed);
        setSelected({ ...draft, format, duration_seconds: duration || 180 });
      } else {
        const response = await fetch("/api/generate", { method: "POST", headers: await apiHeaders(),
          body: JSON.stringify({ prompt, duration: customMinutes ? Number(customMinutes) * 60 : duration, format }) });
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(toSafeMessage(result.error, "Script generation failed."));
        setSelected(normalizeDraft(result.draft));
        setNotice(toSafeMessage(result.message, "Script generated. Review your scenes."));
      }
    } catch (error) { setNotice(toSafeMessage(error, "Could not prepare the script.")); }
    finally { setBusy(false); }
  }
  async function queueScript() {
    if (!selected) return;
    setBusy(true); setNotice(toSafeMessage("", ""));
    try {
      if (!selected.title.trim() || !selected.scenes.length || selected.scenes.some(scene => !scene.narration.trim())) {
        throw new Error("Give your video a title and narration for every scene.");
      }
      const response = await fetch("/api/render", { method: "POST", headers: await apiHeaders(),
        body: JSON.stringify(selected) });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(toSafeMessage(result.error, "Could not queue video."));
      setSelected(null);
      setNotice(toSafeMessage(result.message, "Video queued!") + (result.note ? " " + toSafeMessage(result.note, "") : ""));
      await loadVideos();
    } catch (error) { setNotice(toSafeMessage(error, "Could not queue video.")); }
    finally { setBusy(false); }
  }
  async function render(video: Video) {
    setBusy(true); setNotice(toSafeMessage("", ""));
    try {
      const response = await fetch("/api/render", { method: "POST", headers: await apiHeaders(),
        body: JSON.stringify({ id: video.id }) });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(toSafeMessage(result.error, "Retry failed."));
      setNotice(toSafeMessage(result.message, "Video queued again."));
      await loadVideos();
    } catch (error) { setNotice(toSafeMessage(error, "Retry failed.")); }
    finally { setBusy(false); }
  }
  async function regenerateScript() {
    if (!selected) return;
    setBusy(true);
    try {
      const response = await fetch("/api/generate", { method: "POST", headers: await apiHeaders(),
        body: JSON.stringify({ prompt: prompt.trim() || selected.title, duration: selected.duration_seconds, format: selected.format }) });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(toSafeMessage(result.error, "Could not regenerate."));
      setSelected(normalizeDraft(result.draft));
      setNotice(toSafeMessage(result.message, "Script regenerated."));
    } catch (error) { setNotice(toSafeMessage(error, "Could not regenerate script.")); }
    finally { setBusy(false); }
  }
  function moveScene(index: number, offset: number) {
    setSelected(current => {
      if (!current) return current;
      const destination = index + offset;
      if (destination < 0 || destination >= current.scenes.length) return current;
      const next = [...current.scenes];
      [next[index], next[destination]] = [next[destination], next[index]];
      return { ...current, scenes: next.map((item, i) => ({ ...item, scene_number: i + 1 })) };
    });
  }
  function updateSelected(patch: Partial<Script>) { setSelected((current) => current ? { ...current, ...patch } : current); }
  async function install() { if (!installEvent) return; const promptEvent = installEvent as Event & { prompt: () => Promise<void> }; await promptEvent.prompt(); setInstallEvent(null); }
  if (!user) return <main className="grain min-h-screen px-5 py-5 sm:px-10"><header className="mx-auto flex max-w-7xl items-center justify-between"><Logo /><span className="mono hidden text-xs text-[#8e958f] sm:block">PRIVATE WORKSPACE</span></header><section className="mx-auto flex min-h-[calc(100vh-90px)] max-w-7xl items-center justify-center py-10"><div className="auth-card"><p className="mono mb-5 text-xs uppercase tracking-[.28em] text-[#d3f36b]">Your publishing desk</p><h1 className="mb-5 text-5xl leading-[.98] tracking-[-.04em] sm:text-6xl">Make the next<br /><i>good video.</i></h1><p className="mb-9 max-w-sm text-lg leading-relaxed text-[#8e958f]">A quiet command center for turning a sharp idea into a ready-to-render YouTube story.</p><form onSubmit={authenticate}><label className="mono mb-2 block text-[10px] uppercase tracking-[.2em] text-[#8e958f]">Email</label><input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required placeholder="you@studio.com" /><label className="mono mb-2 block text-[10px] uppercase tracking-[.2em] text-[#8e958f]">Password</label><input value={password} onChange={(event) => setPassword(event.target.value)} type="password" required minLength={6} placeholder="At least 6 characters" /><button disabled={busy} className="primary-action flex items-center justify-center gap-2">{busy ? <LoaderCircle className="animate-spin" size={17} /> : <LockKeyhole size={17} />}{authMode === "signin" ? "Sign in" : "Create account"}</button></form><button onClick={() => setAuthMode(authMode === "signin" ? "signup" : "signin")} className="secondary-action mt-3 text-sm underline decoration-[#3b4448] underline-offset-4 hover:text-[#f5f2ea]">{authMode === "signin" ? "New here? Create an account" : "Already have an account? Sign in"}</button>{notice && <p className="mt-5 text-sm text-[#ff9d87]">{notice}</p>}</div></section></main>;
  return <main className="grain min-h-screen"><header className="sticky top-0 z-10 border-b border-[#252b2e] bg-[#090b0f]/95 px-5 py-4 backdrop-blur sm:px-10"><div className="mx-auto flex max-w-7xl items-center justify-between"><Logo /><nav className="flex items-center gap-3"><button onClick={install} disabled={!installEvent} className="flex items-center gap-2 rounded-full border border-[#3b4448] px-3 py-2 text-xs text-[#b6c0bb] disabled:opacity-40"><Download size={14} /><span className="hidden sm:inline">Install App</span></button><button onClick={() => supabase?.auth.signOut()} aria-label="Sign out" className="rounded-full border border-[#3b4448] p-2 text-[#8e958f]"><LogOut size={15} /></button></nav></div></header><div className="mx-auto max-w-7xl px-5 py-8 sm:px-10 sm:py-12"><div className="mb-10 flex flex-col justify-between gap-5 md:flex-row md:items-end"><div><p className="mono mb-3 text-xs uppercase tracking-[.28em] text-[#d3f36b]">Dashboard / Overview</p><h1 className="text-4xl tracking-[-.04em] sm:text-6xl">The story room.</h1></div><div className="flex items-center gap-3 text-sm text-[#8e958f]"><span className="h-2 w-2 animate-pulse rounded-full bg-[#d3f36b]" />Queue syncs every 4 sec</div></div><section className="mb-10 grid gap-4 lg:grid-cols-[1.35fr_.65fr]"><form onSubmit={generate} className="rounded-xl border border-[#3c4d54] bg-[#11161a] p-5 sm:p-7"><div className="mb-6 flex items-center justify-between"><div><div className="mono mb-2 text-[10px] uppercase tracking-[.22em] text-[#8e958f]">New production</div><div className="flex gap-4 text-sm"><button type="button" onClick={() => setComposerMode("generate")} className={composerMode === "generate" ? "text-[#d3f36b]" : "text-[#8e958f]"}>Generate a Script</button><button type="button" onClick={() => setComposerMode("paste")} className={composerMode === "paste" ? "text-[#d3f36b]" : "text-[#8e958f]"}>Paste My Own Script</button></div></div><Sparkles className="text-[#d3f36b]" size={22} /></div>{composerMode === "generate" ? <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} className="min-h-28 w-full resize-y border-b border-[#3b4448] bg-transparent py-3 text-xl leading-relaxed outline-none placeholder:text-[#566166] focus:border-[#d3f36b]" placeholder="A 12-minute essay on why small teams build better tools..." /> : <textarea value={pastedScript} onChange={(event) => setPastedScript(event.target.value)} className="min-h-28 w-full resize-y rounded-lg border border-[#252b2e] bg-[#0b1013] p-3 text-sm leading-relaxed outline-none focus:border-[#d3f36b]" placeholder="Paste plain-text paragraphs or the JSON script shape here..." />}<div className="mt-5 grid gap-3 sm:grid-cols-3"><label className="text-xs text-[#8e958f]">Aspect ratio<select value={format} onChange={(event) => setFormat(event.target.value as "16:9" | "9:16")} className="mt-1 w-full rounded border border-[#3b4448] bg-[#0b1013] p-2 text-sm text-[#f5f2ea]"><option value="16:9">Landscape 16:9</option><option value="9:16">Portrait 9:16</option></select></label><label className="text-xs text-[#8e958f]">Duration<select value={duration} onChange={(event) => setDuration(Number(event.target.value))} className="mt-1 w-full rounded border border-[#3b4448] bg-[#0b1013] p-2 text-sm text-[#f5f2ea]">{durationOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}<option value={0}>Custom</option></select></label>{duration === 0 && <label className="text-xs text-[#8e958f]">Custom minutes<input value={customMinutes} onChange={(event) => setCustomMinutes(event.target.value)} type="number" min="1" max="12" className="mt-1 w-full rounded border border-[#3b4448] bg-[#0b1013] p-2 text-sm text-[#f5f2ea]" placeholder="1–12" /></label>}</div><div className="mt-5 flex flex-wrap items-center justify-between gap-3">{composerMode === "paste" ? <button type="button" onClick={() => setSelected({ title: "New video", description: "", tags: [], format, duration_seconds: duration || 180, scenes: [blankScene(1)] })} className="rounded-lg border border-[#3b4448] px-3 py-2 text-sm">Start Blank Scene</button> : <span className="mono hidden text-[10px] uppercase tracking-[.16em] text-[#566166] sm:block">Gemini / JSON / scene editor</span>}<button disabled={busy || (composerMode === "generate" ? !prompt.trim() : !pastedScript.trim())} className="flex items-center gap-2 rounded-lg bg-[#d3f36b] px-4 py-3 text-sm font-bold text-[#090b0f] disabled:opacity-40">{busy ? <LoaderCircle className="animate-spin" size={16} /> : <Plus size={16} />}{composerMode === "paste" ? "Open in editor" : "Generate script"}</button></div></form><div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-2">{[["Pending", "pending"], ["Rendering", "rendering"], ["Completed", "completed"], ["Failed", "failed"]].map(([label, key]) => <div key={key} className="rounded-xl border border-[#252b2e] bg-[#11161a] p-5"><div className="mono text-[10px] uppercase tracking-[.18em] text-[#8e958f]">{label}</div><div className="mt-5 text-4xl">{counts[key] ?? 0}</div></div>)}</div></section>{notice && <div className="mb-6 flex items-center gap-2 border-l-2 border-[#d3f36b] bg-[#141b18] px-4 py-3 text-sm text-[#d3f36b]"><Check size={16} />{notice}</div>}<div className="mb-4 flex items-center justify-between"><div><div className="mono text-[10px] uppercase tracking-[.22em] text-[#8e958f]">Production queue</div><h2 className="mt-2 text-2xl">Recent stories</h2></div><button onClick={() => void loadVideos()} className="rounded-full border border-[#3b4448] p-2 text-[#8e958f]" aria-label="Refresh queue"><RefreshCw size={15} /></button></div><div className="overflow-hidden rounded-xl border border-[#252b2e] bg-[#11161a]">{videos.length === 0 ? <div className="px-5 py-16 text-center text-[#8e958f]"><p className="mb-2 text-xl text-[#f5f2ea]">The queue is clear.</p><p className="text-sm">Your next idea belongs in the composer above.</p></div> : videos.map((video) => { const link = youtubeLink(video); return <div key={video.id} className="grid gap-4 border-b border-[#252b2e] px-5 py-5 last:border-0 sm:grid-cols-[1fr_auto] sm:items-center"><div className="min-w-0"><div className="mb-2 flex flex-wrap items-center gap-3"><span className={`mono rounded-full border px-2 py-1 text-[9px] uppercase tracking-[.16em] ${statusStyle(video.status)}`}>{video.status}</span><span className="mono text-[10px] text-[#8e958f]">{video.format} · {Math.round(video.duration_seconds / 60 * 10) / 10} min</span><span className="mono text-[10px] text-[#566166]">{video.created_at ? new Date(video.created_at).toLocaleDateString() : "new"}</span></div><h3 className="truncate text-xl">{video.title || "Untitled production"}</h3><p className="mt-1 truncate text-sm text-[#8e958f]">{video.description || "No description yet"}</p>{video.status === "failed" && <p className="mt-2 text-sm text-[#ff9d87]">{video.error_message || "Rendering failed. Open the worker logs for details."}</p>}{video.progress_note && <p className="mt-2 text-xs text-[#b2c5cb]">{video.progress_note}</p>}</div><div className="flex items-center gap-2 sm:justify-end"><button onClick={() => setSelected(video)} className="flex items-center gap-1 rounded-lg border border-[#3b4448] px-3 py-2 text-sm text-[#b6c0bb]">Edit <ChevronRight size={14} /></button>{(video.status === "pending" || video.status === "failed") && <button onClick={() => void render(video)} className="flex items-center gap-2 rounded-lg bg-[#d3f36b] px-3 py-2 text-sm font-bold text-[#090b0f]"><Play size={14} /> {video.status === "failed" ? "Retry Render" : "Run Worker"}</button>}{link && <a href={link} target="_blank" rel="noreferrer" className="rounded-lg border border-[#5d8a42] p-2 text-[#b8e889]" aria-label="Watch on YouTube"><ArrowUpRight size={16} /></a>}</div>{video.status === "completed" && (video.video_url || video.storage_url || link) && <div className="sm:col-span-2 space-y-3">
{(video.video_url || video.storage_url) && <><video src={video.video_url || video.storage_url} controls playsInline preload="metadata" className="w-full max-w-3xl rounded-lg bg-black" /><a href={video.video_url || video.storage_url} download={(video.title || "studio-video") + ".mp4"} target="_blank" rel="noreferrer" className="inline-flex rounded-lg border border-[#5d8a42] px-4 py-2 text-sm text-[#b8e889]">Download MP4</a></>}
{(video.youtube_id || link) && <><iframe title={"YouTube video: " + video.title} className="aspect-video w-full max-w-3xl rounded-lg" loading="lazy" allowFullScreen src={"https://www.youtube-nocookie.com/embed/" + (video.youtube_id || (link?.match(/[?&]v=([\w-]{6,20})/)?.[1] || ""))} /><a href={link || "#"} target="_blank" rel="noreferrer" className="inline-flex rounded-lg border border-[#3b4448] px-4 py-2 text-sm">Watch on YouTube ↗</a></>}
</div>}</div>; })}</div></div>{selected && <div className="fixed inset-0 z-30 grid place-items-center bg-black/70 p-4" onClick={() => setSelected(null)}><div className="max-h-[92vh] w-full max-w-4xl overflow-auto rounded-xl border border-[#3c4d54] bg-[#11161a]" onClick={(event) => event.stopPropagation()}><div className="flex items-start justify-between border-b border-[#252b2e] p-5 sm:p-7"><div><div className="mono mb-2 text-[10px] uppercase tracking-[.2em] text-[#d3f36b]">Scene editor / review before render</div><h2 className="text-2xl">Edit your production</h2></div><button onClick={() => setSelected(null)} className="text-[#8e958f]" aria-label="Close editor"><X size={20} /></button></div><div className="space-y-5 p-5 sm:p-7"><div className="grid gap-4 sm:grid-cols-2"><label className="text-xs text-[#8e958f]">Title<input value={selected.title} onChange={(event) => updateSelected({ title: event.target.value })} className="mt-1 w-full rounded border border-[#3b4448] bg-[#0b1013] p-3 text-[#f5f2ea]" /></label><label className="text-xs text-[#8e958f]">Aspect ratio<select value={selected.format} onChange={(event) => updateSelected({ format: event.target.value as "16:9" | "9:16" })} className="mt-1 w-full rounded border border-[#3b4448] bg-[#0b1013] p-3 text-[#f5f2ea]"><option value="16:9">Landscape 16:9</option><option value="9:16">Portrait 9:16</option></select></label></div><label className="block text-xs text-[#8e958f]">Description<textarea value={selected.description} onChange={(event) => updateSelected({ description: event.target.value })} className="mt-1 min-h-20 w-full rounded border border-[#3b4448] bg-[#0b1013] p-3 text-[#f5f2ea]" /></label><div className="grid gap-4 sm:grid-cols-2"><label className="text-xs text-[#8e958f]">Tags, comma separated<input value={selected.tags.join(", ")} onChange={event => updateSelected({ tags: event.target.value.split(",").map(t => t.trim()).filter(Boolean) })} /></label><label className="text-xs text-[#8e958f]">Target duration (seconds)<input type="number" min={60} max={720} value={selected.duration_seconds} onChange={event => updateSelected({ duration_seconds: Number(event.target.value) })} /></label></div>{selected.scenes.map((scene, index) => <div key={`${selected.id || "draft"}-${index}`} className="rounded-lg border border-[#252b2e] bg-[#0b1013] p-4"><div className="mb-3 flex items-center justify-between"><span className="mono text-[10px] uppercase tracking-[.18em] text-[#d3f36b]">Scene {index + 1} · {scene.estimated_duration || Math.round(selected.duration_seconds / Math.max(1, selected.scenes.length))} sec</span><div className="flex items-center gap-2"><button type="button" disabled={index === 0} onClick={() => moveScene(index, -1)} className="text-xs text-[#b6c0bb]">↑ Up</button><button type="button" disabled={index === selected.scenes.length - 1} onClick={() => moveScene(index, 1)} className="text-xs text-[#b6c0bb]">↓ Down</button><button type="button" onClick={() => updateSelected({ scenes: selected.scenes.filter((_, sceneIndex) => sceneIndex !== index).map((item, sceneIndex) => ({ ...item, scene_number: sceneIndex + 1 })) })} className="text-[#ff9d87]" aria-label={`Delete scene ${index + 1}`}><Trash2 size={15} /></button></div></div><textarea value={scene.narration} onChange={(event) => updateSelected({ scenes: selected.scenes.map((item, sceneIndex) => sceneIndex === index ? { ...item, narration: event.target.value } : item) })} className="min-h-24 w-full rounded border border-[#3b4448] bg-transparent p-3 text-sm leading-relaxed text-[#f5f2ea]" placeholder="Narration" /><input value={scene.search_queries.join(", ")} onChange={(event) => updateSelected({ scenes: selected.scenes.map((item, sceneIndex) => sceneIndex === index ? { ...item, search_queries: event.target.value.split(",").map((query) => query.trim()).filter(Boolean) } : item) })} className="mt-3 w-full rounded border border-[#3b4448] bg-transparent p-3 text-sm text-[#b6c0bb]" placeholder="Pixabay search keywords, comma separated" /></div>)}<div className="flex flex-wrap justify-between gap-3"><button type="button" onClick={() => updateSelected({ scenes: [...selected.scenes, blankScene(selected.scenes.length + 1)] })} className="flex items-center gap-2 rounded-lg border border-[#3b4448] px-3 py-2 text-sm text-[#b6c0bb]"><Plus size={15} /> Add scene</button><button type="button" disabled={busy} onClick={() => void regenerateScript()} className="rounded-lg border border-[#3b4448] px-3 py-2 text-sm text-[#b6c0bb]">Regenerate Script</button><button type="button" disabled={busy || selected.scenes.length === 0} onClick={() => void queueScript()} className="flex items-center gap-2 rounded-lg bg-[#d3f36b] px-4 py-3 text-sm font-bold text-[#090b0f] disabled:opacity-40">{busy && <LoaderCircle className="animate-spin" size={16} />} Approve & Send to Render Queue</button></div></div></div></div>}</main>;
}
