"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { User } from "@supabase/supabase-js";
import Link from "next/link";
import { Film, HardDrive, LoaderCircle, Newspaper, Plus, Workflow } from "lucide-react";
import { getSupabaseClient } from "@/src/lib/supabase";
import { normalizeDraft, normalizeVideo, toSafeMessage, type Draft, type StudioVideo } from "@/src/lib/studio";
import { Brand, Notice, PolicyLinks, SignIn, type Feedback } from "./components/studio-shell";
import { Composer } from "./components/composer";
import { ProductionQueue, type RevisionInput } from "./components/production-queue";
import { SceneEditor } from "./components/scene-editor";

type Quota = { used: number; limit: number };
type StorageInfo = {
  buckets: { bytes: number; available: boolean; partial: boolean }[];
  retention: { published_preview_days: number; published_thumbnail_days: number };
  immediate_dispatch_available: boolean;
};
type QueueData = { videos: StudioVideo[]; quota: Quota };
type InstallEvent = Event & { prompt: () => Promise<unknown> };
const WORKFLOW = "https://github.com/lenchingozi2-web/yt-automation-agent/actions/workflows/daily_video.yml";

function useWorkspace(user: User, request: <T>(path: string, init?: RequestInit) => Promise<T>) {
  const [data, setData] = useState<QueueData | null>(null);
  const [storage, setStorage] = useState<StorageInfo | null>(null);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [storageLoading, setStorageLoading] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const pendingStorage = useRef<AbortController | null>(null);
  const active = data?.videos.some((video) => video.status === "pending" || video.status === "rendering" ||
    video.publish_status === "pending" || video.publish_status === "uploading");

  const refresh = useCallback(async (manual = false) => {
    if (pending.current && !manual) return;
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    if (manual) setRefreshing(true);
    try {
      const result = await request<QueueData>("/api/videos", { signal: controller.signal });
      if (controller.signal.aborted) return;
      setData({ ...result, videos: result.videos.map(normalizeVideo) });
      setError("");
    } catch (failure) {
      if (!controller.signal.aborted) setError(toSafeMessage(failure, "The queue could not be refreshed."));
    } finally {
      if (pending.current === controller) {
        pending.current = null;
        setRefreshing(false);
      }
    }
  }, [request]);

  const refreshStorage = useCallback(async () => {
    if (pendingStorage.current) return;
    const controller = new AbortController();
    pendingStorage.current = controller;
    setStorageLoading(true);
    try {
      const result = await request<StorageInfo>("/api/storage", { signal: controller.signal });
      if (!controller.signal.aborted) setStorage(result);
    } catch {
      if (!controller.signal.aborted) setStorage(null);
    } finally {
      if (pendingStorage.current === controller) {
        pendingStorage.current = null;
        setStorageLoading(false);
      }
    }
  }, [request]);

  useEffect(() => {
    const initial = window.setTimeout(() => { void refresh(); void refreshStorage(); }, 0);
    return () => {
      window.clearTimeout(initial);
      pending.current?.abort();
      pendingStorage.current?.abort();
      pending.current = null;
      pendingStorage.current = null;
    };
  }, [user.id, refresh, refreshStorage]);

  useEffect(() => {
    function refreshVisible() {
      if (document.visibilityState === "visible") void refresh();
    }
    const timer = window.setInterval(refreshVisible, active ? 5000 : 30000);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, [active, refresh]);

  return { data, storage, error, refreshing, storageLoading, refresh, refreshStorage };
}

function Workspace({ user, install, signOut }: { user: User; install: (() => void) | null; signOut: () => Promise<void> }) {
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [topic, setTopic] = useState("");
  const [editorFeedback, setEditorFeedback] = useState<Feedback | null>(null);
  const actionLock = useRef(false);
  const draftId = useRef<string | null>(null);
  const request = useCallback(async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
    const { data } = await getSupabaseClient()!.auth.getSession();
    if (!data.session?.access_token) throw new Error("Your session ended. Sign in again.");
    const response = await fetch(path, {
      ...init, cache: "no-store",
      headers: { Authorization: "Bearer " + data.session.access_token, ...(init.body ? { "Content-Type": "application/json" } : {}) },
    });
    const result = await response.json().catch(() => null);
    if (!response.ok || result?.ok !== true) throw new Error(toSafeMessage(result?.error, "Studio could not complete the request. Try again."));
    return result as T;
  }, []);
  const { data, storage, error, refreshing, storageLoading, refresh, refreshStorage } = useWorkspace(user, request);
  const availableBuckets = storage?.buckets.filter((bucket) => bucket.available) ?? [];
  const bytes = availableBuckets.reduce((total, bucket) => total + bucket.bytes, 0);
  const storageLabel = storageLoading && !storage ? "Checking…" : availableBuckets.length ? (bytes / 1048576).toLocaleString(undefined, { maximumFractionDigits: 1 }) + " MB" : "Unavailable";

  function edit(next: Draft) {
    draftId.current = crypto.randomUUID();
    setDraft(next);
    setEditorFeedback(null);
  }

  async function action(key: string, perform: () => Promise<void>, inEditor = false) {
    if (actionLock.current) return false;
    actionLock.current = true;
    setBusy(key);
    (inEditor ? setEditorFeedback : setFeedback)(null);
    try {
      await perform();
      return true;
    } catch (failure) {
      (inEditor ? setEditorFeedback : setFeedback)({ kind: "error", message: toSafeMessage(failure, "The action could not be completed.") });
      return false;
    } finally {
      actionLock.current = false;
      setBusy(null);
    }
  }

  function post<T>(path: string, payload: object) {
    return request<T>(path, { method: "POST", body: JSON.stringify(payload) });
  }

  type GenerateResult = { draft: Draft; video?: StudioVideo; message: string; note?: string };
  type RenderResult = { message: string; note?: string };

  async function generate(prompt: string, duration: number, format: Draft["format"], review: boolean, requestId: string) {
    return action("generate", async () => {
      setTopic(prompt);
      const result = await post<GenerateResult>("/api/generate", { prompt, duration, format, autoRender: !review, requestId });
      if (review || !result.video) {
        edit(normalizeDraft(result.draft));
        if (!review) {
          draftId.current = requestId;
          setEditorFeedback({ kind: "error", message: result.message, detail: result.note });
        }
        return;
      }
      setFeedback({ kind: "success", message: result.message, detail: result.note });
      void refresh(true);
    });
  }

  async function saveDraft() {
    if (!draft) return;
    await action("save", async () => {
      if (!draft.title.trim() || !draft.scenes.length || draft.scenes.some((scene) => !scene.narration.trim())) {
        throw new Error("Add a title and narration for every scene.");
      }
      if (!Number.isInteger(draft.duration_seconds) || draft.duration_seconds < 60 || draft.duration_seconds > 720) {
        throw new Error("Set a target duration between 60 and 720 seconds.");
      }
      const result = await post<RenderResult>("/api/render", { ...draft, requestId: draftId.current });
      setDraft(null);
      setFeedback({ kind: "success", message: result.message, detail: result.note });
      void refresh(true);
    }, true);
  }

  async function regenerate() {
    if (!draft) return;
    await action("regenerate", async () => {
      const result = await post<GenerateResult>("/api/generate", { prompt: topic || draft.title, duration: draft.duration_seconds, format: draft.format });
      edit(normalizeDraft(result.draft));
      setEditorFeedback({ kind: "success", message: "Script regenerated." });
    }, true);
  }

  async function retry(video: StudioVideo, publish = false) {
    return action(video.id, async () => {
      const result = await post<RenderResult>("/api/render", { id: video.id, ...(publish ? { action: "publish", approved: true } : {}) });
      setFeedback({ kind: "success", message: result.message, detail: result.note });
      void refresh(true);
    });
  }

  async function revise(video: StudioVideo, input: RevisionInput) {
    return action(video.id, async () => {
      const result = await post<RenderResult>("/api/revise", { id: video.id, ...input });
      setFeedback({ kind: "success", message: result.message, detail: result.note });
      void refresh(true);
    });
  }

  async function retryThumbnail(video: StudioVideo) {
    return action(video.id, async () => {
      const result = await post<RenderResult>("/api/render", { id: video.id, action: "retry_thumbnail" });
      setFeedback({ kind: "success", message: result.message });
      void refresh(true);
    });
  }

  return (
    <div className="studio">
      <a className="skip-link" href="#workspace">Skip to workspace</a>
      <header className="studio-header">
        <div className="header-inner">
          <Brand />
          <nav aria-label="Workspace">
            <a href="#compose">Create</a>
            <a href="#queue">Production queue</a>
            <Link href="/briefing">Daily Briefing</Link>
          </nav>
          <div className="account">
            {install && <button className="button button-quiet install-button" onClick={install}>Install app</button>}
            <button className="button button-quiet" title={user.email} onClick={() => void action("sign-out", signOut)} disabled={busy === "sign-out"}>Sign out</button>
          </div>
        </div>
      </header>
      <main className="workspace" id="workspace">
        <section className="workspace-heading">
          <div><h1>Production Studio</h1><p>Create videos, watch the finished MP4, and approve each YouTube upload.</p></div>
          <a className="button button-primary" href="#compose"><Plus size={17} /> New production</a>
        </section>
        <Link className="button button-secondary self-start min-[651px]:hidden" href="/briefing"><Newspaper size={16} /> Daily Briefing</Link>

        <section className="metrics" aria-label="Studio overview">
          <article className="metric">
            <div className="metric-label"><Workflow size={17} /> Daily automation</div>
            <strong>Renders for review</strong>
            <a href={WORKFLOW} target="_blank" rel="noreferrer">View runs on GitHub ↗</a>
          </article>
          <article className="metric">
            <div className="metric-label"><Film size={17} /> Today’s production</div>
            <strong>{data?.quota ? `${data.quota.used} / ${data.quota.limit}` : error ? "Unavailable" : "—"}</strong>
            <p>Resets at midnight WAT</p>
          </article>
          <article className="metric">
            <div className="metric-label"><HardDrive size={17} /> Media storage</div>
            <strong>{storageLabel}</strong>
            {storage && <p>Uploaded previews expire after {storage.retention.published_preview_days} days. Unpublished previews stay available.</p>}
            <button className="text-button" disabled={storageLoading} onClick={() => void refreshStorage()}>
              {storageLoading ? "Checking storage…" : "Refresh usage"}
            </button>
            {storage?.immediate_dispatch_available && <button type="button" className="text-button" disabled={!!busy} onClick={() => void action("cleanup", async () => {
              const result = await request<{ message: string }>("/api/storage", { method: "POST" });
              setFeedback({ kind: "success", message: result.message });
            })}>{busy === "cleanup" ? "Starting cleanup…" : "Clean expired media"}</button>}
          </article>
        </section>
        <Composer id="compose" busy={busy === "generate"} disabled={busy !== null} generate={generate} edit={(next) => { setTopic(""); edit(next); }} />
        <Notice feedback={feedback} dismiss={() => setFeedback(null)} />
        <ProductionQueue
          videos={data?.videos ?? []} loading={!data && !error}
          error={error} refreshing={refreshing} refresh={() => void refresh(true)}
          busy={busy} retry={retry} retryThumbnail={retryThumbnail} revise={revise} feedback={feedback} reuse={(video) => { setTopic(""); edit(normalizeDraft(video)); }}
        />
      </main>
      <footer className="studio-footer"><span>LenxiQ Studio</span><PolicyLinks /></footer>
      {draft && <SceneEditor draft={draft} change={setDraft} busy={busy} close={() => setDraft(null)}
        save={() => void saveDraft()} regenerate={() => void regenerate()} feedback={editorFeedback} />}
    </div>
  );
}

