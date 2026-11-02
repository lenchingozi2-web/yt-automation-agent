"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { ArrowUpRight, Check, ChevronRight, Download, LoaderCircle, LockKeyhole, LogOut, Play, Plus, RefreshCw, Sparkles, X } from "lucide-react";
import { getSupabaseClient } from "@/src/lib/supabase";

type Scene = { scene_number: number; narration: string; search_queries?: string[] };
type Video = { id: string; title: string; description?: string; tags?: string[]; scenes?: Scene[]; status: string; youtube_url?: string; youtube_id?: string; created_at?: string };

const statuses = ["pending", "rendering", "completed", "failed"];

function Logo() {
  return <div className="flex items-center gap-3"><div className="grid h-10 w-10 place-items-center rounded-xl bg-[#d3f36b] text-[#090b0f]"><svg width="32" height="32" style={{ width: 32, height: 32, flexShrink: 0 }} viewBox="0 0 32 32" aria-hidden="true"><rect x="4" y="6" width="24" height="20" rx="6" fill="currentColor" /><path d="m13 11 9 5-9 5v-10Z" fill="#090b0f" /></svg></div><div><div className="text-[15px] font-bold tracking-tight">YT Automation</div><div className="mono text-[10px] uppercase tracking-[.22em] text-[#8e958f]">Studio / 01</div></div></div>;
}

function statusStyle(status: string) {
  if (status === "completed") return "border-[#5d8a42] bg-[#17241a] text-[#b8e889]";
  if (status === "failed") return "border-[#7a4036] bg-[#281b1a] text-[#ff9d87]";
  if (status === "rendering") return "border-[#876c34] bg-[#282215] text-[#f3ca73]";
  return "border-[#3c4d54] bg-[#182126] text-[#b2c5cb]";
}

function youtubeLink(video: Video) {
  if (video.youtube_url) return video.youtube_url;
  if (video.youtube_id) return `https://www.youtube.com/watch?v=${video.youtube_id}`;
  return null;
}

