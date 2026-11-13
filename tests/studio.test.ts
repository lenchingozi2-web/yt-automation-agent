import { test } from 'node:test';
import assert from 'node:assert/strict';
import { canRetryYouTubeThumbnail, durationFromBrief, normalizeVideo, normalizeDraft, youtubeStudioUrl } from '../src/lib/studio.ts';

test('saved settings beat database defaults and measured geometry describes the finished preview', () => {
  const row = { format: '16:9', duration_seconds: 180, scenes: [{ narration: 'Keep the selected settings.', _studio: {
    aspect_ratio: '9:16', duration_seconds: 60,
  } }] };
  assert.equal(normalizeDraft(row).format, '9:16');
  assert.equal(normalizeDraft(row).duration_seconds, 60);
  assert.equal(normalizeVideo({ ...row, actual_format: '16:9' }).format, '16:9');
});

test('only a confirmed upload with a saved failed thumbnail can retry', () => {
  const video = normalizeVideo({ status: 'completed', scenes: [{ narration: 'A finished video.', _studio: {
    youtube_id: 'AbCdEf12345', publish_status: 'uploaded', publish_approved_at: '2026-11-12T17:00:00Z',
    thumbnail_publish_status: 'failed', thumbnail_url: 'https://example.org/thumbnail.jpg',
  } }] });
  assert.equal(youtubeStudioUrl(video), 'https://studio.youtube.com/video/AbCdEf12345/edit');
  assert.equal(canRetryYouTubeThumbnail(video), true);
  assert.equal(canRetryYouTubeThumbnail({ ...video, publish_status: 'pending' }), false);
  assert.equal(canRetryYouTubeThumbnail({ ...video, youtube_id: 'https://example.org' }), false);
  assert.equal(canRetryYouTubeThumbnail({ ...video, thumbnail_publish_status: 'uploaded' }), false);
});

test('actual production schema retains portrait, timing, tags and publish state through API/client normalization', () => {
  const row = { id: 'video', title: 'Test', status: 'completed', scenes: [
    { narration: 'Every scene has a voice.', search_queries: ['doctor'], _studio: {
      aspect_ratio: '9:16', duration_seconds: 90, tags: ['AI'], thumbnail_title: 'A useful hook',
      video_url: 'https://x.supabase.co/storage/v1/object/public/renders/video.mp4',
      actual_duration_seconds: 88, publish_status: 'auth_required', publish_error: 'Reconnect YouTube'
    } }
  ] };
  const api = normalizeVideo(row);
  const ui = normalizeVideo(api);
  assert.equal(ui.format, '9:16');
  assert.equal(ui.duration_seconds, 90);
  assert.equal(ui.actual_duration_seconds, 88);
  assert.equal(ui.publish_status, 'auth_required');
  assert.equal(ui.publish_error, 'Reconnect YouTube');
  assert.deepEqual(ui.tags, ['AI']);
  assert.equal(ui.thumbnail_title, 'A useful hook');
  assert.ok(ui.video_url?.endsWith('/video.mp4'));
});

test('legacy voiceover alias survives normalization', () => {
  assert.equal(normalizeDraft({ scenes: [{ voiceover: 'Legacy narration.' }] }).scenes[0].narration, 'Legacy narration.');
});

test("review instructions resolve one scene and protect against a mismatched selection", async () => {
  const { revisionMode, revisionTarget } = await import("../src/lib/studio.ts");
  assert.equal(revisionTarget(undefined, "Scene 5 lacks voice", 7), 5);
  assert.equal(revisionMode(undefined, "Scene 5 lacks voice"), "audio");
  assert.equal(revisionMode(undefined, "Change scene 3 to a lab demonstration"), "visual");
  assert.equal(revisionMode(undefined, "Rewrite the claim in scene 3"), "rewrite");
  assert.equal(revisionTarget(3, "Scene 5 lacks voice", 7), undefined);
  assert.equal(revisionTarget(undefined, "Fix scene 2 and scene 3", 7), undefined);
});

test("the reported Scene 5 edit selects footage and uses the saved scene's subjects", async () => {
  const { footageSearches, revisionMode, revisionTarget } = await import("../src/lib/studio.ts");
  const direction = "Replace the footage to be more aligned with the scene/script";
  const scene = { scene_number: 5, narration: "A newborn bloodspot sample can be examined in a laboratory.", search_queries: ["doctor consultation"] };
  assert.equal(revisionTarget(5, direction, 5), 5);
  assert.equal(revisionMode(undefined, direction), "visual");
  assert.deepEqual(footageSearches(scene, direction), ["blood sample laboratory", "newborn hospital", "doctor consultation"]);
  assert.equal(footageSearches(scene, "Replace the footage with a laboratory technician")[0], "laboratory technician");
});

