import { type FormEvent, useRef, useState } from "react";
import { ArrowRight, Clapperboard, FileText, LoaderCircle, Monitor, Smartphone, Sparkles } from "lucide-react";
import { durationFromBrief, type Draft } from "@/src/lib/studio";
import { parseFullScript } from "@/src/lib/studio-import";
import { MAX_VIDEO_MINUTES } from "@/src/lib/studio-limits";

const DURATIONS = [[60, "1 minute"], [90, "90 seconds"], [180, "3 minutes"], [300, "5 minutes"], [480, "8 minutes"], [600, "10 minutes"], [720, "12 minutes"], [900, "15 minutes"], [1200, "20 minutes"], [1500, "25 minutes"], [1800, "30 minutes"], [0, "Custom"]] as const;

export function Composer({ id, busy, disabled, generate, edit }: {
  id: string;
  busy: boolean;
  disabled: boolean;
  generate: (prompt: string, seconds: number, format: Draft["format"], review: boolean, requestId: string) => Promise<boolean>;
  edit: (draft: Draft, notes?: string[]) => void;
}) {
  const [mode, setMode] = useState<"generate" | "paste">("generate");
  const [format, setFormat] = useState<Draft["format"]>("16:9");
  const [formatChosen, setFormatChosen] = useState(false);
  const [pasteError, setPasteError] = useState("");
  const [duration, setDuration] = useState(180);
  const [durationChosen, setDurationChosen] = useState(false);
  const [custom, setCustom] = useState("3");
  const [prompt, setPrompt] = useState("");
  const [script, setScript] = useState("");
  const [review, setReview] = useState(false);
  const request = useRef<{ signature: string; id: string } | null>(null);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const seconds = mode === "generate" && !durationChosen ? durationFromBrief(prompt) ?? duration : duration || Math.round(Number(custom) * 60);
    if (mode === "paste") {
      try {
        const imported = parseFullScript(script, {
          format, formatChosen, durationSeconds: durationChosen ? seconds : undefined,
        });
        setPasteError("");
        edit(imported.drafts[0], imported.notes);
      } catch (error) {
        setPasteError(error instanceof Error ? error.message : "Studio could not parse the pasted content.");
      }
      return;
    }
    const signature = JSON.stringify([prompt.trim(), seconds, format, review]);
    if (request.current?.signature !== signature) request.current = { signature, id: crypto.randomUUID() };
    const current = request.current;
    void generate(prompt.trim(), seconds, format, review, current.id).then((completed) => {
      if (completed && request.current === current) request.current = null;
    });
  }

  return <section className="panel composer" id={id} aria-labelledby="compose-title">
    <div className="section-heading">
      <div className="section-icon"><Clapperboard size={21} /></div>
      <div><h2 id="compose-title">Create a video</h2><p>Start with a brief or paste an entire ready-made video script.</p></div>
    </div>
    <div className="mode-switch" role="group" aria-label="Script source">
      <button type="button" aria-pressed={mode === "generate"} onClick={() => setMode("generate")}><Sparkles size={16} /> Generate script</button>
      <button type="button" aria-pressed={mode === "paste"} onClick={() => setMode("paste")}><FileText size={16} /> Paste full content</button>
    </div>
    <form onSubmit={submit}>
      <label>{mode === "generate" ? "Video brief" : "Your full video content — paste everything once"}
        {mode === "generate"
          ? <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} minLength={3} maxLength={4000} required
              placeholder="Explain how medical students can use active recall. Include a practical example." />
          : <textarea value={script} onChange={(event) => setScript(event.target.value)} required
              placeholder={"Title: My YouTube tutorial\nDuration: 20 minutes\nFormat: 16:9\nThumbnail: Build Your AI Engine\n\n## Scene 1 — Opening\nNarration:\nImagine making videos automatically...\nVisuals:\nA phone displaying a production dashboard\n\n## Scene 2 — How it works\nNarration:\nFirst, create a GitHub repository..."} rows={12} />}
      </label>
      {mode === "paste" && <p className="text-sm text-[var(--muted)]">One paste imports the title, narration, scenes, visual cues, description, tags, thumbnail text and duration. Up to 30 minutes of narration stays in ONE video with all scenes preserved. Your work is never published automatically.</p>}
      {mode === "paste" && pasteError && <p className="queue-warning" role="alert">{pasteError}</p>}
      <div className="composer-settings">
        <fieldset>
          <legend>Format</legend>
          <div className="format-switch">
            <button type="button" aria-pressed={format === "16:9"} onClick={() => { setFormat("16:9"); setFormatChosen(true); }}><Monitor size={17} /> Landscape <small>16:9</small></button>
            <button type="button" aria-pressed={format === "9:16"} onClick={() => { setFormat("9:16"); setFormatChosen(true); }}><Smartphone size={17} /> Portrait <small>9:16</small></button>
          </div>
        </fieldset>
        <label>Approximate duration (up to 30 min)<select value={duration} onChange={(event) => { setDurationChosen(true); setDuration(Number(event.target.value)); }}>
          {DURATIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        {duration === 0 && <label>Minutes<input type="number" min="1" max={MAX_VIDEO_MINUTES} step="any" value={custom} onChange={(event) => setCustom(event.target.value)} required /></label>}
      </div>
      {mode === "generate" && !durationChosen && durationFromBrief(prompt) && durationFromBrief(prompt) !== duration &&
        <p className="text-sm text-[var(--muted)]">Using the duration in your brief. You can override it above.</p>}
      <div className="composer-footer">
        {mode === "generate"
          ? <label className="checkbox"><input type="checkbox" checked={review} onChange={(event) => setReview(event.target.checked)} /> Review the script before rendering</label>
          : <button type="button" className="text-button" disabled={disabled} onClick={() => edit({
              title: "New production", description: "", tags: [], format,
              duration_seconds: duration || Math.round(Number(custom) * 60) || 180,
              scenes: [{ scene_number: 1, narration: "", search_queries: ["cinematic b-roll"] }],
            })}>Start with a blank scene</button>}
        <button className="button button-primary" disabled={disabled || (mode === "generate" ? prompt.trim().length < 3 : !script.trim())}>
          {busy ? "Generating script…" : mode === "paste" ? "Parse & review all scenes" : review ? "Generate script" : "Generate video"}
          {busy ? <LoaderCircle className="spinner" size={17} /> : <ArrowRight size={17} />}
        </button>
      </div>
    </form>
  </section>;
}
