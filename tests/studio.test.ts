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

test("revision metadata and logical scene times survive API normalization", () => {
  const video = normalizeVideo({ id: "child", status: "completed", scenes: [{
    narration: "Start here.", _studio: { revision: { parent_id: "parent", scene_number: 1, mode: "audio", original_media: true },
      scene_timeline: [{ scene_number: 1, start: 0 }, { scene_number: 1, start: 8 }, { scene_number: 2, start: 16 }] },
  }, { narration: "Second scene." }] });
  assert.equal(video.revision?.parent_id, "parent");
  assert.deepEqual(normalizeVideo(video).scene_times, [{ scene_number: 1, start: 0 }, { scene_number: 2, start: 16 }]);
  assert.equal(video.publish_approved_at, "");
});