test("revision metadata and logical scene times survive API normalization", () => {
  const video = normalizeVideo({ id: "child", status: "completed", scenes: [{
    narration: "Start here.", _studio: { revision: { parent_id: "parent", scene_number: 1, mode: "audio", original_media: true },
      scene_timeline: [{ scene_number: 1, start: 0 }, { scene_number: 1, start: 8 }, { scene_number: 2, start: 16 }] },
  }, { narration: "Second scene." }] });
  assert.equal(video.revision?.parent_id, "parent");
  assert.deepEqual(normalizeVideo(video).scene_times, [{ scene_number: 1, start: 0 }, { scene_number: 2, start: 16 }]);
  assert.equal(video.publish_approved_at, "");
});

test("revisions share one production card and the newest playable version becomes the preview", async () => {
  const { groupProductions } = await import("../src/lib/studio.ts");
  const root = normalizeVideo({ id: "original", title: "Shared title", status: "completed", video_url: "/original.mp4", created_at: "2026-11-12T12:00:00Z" });
  const revision = normalizeVideo({ id: "second", title: root.title, status: "completed", video_url: "/second.mp4",
    revision: { parent_id: root.id, scene_number: 3, mode: "audio", original_media: true }, created_at: "2026-11-12T13:00:00Z" });
  const pending = normalizeVideo({ id: "third", title: root.title, status: "pending",
    revision: { parent_id: revision.id, scene_number: 5, mode: "visual", original_media: true }, created_at: "2026-11-12T14:00:00Z" });
  const independent = { ...root, id: "unrelated" };
  const grouped = groupProductions([root, revision, pending, independent]);
  assert.equal(grouped.length, 2);
  assert.equal(grouped[0].id, root.id);
  assert.equal(grouped[0].latest.id, pending.id);
  assert.equal(grouped[0].playable?.id, revision.id);
  assert.deepEqual(grouped[0].versions.map(({ id }) => id), ["third", "second", "original"]);
  const completed = { ...pending, status: "completed", video_url: "/third.mp4" };
  assert.equal(groupProductions([root, revision, completed])[0].playable?.id, completed.id);
  assert.equal(groupProductions([{ ...pending, revision: { ...pending.revision!, root_id: root.id } }, root])[0].id, root.id);
});

test("researched drafting revises an unsupported claim and keeps cited source records", async (context) => {
  const { researchedDraft } = await import("../src/lib/studio-research.ts");
  const original = process.env.GEMINI_API_KEY;
  process.env.GEMINI_API_KEY = "fixture";
  context.after(() => {
    if (original === undefined) delete process.env.GEMINI_API_KEY;
    else process.env.GEMINI_API_KEY = original;
  });
  const lines = [
    "Wikipedia describes retrieval practice as recalling information from memory. Try closing the notebook and explaining the idea as if a friend had asked a question.",
    "If you cannot remember a detail, keep going until you reach the end of your explanation. Open the notebook and compare your explanation with the text.",
    "Suppose you are reviewing three definitions. Write each one down from memory and mark the parts you would like to revisit when you next sit down.",
    "You can try that with one topic today. Keep the question you struggled with and use it as your starting point the next time you study.",
  ];
  const generated = { title: "Try a closed-notebook recall exercise", description: "A short example of recalling a topic from memory.",
    scenes: lines.map((narration) => ({ narration, search_queries: ["student studying", "open notebook"], source_ids: [1] })) };
  let writes = 0;
  let audits = 0;
  let referenceReads = 0;
  context.mock.method(globalThis, "fetch", async (input: string, init?: RequestInit) => {
    if (input.includes("europepmc")) return Response.json({ resultList: { result: [] } });
    if (input.includes("api.ies.ed.gov")) return Response.json({ response: { docs: [] } });
    if (input.includes("wikipedia")) {
      referenceReads++;
      return Response.json({ query: { pages: [{ pageid: 100 + referenceReads, title: "Testing effect",
        extract: "Retrieval practice is the act of trying to recall information from memory. A learner can use questions about studied material to practise retrieval." }] } });
    }
    const instruction = JSON.parse(String(init?.body)).contents[0].parts[0].text as string;
    let payload;
    if (instruction.startsWith("Plan research")) payload = { angle: "Use closed-book recall", clinical: false, queries: ["retrieval practice", "testing effect"] };
    else if (instruction.startsWith("You are an independent fact checker")) {
      audits++;
      assert.ok(instruction.includes("act of trying to recall"));
      payload = { issues: audits === 1 ? ["Scene 1 includes an unsupported success rate."] : [] };
    } else {
      writes++;
      if (writes === 2) assert.ok(instruction.includes("unsupported success rate"));
      payload = generated;
    }
    return Response.json({ candidates: [{ content: { parts: [{ text: JSON.stringify(payload) }] } }] });
  });
  const draft = await researchedDraft("Choose a fresh topic under learning and healthcare", 60, "9:16", ["Past video"]);
  assert.equal(writes, 2);
  assert.equal(audits, 2);
  assert.equal(referenceReads, 2);
  assert.deepEqual(draft.sources?.map(({ id }) => id), [1]);
});