export default function Home() {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [install, setInstall] = useState<InstallEvent | null>(null);
  const supabase = getSupabaseClient();

  useEffect(() => {
    if (!supabase) return;
    const { data } = supabase.auth.onAuthStateChange((_event, session) => setUser(session?.user ?? null));
    return () => data.subscription.unsubscribe();
  }, [supabase]);

  useEffect(() => {
    function onInstall(event: Event) {
      event.preventDefault();
      setInstall(event as InstallEvent);
    }
    function onInstalled() { setInstall(null); }
    window.addEventListener("beforeinstallprompt", onInstall);
    window.addEventListener("appinstalled", onInstalled);
    if ("serviceWorker" in navigator) void navigator.serviceWorker.register("/sw.js").catch(() => undefined);
    return () => {
      window.removeEventListener("beforeinstallprompt", onInstall);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  if (supabase && user === undefined) return <main className="startup"><Brand /><p role="status"><LoaderCircle className="spinner" size={18} /> Opening workspace…</p></main>;
  if (!supabase || !user) return <SignIn supabase={supabase} />;
  return <Workspace key={user.id} user={user} install={install ? () => { void install.prompt().finally(() => setInstall(null)); } : null}
    signOut={async () => { const { error } = await supabase.auth.signOut(); if (error) throw error; }} />;
}
