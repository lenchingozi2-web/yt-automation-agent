import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeVideo, normalizeDraft } from '../src/lib/studio.ts';

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
    "Try this after reading a short page of notes. Close the notebook and explain the main idea as if a friend has asked you a question.",
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
