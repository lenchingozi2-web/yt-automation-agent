import { ArrowDown, ArrowUp, Clapperboard, LoaderCircle, Plus, RefreshCw, Trash2, X } from "lucide-react";
import type { Draft, Scene } from "@/src/lib/studio";
import { Modal, Notice, type Feedback } from "./studio-shell";

export function SceneEditor({ draft, change, busy, close, save, regenerate, feedback }: {
  draft: Draft; change: (draft: Draft) => void; busy: string | null; close: () => void; save: () => void;
  regenerate: () => void; feedback: Feedback | null;
}) {
  const working = busy === "save" || busy === "regenerate";
  function update(patch: Partial<Draft>) { change({ ...draft, ...patch }); }
  function updateScenes(scenes: Scene[]) {
    update({ scenes: scenes.map((scene, index) => ({ ...scene, scene_number: index + 1 })) });
  }
  function updateScene(index: number, patch: Partial<Scene>) {
    updateScenes(draft.scenes.map((scene, i) => i === index ? { ...scene, ...patch } : scene));
  }
  function moveScene(index: number, offset: number) {
    const next = [...draft.scenes];
    const [scene] = next.splice(index, 1);
    next.splice(index + offset, 0, scene);
    updateScenes(next);
  }
  const safeClose = () => { if (!working) close(); };

  return <Modal titleId="editor-title" close={safeClose} className="editor-dialog">
    <header className="dialog-header">
      <div><p>{draft.scenes.length} {draft.scenes.length === 1 ? "scene" : "scenes"}</p><h2 id="editor-title">Scene editor</h2></div>
      <button className="icon-button" disabled={working} aria-label="Close editor" onClick={safeClose}><X size={20} /></button>
    </header>
    <form className="editor-form" onSubmit={(event) => { event.preventDefault(); save(); }}>
      <div className="editor-body">
        <fieldset disabled={working} className="editor-details">
          <label className="editor-wide">Title<input required maxLength={100} value={draft.title} onChange={(event) => update({ title: event.target.value })} /></label>
          <label>Format<select value={draft.format} onChange={(event) => update({ format: event.target.value as Draft["format"] })}>
            <option value="16:9">Landscape (16:9)</option><option value="9:16">Portrait (9:16)</option></select></label>
          <label>Target duration (seconds)<input type="number" min={60} max={720} required value={draft.duration_seconds || ""} onChange={(event) => update({ duration_seconds: Number(event.target.value) })} /></label>
          <label className="editor-wide">Description<textarea rows={3} maxLength={5000} value={draft.description} onChange={(event) => update({ description: event.target.value })} /></label>
          <label className="editor-wide">Thumbnail headline<input maxLength={90} value={draft.thumbnail_title ?? ""} placeholder={draft.title}
            onChange={(event) => update({ thumbnail_title: event.target.value })} /></label>
          <label className="editor-wide">Tags <small>Separated by commas</small><input value={draft.tags.join(",")}
            onChange={(event) => update({ tags: event.target.value.split(",") })} /></label>
        </fieldset>
        <fieldset disabled={working} className="editor-scenes">
          <legend>Narration and footage</legend>
          {draft.scenes.map((scene, index) => <section className="scene" key={index} aria-labelledby={`scene-${index}`}>
            <div className="scene-top">
              <h3 id={`scene-${index}`}>Scene {index + 1}</h3>
              <div className="scene-tools">
                <button type="button" className="icon-button" aria-label={`Move scene ${index + 1} up`} disabled={index === 0} onClick={() => moveScene(index, -1)}><ArrowUp size={17} /></button>
                <button type="button" className="icon-button" aria-label={`Move scene ${index + 1} down`} disabled={index === draft.scenes.length - 1} onClick={() => moveScene(index, 1)}><ArrowDown size={17} /></button>
                <button type="button" className="icon-button danger" aria-label={`Delete scene ${index + 1}`} onClick={() => updateScenes(draft.scenes.filter((_, i) => i !== index))}><Trash2 size={16} /></button>
              </div>
            </div>
            <label>Narration<textarea rows={3} required value={scene.narration} onChange={(event) => updateScene(index, { narration: event.target.value })} /></label>
            <label>Footage search terms <small>Separated by commas</small><input value={scene.search_queries.join(",")}
              onChange={(event) => updateScene(index, { search_queries: event.target.value.split(",") })} /></label>
          </section>)}
          <button type="button" className="button button-secondary" disabled={draft.scenes.length >= 60} onClick={() => updateScenes([
            ...draft.scenes, { scene_number: draft.scenes.length + 1, narration: "", search_queries: ["cinematic b-roll"] },
          ])}><Plus size={17} /> Add scene</button>
        </fieldset>
      </div>
      <footer className="dialog-footer editor-footer">
        <Notice feedback={feedback} />
        <div>
          <button type="button" className="button button-secondary" disabled={working} onClick={regenerate}>
            {busy === "regenerate" ? <LoaderCircle className="spinner" size={17} /> : <RefreshCw size={17} />} Regenerate script
          </button>
          <button className="button button-primary" disabled={working || !draft.scenes.length}>
            {busy === "save" ? <LoaderCircle className="spinner" size={17} /> : <Clapperboard size={17} />} {busy === "save" ? "Queuing…" : "Approve & render"}
          </button>
        </div>
      </footer>
    </form>
  </Modal>;
}
