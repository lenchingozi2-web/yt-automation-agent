import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
import type { SupabaseClient } from "@supabase/supabase-js";
import Image from "next/image";
import Link from "next/link";
import { ArrowDown, ArrowRight, CheckCircle2, CircleAlert, Clapperboard, Film, LoaderCircle, Play, X } from "lucide-react";

const POLICIES = "https://yt-automation-studio-gilt.vercel.app";
export type Feedback = { kind: "error" | "success"; message: string; detail?: string };

export function Brand() {
  return <Link href="/" className="brand" aria-label="LenxiQ Studio home">
    <span className="brand-icon"><Image src="/lenxiq.jpg" width={40} height={40} alt="" priority /></span>
    <span>LenxiQ <small>Studio</small></span>
  </Link>;
}

export function PolicyLinks() {
  return <div className="policy-links"><a href={POLICIES + "/privacy"}>Privacy</a><a href={POLICIES + "/terms"}>Terms</a></div>;
}

export function Notice({ feedback, dismiss }: { feedback: Feedback | null; dismiss?: () => void }) {
  if (!feedback) return null;
  const Icon = feedback.kind === "error" ? CircleAlert : CheckCircle2;
  return <div className={"notice notice-" + feedback.kind} role={feedback.kind === "error" ? "alert" : "status"}>
    <Icon size={19} /><div>{feedback.message}{feedback.detail && <small>{feedback.detail}</small>}</div>
    {dismiss && <button className="icon-button" aria-label="Dismiss message" onClick={dismiss}><X size={17} /></button>}
  </div>;
}

export function Modal({ children, titleId, close, className = "" }: { children: ReactNode; titleId: string; close: () => void; className?: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    element.showModal();
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { element.close(); document.body.style.overflow = previous; };
  }, []);
  return <dialog ref={dialog} className={"studio-dialog " + className} aria-labelledby={titleId}
    onCancel={(event) => { event.preventDefault(); close(); }}
    onClick={(event) => {
      if (event.target === event.currentTarget) {
        const bounds = event.currentTarget.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) close();
      }
    }}>{children}</dialog>;
}

export function SignIn({ supabase }: { supabase: SupabaseClient | null }) {
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  async function authenticate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !supabase) return;
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email")).trim();
    const password = String(form.get("password"));
    setBusy(true);
    setFeedback(null);
    try {
      const result = await supabase.auth.signInWithPassword({ email, password });
      if (result.error) throw result.error;
    } catch (error) {
      setFeedback({ kind: "error", message: error instanceof Error ? error.message : "Unable to sign in. Try again." });
    } finally {
      setBusy(false);
    }
  }

  return <div className="auth-page">
    <header className="auth-header"><Brand /><PolicyLinks /></header>
    <main className="auth-main">
      <section className="auth-intro">
        <div className="auth-visual" aria-hidden="true">
          <div className="visual-glow" />
          <div className="visual-back" />
          <div className="visual-frame"><div className="visual-film"><Play size={40} strokeWidth={1.3} /></div><div className="visual-timeline"><span /><span /><span /><span /></div></div>
        </div>
        <h1>LenxiQ Production Studio</h1>
        <p>Write scripts and render videos. Watch the finished MP4 and approve it before it goes to your YouTube channel.</p>
        <div className="auth-flow"><span><Clapperboard size={16} /> Script</span><ArrowDown size={14} className="flow-arrow" /><span><Film size={16} /> Render</span><ArrowDown size={14} className="flow-arrow" /><span><Play size={16} /> Upload</span></div>
      </section>
      <section className="auth-form-panel">
        <h2>Sign in to Studio</h2>
        <p>Use your authorized workspace account.</p>
        <form onSubmit={authenticate}>
          <label>Email address<input name="email" type="email" autoComplete="email" placeholder="you@example.com" required /></label>
          <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
          <Notice feedback={feedback ?? (!supabase ? { kind: "error", message: "Sign-in is temporarily unavailable." } : null)} />
          <button className="button button-primary auth-submit" disabled={busy || !supabase}>
            Sign in {busy ? <LoaderCircle className="spinner" size={18} /> : <ArrowRight size={18} />}
          </button>
        </form>
      </section>
    </main>
  </div>;
}