test("a briefing draft rechecks its selected reports and keeps all exact links in the description", async (context) => {
  const { researchedDraft } = await import("../src/lib/studio-research.ts");
  const original = process.env.GEMINI_API_KEY;
  process.env.GEMINI_API_KEY = "fixture";
  context.after(() => {
    if (original === undefined) delete process.env.GEMINI_API_KEY;
    else process.env.GEMINI_API_KEY = original;
  });
  const references = ["https://pubmed.ncbi.nlm.nih.gov/901/", "https://eric.ed.gov/?id=EJ902"];
  const narration = [
    "A team writing in the Example Journal looked at a hospital's new handover process. The team wanted to see where details went missing between shifts.",
    "The case study describes how staff agreed on a simple list of items for handover. Each item was assigned a place in the conversation.",
    "Here's what that can look like in practice. Imagine handing over a task to a colleague just as the next shift walks in the door.",
    "A second paper examined checklists in another setting. The results describe each team separately, and neither paper says every hospital should follow the same steps.",
  ];
  const draftInput = { title: "The missing detail between shifts", description: "Two teams studied how staff hand over their work.",
    scenes: narration.map((line, index) => ({ narration: line, search_queries: ["hospital staff handover"], source_ids: index < 2 ? [1] : index === 3 ? [2] : [] })) };
  let lookups = 0;
  context.mock.method(globalThis, "fetch", async (input: string, init?: RequestInit) => {
    if (input.includes("europepmc")) {
      lookups++;
      assert.match(new URL(input).searchParams.get("query") || "", /EXT_ID:901/);
      return Response.json({ resultList: { result: ["901"].map((pmid) => ({ pmid, title: `Report ${pmid}`,
        journalInfo: { journal: { title: "Example Journal" } }, abstractText: "Source evidence. ".repeat(18) })) } });
    }
    if (input.includes("api.ies.ed.gov")) {
      lookups++;
      assert.match(new URL(input).searchParams.get("search") || "", /id:EJ902.*peerreviewed:T/);
      return Response.json({ response: { docs: [{ id: "EJ902", title: "ERIC study", source: "Example Education Journal",
        description: "Education evidence. ".repeat(18), publicationdateyear: 2026 }] } });
    }
    const instruction = JSON.parse(String(init?.body)).contents[0].parts[0].text as string;
    assert.ok(!instruction.startsWith("Plan research"));
    const audit = instruction.startsWith("You are an independent fact checker");
    assert.match(instruction, audit ? /voiceover must naturally name/ : /Naturally name a cited journal/);
    return Response.json({ candidates: [{ content: { parts: [{ text: JSON.stringify(audit ? { issues: [] } : draftInput) }] } }] });
  });
  const draft = await researchedDraft("Create a video from this briefing", 60, "9:16", [], {
    title: "The missing detail between shifts", hook_summary: "Two papers about handover checks", references,
  });
  assert.equal(lookups, 2);
  assert.match(draft.scenes[0].narration, /Example Journal/);
  assert.deepEqual(draft.sources?.map((source) => source.url), references);
  assert.deepEqual(draft.description.split("\n").slice(-2), references.map((url, index) => ["Example Journal", "Example Education Journal"][index] + " — " + url));
});


test("duration stated in a video brief can override the untouched default", () => {
  assert.equal(durationFromBrief("Generate a 10 minutes video on AI in education"), 600);
  assert.equal(durationFromBrief("Create a 12-minute video"), 720);
  assert.equal(durationFromBrief("Create a video about a 10-minute study break"), undefined);
});
