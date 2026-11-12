"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ExternalLink, Film, LoaderCircle, RefreshCw } from "lucide-react";
import { getSupabaseClient } from "@/src/lib/supabase";
import { isBriefingSource, toSafeMessage } from "@/src/lib/studio";
import type { ResearchIdea } from "@/src/lib/studio-briefing";
import { Brand, Notice, PolicyLinks, SignIn, type Feedback } from "../components/studio-shell";

type BriefingData = { ideas: ResearchIdea[]; today: string };
const topicLabels = { health: "Health", education: "Education", intelligence: "Intelligence" };

async function studioRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { data } = await getSupabaseClient()!.auth.getSession();
  if (!data.session) throw new Error("Your session ended. Sign in again.");
  const response = await fetch(path, {
    ...init, cache: "no-store",
    headers: { Authorization: `Bearer ${data.session.access_token}`, ...(init.body ? { "Content-Type": "application/json" } : {}) },
  });
  const result = await response.json().catch(() => null);
  if (!response.ok || result?.ok !== true) throw new Error(toSafeMessage(result?.error, "Studio could not complete the request."));
  return result as T;
}

function Briefing() {
  const [data, setData] = useState<BriefingData | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [loading, setLoading] = useState(true);
  const [drafting, setDrafting] = useState<string | null>(null);
  const draftLock = useRef(false);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setFeedback(null);
    try {
      const next = await studioRequest<BriefingData>("/api/briefing", { signal });
      if (!signal?.aborted) setData(next);
    } catch (error) {
      if (!signal?.aborted) setFeedback({ kind: "error", message: toSafeMessage(error, "Could not load the briefing.") });
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => void refresh(controller.signal), 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [refresh]);

  async function draftVideo(idea: ResearchIdea) {
    if (draftLock.current) return;
    draftLock.current = true;
    setDrafting(idea.id);
    setFeedback(null);
    try {
      const result = await studioRequest<{ video?: { id: string }; note?: string }>("/api/generate", {
        method: "POST", body: JSON.stringify({ researchIdeaId: idea.id, autoRender: true, duration: 60, format: "9:16" }),
      });
      if (!result.video) throw new Error("The draft was created but Studio could not queue the render. Try again.");
      setData((current) => current && { ...current, ideas: current.ideas.map((row) => row.id === idea.id ? { ...row, status: "queued" } : row) });
      setFeedback({ kind: "success", message: "Video queued for review.", detail: result.note });
    } catch (error) {
      setFeedback({ kind: "error", message: toSafeMessage(error, "Could not queue this video.") });
    } finally {
      draftLock.current = false;
      setDrafting(null);
    }
  }

  const day = data?.ideas[0]?.briefing_date;
  const dateLabel = day ? new Intl.DateTimeFormat("en-GB", { dateStyle: "long", timeZone: "UTC" }).format(new Date(day + "T00:00:00Z")) : "";
  return <div className="studio">
    <a className="skip-link" href="#workspace">Skip to briefing</a>
    <header className="studio-header"><div className="header-inner">
      <Brand />
      <nav aria-label="Workspace"><Link href="/#compose">Create</Link><Link href="/#queue">Production queue</Link><Link href="/briefing" aria-current="page">Daily Briefing</Link></nav>
      <div className="account"><Link className="button button-quiet" href="/"><ArrowLeft size={16} /> Studio</Link></div>
    </div></header>
    <main className="workspace" id="workspace">
      <section className="workspace-heading">
        <div><h1>Daily Briefing</h1><p>Source-checked stories across health, education and intelligence.</p></div>
      </section>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[var(--muted)]">{day === data?.today ? dateLabel : day ? `Latest briefing · ${dateLabel}` : ""}</p>
        <button className="button button-secondary" onClick={() => void refresh()} disabled={loading || !!drafting}>
          <RefreshCw size={16} className={loading ? "spinner" : undefined} /> Refresh
        </button>
      </div>
      <Notice feedback={feedback} dismiss={() => setFeedback(null)} />
      {loading && !data && <p role="status">Loading briefing…</p>}
      {data && !data.ideas.length && <p className="panel p-5">The first briefing is being prepared. Check again after the daily research run.</p>}
      <div className="grid gap-4">
        {data?.ideas.map((idea) => <article key={idea.id} className="panel grid gap-4 p-5 sm:p-6">
          {idea.topic_area && <p className="text-sm text-[var(--accent)]">{topicLabels[idea.topic_area]}</p>}
          <h2 className="text-xl font-semibold leading-snug">{idea.title}</h2>
          <p className="text-[var(--muted)]">{idea.hook_summary}</p>
          <ul className="flex flex-wrap gap-x-4 gap-y-2" aria-label="Sources">
            {idea.references.filter(isBriefingSource).map((url, index) => <li key={url}>
              <a className="inline-flex items-center gap-1.5 text-sm text-[var(--accent)] underline-offset-4 hover:underline" href={url} target="_blank" rel="noreferrer">
                {index === 0 ? "Lead report" : `Supporting source ${index}`} <ExternalLink size={13} />
              </a>
            </li>)}
          </ul>
          <div className="flex flex-wrap gap-3">
            {idea.status === "queued" ? <Link className="button button-secondary" href="/#queue">Open production queue</Link> :
              <button className="button button-primary" onClick={() => void draftVideo(idea)} disabled={!!drafting || loading}>
                {drafting === idea.id ? <><LoaderCircle className="spinner" size={17} /> Drafting and checking…</> : <><Film size={17} /> Draft Video</>}
              </button>}
          </div>
        </article>)}
      </div>
    </main>
    <footer className="studio-footer"><span>LenxiQ Studio</span><PolicyLinks /></footer>
  </div>;
}

export default function BriefingPage() {
  const [userId, setUserId] = useState<string | null | undefined>(undefined);
  const supabase = getSupabaseClient();
  useEffect(() => {
    if (!supabase) return;
    const { data } = supabase.auth.onAuthStateChange((_event, session) => setUserId(session?.user.id ?? null));
    return () => data.subscription.unsubscribe();
  }, [supabase]);
  if (supabase && userId === undefined) return <main className="startup"><Brand /><p role="status">Opening briefing…</p></main>;
  if (!supabase || !userId) return <SignIn supabase={supabase} />;
  return <Briefing key={userId} />;
}