export default function Home() {
  const supabase = getSupabaseClient();
  const [videos, setVideos] = useState<Video[]>([]);
  const [prompt, setPrompt] = useState("");
  const [selected, setSelected] = useState<Video | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [user, setUser] = useState<{ email?: string } | null>(null);
  const [authMode, setAuthMode] = useState<"signin" | "signup">("signin");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [installEvent, setInstallEvent] = useState<Event | null>(null);

  const loadVideos = useCallback(async () => {
    if (!supabase) return;
    const { data } = await supabase.from("videos").select("*").order("created_at", { ascending: false });
    setVideos((data as Video[]) ?? []);
  }, [supabase]);

  useEffect(() => {
    if (!supabase) {
      return;
    }
    supabase.auth.getUser().then(({ data }) => setUser(data.user ? { email: data.user.email } : null));
    const { data: listener } = supabase.auth.onAuthStateChange((_event, session) => setUser(session?.user ? { email: session.user.email } : null));
    const initialLoad = window.setTimeout(() => void loadVideos(), 0);
    const timer = window.setInterval(loadVideos, 12000);
    const onInstall = (event: Event) => { event.preventDefault(); setInstallEvent(event); };
    window.addEventListener("beforeinstallprompt", onInstall);
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => undefined);
    return () => { listener.subscription.unsubscribe(); window.clearTimeout(initialLoad); window.clearInterval(timer); window.removeEventListener("beforeinstallprompt", onInstall); };
  }, [loadVideos, supabase]);

  const counts = useMemo(() => statuses.reduce<Record<string, number>>((result, status) => { result[status] = videos.filter((video) => video.status === status).length; return result; }, {}), [videos]);

  async function authenticate(event: FormEvent) {
    event.preventDefault(); setBusy(true); setNotice("");
    if (!supabase) {
      setNotice("Connect Supabase environment variables to sign in.");
      setBusy(false);
      return;
    }
    const response = authMode === "signin" ? await supabase.auth.signInWithPassword({ email, password }) : await supabase.auth.signUp({ email, password });
    setNotice(response.error?.message ?? (authMode === "signup" ? "Check your email to confirm your account." : "Welcome back."));
    setBusy(false);
  }

  async function generate(event: FormEvent) {
    event.preventDefault(); if (!prompt.trim()) return;
    setBusy(true); setNotice("");
    const response = await fetch("/api/generate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt }) });
    const result = await response.json();
    if (!response.ok) setNotice(result.error ?? "Generation failed."); else { setPrompt(""); await loadVideos(); setNotice("Script added to the queue."); }
    setBusy(false);
  }

  async function render(video: Video) {
    setBusy(true); setNotice("");
    const response = await fetch("/api/render", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: video.id }) });
    const result = await response.json();
    if (!response.ok) setNotice(result.error ?? "Could not start rendering."); else await loadVideos();
    setBusy(false);
  }

  async function install() {
    if (!installEvent) return;
    const promptEvent = installEvent as Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> };
    await promptEvent.prompt(); setInstallEvent(null);
  }

  if (!user) return <main className="grain min-h-screen px-5 py-5 sm:px-10"><header className="mx-auto flex max-w-7xl items-center justify-between"><Logo /><span className="mono hidden text-xs text-[#8e958f] sm:block">PRIVATE WORKSPACE</span></header><section className="mx-auto flex min-h-[calc(100vh-90px)] max-w-7xl items-center justify-center py-10"><div className="auth-card"><p className="mono mb-5 text-xs uppercase tracking-[.28em] text-[#d3f36b]">Your publishing desk</p><h1 className="mb-5 text-5xl leading-[.98] tracking-[-.04em] sm:text-6xl">Make the next<br /><i>good video.</i></h1><p className="mb-9 max-w-sm text-lg leading-relaxed text-[#8e958f]">A quiet command center for turning a sharp idea into a ready-to-render YouTube story.</p><form onSubmit={authenticate}><label className="mono mb-2 block text-[10px] uppercase tracking-[.2em] text-[#8e958f]">Email</label><input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required placeholder="you@studio.com" /><label className="mono mb-2 block text-[10px] uppercase tracking-[.2em] text-[#8e958f]">Password</label><input value={password} onChange={(event) => setPassword(event.target.value)} type="password" required minLength={6} placeholder="At least 6 characters" /><button disabled={busy} className="primary-action flex items-center justify-center gap-2">{busy ? <LoaderCircle className="animate-spin" size={17} width={17} height={17} /> : <LockKeyhole size={17} width={17} height={17} />}{authMode === "signin" ? "Sign in" : "Create account"}</button></form><button onClick={() => setAuthMode(authMode === "signin" ? "signup" : "signin")} className="secondary-action mt-3 text-sm underline decoration-[#3b4448] underline-offset-4 hover:text-[#f5f2ea]">{authMode === "signin" ? "New here? Create an account" : "Already have an account? Sign in"}</button>{notice && <p className="mt-5 text-sm text-[#ff9d87]">{notice}</p>}</div></section></main>;

  return <main className="grain min-h-screen"><header className="sticky top-0 z-10 border-b border-[#252b2e] bg-[#090b0f]/95 px-5 py-4 backdrop-blur sm:px-10"><div className="mx-auto flex max-w-7xl items-center justify-between"><Logo /><nav className="flex items-center gap-3"><button onClick={install} disabled={!installEvent} className="flex items-center gap-2 rounded-full border border-[#3b4448] px-3 py-2 text-xs text-[#b6c0bb] transition hover:border-[#d3f36b] hover:text-[#d3f36b] disabled:opacity-40"><Download size={14} /><span className="hidden sm:inline">Install App</span></button><button onClick={() => supabase?.auth.signOut()} aria-label="Sign out" className="rounded-full border border-[#3b4448] p-2 text-[#8e958f] hover:text-[#f5f2ea]"><LogOut size={15} /></button></nav></div></header><div className="mx-auto max-w-7xl px-5 py-8 sm:px-10 sm:py-12"><div className="mb-10 flex flex-col justify-between gap-5 md:flex-row md:items-end"><div><p className="mono mb-3 text-xs uppercase tracking-[.28em] text-[#d3f36b]">Dashboard / Overview</p><h1 className="text-4xl tracking-[-.04em] sm:text-6xl">The story room.</h1></div><div className="flex items-center gap-3 text-sm text-[#8e958f]"><span className="h-2 w-2 animate-pulse rounded-full bg-[#d3f36b]" />Queue syncs every 12 sec</div></div><section className="mb-10 grid gap-4 lg:grid-cols-[1.35fr_.65fr]"><form onSubmit={generate} className="rounded-xl border border-[#3c4d54] bg-[#11161a] p-5 sm:p-7"><div className="mb-8 flex items-center justify-between"><div><div className="mono mb-2 text-[10px] uppercase tracking-[.22em] text-[#8e958f]">New production</div><h2 className="text-2xl">What are we making?</h2></div><Sparkles className="text-[#d3f36b]" size={22} /></div><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} className="min-h-32 w-full resize-y border-b border-[#3b4448] bg-transparent py-3 text-xl leading-relaxed outline-none placeholder:text-[#566166] focus:border-[#d3f36b]" placeholder="A 90-second essay on why small teams build better tools..." /><div className="mt-5 flex items-center justify-between gap-3"><span className="mono hidden text-[10px] uppercase tracking-[.16em] text-[#566166] sm:block">Gemini / Flash / JSON</span><button disabled={busy || !prompt.trim()} className="flex items-center gap-2 rounded-lg bg-[#d3f36b] px-4 py-3 text-sm font-bold text-[#090b0f] hover:bg-[#e1fa8f] disabled:opacity-40">{busy ? <LoaderCircle className="animate-spin" size={16} /> : <Plus size={16} />}{busy ? "Working..." : "Generate script"}</button></div></form><div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-2"><div className="rounded-xl border border-[#252b2e] bg-[#11161a] p-5"><div className="mono text-[10px] uppercase tracking-[.18em] text-[#8e958f]">Pending</div><div className="mt-5 text-4xl">{counts.pending ?? 0}</div></div><div className="rounded-xl border border-[#252b2e] bg-[#11161a] p-5"><div className="mono text-[10px] uppercase tracking-[.18em] text-[#8e958f]">Rendering</div><div className="mt-5 text-4xl text-[#f3ca73]">{counts.rendering ?? 0}</div></div><div className="rounded-xl border border-[#252b2e] bg-[#11161a] p-5"><div className="mono text-[10px] uppercase tracking-[.18em] text-[#8e958f]">Completed</div><div className="mt-5 text-4xl text-[#b8e889]">{counts.completed ?? 0}</div></div><div className="rounded-xl border border-[#252b2e] bg-[#11161a] p-5"><div className="mono text-[10px] uppercase tracking-[.18em] text-[#8e958f]">Failed</div><div className="mt-5 text-4xl text-[#ff9d87]">{counts.failed ?? 0}</div></div></div></section>{notice && <div className="mb-6 flex items-center gap-2 border-l-2 border-[#d3f36b] bg-[#141b18] px-4 py-3 text-sm text-[#d3f36b]"><Check size={16} />{notice}</div>}<div className="mb-4 flex items-center justify-between"><div><div className="mono text-[10px] uppercase tracking-[.22em] text-[#8e958f]">Production queue</div><h2 className="mt-2 text-2xl">Recent stories</h2></div><button onClick={loadVideos} className="rounded-full border border-[#3b4448] p-2 text-[#8e958f] hover:text-[#d3f36b]" aria-label="Refresh queue"><RefreshCw size={15} /></button></div><div className="overflow-hidden rounded-xl border border-[#252b2e] bg-[#11161a]">{videos.length === 0 ? <div className="px-5 py-16 text-center text-[#8e958f]"><p className="mb-2 text-xl text-[#f5f2ea]">The queue is clear.</p><p className="text-sm">Your next idea belongs in the composer above.</p></div> : videos.map((video) => { const link = youtubeLink(video); return <div key={video.id} className="grid gap-4 border-b border-[#252b2e] px-5 py-5 last:border-0 sm:grid-cols-[1fr_auto] sm:items-center"><div className="min-w-0"><div className="mb-2 flex flex-wrap items-center gap-3"><span className={`mono rounded-full border px-2 py-1 text-[9px] uppercase tracking-[.16em] ${statusStyle(video.status)}`}>{video.status}</span><span className="mono text-[10px] text-[#566166]">{video.created_at ? new Date(video.created_at).toLocaleDateString() : "new"}</span></div><h3 className="truncate text-xl">{video.title || "Untitled production"}</h3><p className="mt-1 truncate text-sm text-[#8e958f]">{video.description || "No description yet"}</p></div><div className="flex items-center gap-2 sm:justify-end"><button onClick={() => setSelected(video)} className="flex items-center gap-1 rounded-lg border border-[#3b4448] px-3 py-2 text-sm text-[#b6c0bb] hover:border-[#d3f36b] hover:text-[#d3f36b]">Edit <ChevronRight size={14} /></button>{video.status === "pending" && <button onClick={() => render(video)} className="flex items-center gap-2 rounded-lg bg-[#d3f36b] px-3 py-2 text-sm font-bold text-[#090b0f] hover:bg-[#e1fa8f]"><Play size={14} /> Render</button>}{link && <a href={link} target="_blank" rel="noreferrer" className="rounded-lg border border-[#5d8a42] p-2 text-[#b8e889]" aria-label="Watch on YouTube"><ArrowUpRight size={16} /></a>}</div></div>; })}</div></div>{selected && <div className="fixed inset-0 z-30 grid place-items-center bg-black/70 p-4" onClick={() => setSelected(null)}><div className="max-h-[90vh] w-full max-w-3xl overflow-auto rounded-xl border border-[#3c4d54] bg-[#11161a]" onClick={(event) => event.stopPropagation()}><div className="flex items-start justify-between border-b border-[#252b2e] p-5 sm:p-7"><div><div className="mono mb-2 text-[10px] uppercase tracking-[.2em] text-[#8e958f]">Script editor</div><h2 className="text-2xl">{selected.title}</h2></div><button onClick={() => setSelected(null)} className="text-[#8e958f] hover:text-[#f5f2ea]" aria-label="Close editor"><X size={20} /></button></div><div className="space-y-5 p-5 sm:p-7">{(selected.scenes ?? []).map((scene, index) => <div key={`${selected.id}-${index}`} className="border-l-2 border-[#3c4d54] pl-4"><div className="mono mb-2 text-[10px] uppercase tracking-[.18em] text-[#d3f36b]">Scene {scene.scene_number || index + 1}</div><textarea defaultValue={scene.narration} className="min-h-24 w-full resize-y rounded-lg border border-[#252b2e] bg-[#0b1013] p-3 text-sm leading-relaxed outline-none focus:border-[#d3f36b]" /><div className="mt-2 flex flex-wrap gap-2">{(scene.search_queries ?? []).map((query) => <span key={query} className="mono rounded bg-[#1a2529] px-2 py-1 text-[10px] text-[#8e958f]">{query}</span>)}</div></div>)}</div></div></div>}</main>;
}